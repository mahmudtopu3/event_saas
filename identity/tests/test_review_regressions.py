# identity/tests/test_review_regressions.py
"""Regression tests for the confirmed findings of the independent security review."""
import time

import jwt
from django.contrib.auth import get_user_model
from django.test import override_settings
from django_tenants.utils import schema_context

from companies import provisioning
from companies.models import Company, Domain, DomainAuditEvent
from events.models import Registration
from identity import conf
from identity.models import LegacyUserLink
from identity.sessions import TOKENS_KEY
from tests.fixtures import PUBLIC_HOST, TENANT_A_CUSTOM_HOST, TENANT_A_HOST, TENANT_A_SCHEMA

from .base import KeycloakTestCase
from .fake_keycloak import FakeResponse, _KEYS


def _age(client, seconds):
    with schema_context(TENANT_A_SCHEMA):
        session = client.session
        session[TOKENS_KEY]['validated_at'] = time.time() - seconds
        session.save()


class R1TokenEndpointOutageTests(KeycloakTestCase):
    def test_5xx_from_token_endpoint_is_an_outage_not_a_revocation(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        _age(client, 301)
        original = self.kc.post
        self.kc.post = lambda url, data=None, **kw: (FakeResponse(503, {}) if data['grant_type'] == 'refresh_token'
                                                     else original(url, data=data, **kw))
        from unittest import mock
        with mock.patch('requests.post', side_effect=self.kc.post):
            self.assertEqual(client.get('/my-registrations/').status_code, 200)   # grace, not logout

    def test_concurrent_refresh_with_rotated_token_keeps_session(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        with schema_context(TENANT_A_SCHEMA):
            session = client.session
            stale = dict(session[TOKENS_KEY])
            session[TOKENS_KEY] = {**stale, 'refresh_token': 'rt-rotated-by-parallel-request',
                                   'validated_at': time.time()}
            session.save()
        # This request still carries the old refresh token in memory: simulate by aging a copy.
        from identity.middleware import PrincipalMiddleware
        request = client.get('/my-registrations/').wsgi_request
        request.session[TOKENS_KEY] = {**stale, 'validated_at': time.time() - 301}
        self.kc.refresh_result = ('error', 'invalid_grant')
        self.assertTrue(PrincipalMiddleware(lambda r: None)._still_valid(request, request.principal))


class R2LinkedLegacyHistoryTests(KeycloakTestCase):
    def test_principal_sees_linked_legacy_row_and_cannot_double_register(self):
        legacy = self.make_user(TENANT_A_SCHEMA, 'legacy')
        creator = self.make_user(TENANT_A_SCHEMA, 'org')
        event = self.make_event(TENANT_A_SCHEMA, creator, max_attendees=5)
        with schema_context(TENANT_A_SCHEMA):
            Registration.objects.create(event=event, user=legacy)
            LegacyUserLink.objects.create(user=legacy, person_id=100, source='test')
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client, id_claims={'person_id': 100})
        self.assertTrue(client.get(f'/event/{event.pk}/').context['user_registered'])
        self.assertContains(client.get(f'/event/{event.pk}/register/', follow=True), 'already registered')
        with schema_context(TENANT_A_SCHEMA):
            self.assertEqual(Registration.objects.filter(event=event).count(), 1)


class R3AlgKeyMismatchTests(KeycloakTestCase):
    def test_es256_header_with_rsa_kid_is_401_not_500(self):
        self.kc.access_token()  # warm nothing; build a token whose header lies about the alg
        from cryptography.hazmat.primitives.asymmetric import ec
        token = jwt.encode({'sub': 'x', 'iss': 'x', 'iat': 1, 'exp': 2**31}, ec.generate_private_key(ec.SECP256R1()),
                           algorithm='ES256', headers={'kid': 'k1'})
        for path in ('/api/events/', '/api/my-registrations/'):
            with self.subTest(path=path):
                response = self.client_for(TENANT_A_HOST).get(path, HTTP_AUTHORIZATION=f'Bearer {token}')
                self.assertEqual(response.status_code, 401)


class R4R5AdminTests(KeycloakTestCase):
    def test_tenant_superuser_cannot_touch_public_models_via_tenant_admin(self):
        with schema_context(TENANT_A_SCHEMA):
            boss = get_user_model().objects.create_superuser('boss', 'b@a.test', 'pw-123456!')
        client = self.client_for(TENANT_A_HOST)
        self.login(client, TENANT_A_SCHEMA, boss)
        before = Domain.objects.count()
        response = client.post('/admin/companies/domain/add/', {'domain': 'hijack.test', 'tenant': self.tenant_b.pk,
                                                                'is_primary': 'on', 'is_active': 'on'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Domain.objects.count(), before)
        for model in ('company', 'domain', 'plan', 'order'):
            with self.subTest(model=model):
                self.assertEqual(client.get(f'/admin/companies/{model}/').status_code, 403)

    def test_renaming_an_approved_domain_drops_sso_approval_and_is_audited(self):
        platform = get_user_model().objects.create_superuser('platform', 'p@x.test', 'pw-123456!')
        client = self.client_for(PUBLIC_HOST)
        client.force_login(platform)
        domain = Domain.objects.get(domain=TENANT_A_HOST)   # approved in KeycloakTestCase.setUp
        response = client.post(f'/admin/companies/domain/{domain.pk}/change/', {
            'domain': 'renamed.eventos.test', 'tenant': self.tenant_a.pk, 'is_primary': 'on', 'is_active': 'on'})
        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertFalse(domain.oidc_callback_approved)
        self.assertTrue(DomainAuditEvent.objects.filter(domain=domain, action='oidc_revoked').exists())


class R6ProvisioningTests(KeycloakTestCase):
    def test_display_name_clash_is_a_clean_error(self):
        with self.assertRaises(provisioning.ProvisioningError):
            provisioning.provision_eventos_tenant(9191, display_name='Tenant A', actor='t')


class R9LegacyCompatTests(KeycloakTestCase):
    def test_basic_auth_still_configured_and_logout_404_when_disabled(self):
        from django.conf import settings
        self.assertIn('rest_framework.authentication.BasicAuthentication',
                      settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'])
        with override_settings(EVENTOS_AUTH_MODE='legacy', EMPLOYER_AUTH_MODE='legacy'):
            self.assertEqual(self.client_for(TENANT_A_HOST).post('/auth/logout/').status_code, 404)


class R10StepUpTests(KeycloakTestCase):
    settings_overrides = {'EVENTOS_PRIVILEGED_ACR': '2'}

    def test_higher_level_satisfies_and_post_step_up_returns_to_launch_page(self):
        self.assertTrue(conf.acr_satisfies('3', '2'))
        self.assertFalse(conf.acr_satisfies('1', '2'))
        from identity.employer_client import Employer
        Company.objects.filter(pk=self.tenant_a.pk).update(canonical_employer_id=7001)
        self.employers.employers = {7001: Employer(7001)}
        self.entitlements.set(7001)
        self.employers.add_member(100, 7001, roles=('owner',))
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client, id_claims={'acr': '1'})
        creator = self.make_user(TENANT_A_SCHEMA, 'org')
        event = self.make_event(TENANT_A_SCHEMA, creator, status='draft')
        response = client.post(f'/organizer/events/{event.pk}/publish/',
                               HTTP_REFERER=f'http://{TENANT_A_HOST}/organizer/')
        self.assertEqual(response['Location'], '/auth/login/?next=%2Forganizer%2F&step_up=1')
        # A higher level than required is accepted at the callback.
        params, _ = self.start_login(client, step_up='1')
        code = self.kc.issue_code(nonce=params['nonce'], id_claims={'acr': '3'})
        self.assertEqual(client.get('/auth/callback/', {'state': params['state'], 'code': code}).status_code, 302)


class R11MalformedBackendTests(KeycloakTestCase):
    settings_overrides = {'EVENTOS_PROFILE_CLIENT': 'identity.profile_client.BackendMainProfileClient'}

    def test_malformed_profile_body_degrades_instead_of_500(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        self.kc.request = lambda *a, **k: FakeResponse(200, ['not', 'a', 'dict'])
        from unittest import mock
        with mock.patch('requests.request', side_effect=self.kc.request):
            response = client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Skill.jobs member')


class R12LinkUniquenessTests(KeycloakTestCase):
    def test_one_legacy_account_per_person_per_tenant(self):
        from django.db import IntegrityError, transaction
        u1 = self.make_user(TENANT_A_SCHEMA, 'u1')
        u2 = self.make_user(TENANT_A_SCHEMA, 'u2')
        with schema_context(TENANT_A_SCHEMA):
            LegacyUserLink.objects.create(user=u1, person_id=100, source='t')
            with self.assertRaises(IntegrityError), transaction.atomic():
                LegacyUserLink.objects.create(user=u2, person_id=100, source='t')


class R13DisabledDomainTests(KeycloakTestCase):
    def test_disabled_domain_is_404_not_public_site(self):
        Domain.objects.filter(domain=TENANT_A_CUSTOM_HOST).update(is_active=False)
        self.assertEqual(self.client_for(TENANT_A_CUSTOM_HOST).get('/admin/login/').status_code, 404)
