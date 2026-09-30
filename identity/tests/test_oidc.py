# identity/tests/test_oidc.py
"""OIDC browser flow: PKCE, state, nonce, token validation, hosts, next, session, logout, revalidation."""
import base64
import hashlib
import time

from django.contrib.auth import get_user_model
from django.test import override_settings
from django_tenants.utils import schema_context

from companies.models import Company, Domain
from identity.sessions import PENDING_KEY, TOKENS_KEY
from tests.fixtures import (
    PUBLIC_HOST, TENANT_A_CUSTOM_HOST, TENANT_A_HOST, TENANT_A_SCHEMA, TENANT_B_HOST, UNKNOWN_HOST,
)

from .base import KeycloakTestCase
from .fake_keycloak import CLIENT_ID, ISSUER


def _session(client, schema):
    with schema_context(schema):
        return client.session


class AuthorizationRequestTests(KeycloakTestCase):
    def test_login_redirects_with_code_flow_pkce_state_nonce(self):
        client = self.client_for(TENANT_A_HOST)
        params, endpoint = self.start_login(client, next_path='/my-registrations/')
        self.assertEqual(endpoint, f'{ISSUER}/protocol/openid-connect/auth')
        self.assertEqual(params['response_type'], 'code')
        self.assertEqual(params['client_id'], CLIENT_ID)
        self.assertEqual(params['code_challenge_method'], 'S256')
        self.assertEqual(params['redirect_uri'], f'http://{TENANT_A_HOST}/auth/callback/')
        self.assertGreaterEqual(len(params['state']), 32)
        self.assertGreaterEqual(len(params['nonce']), 32)

        pending = _session(client, TENANT_A_SCHEMA)[PENDING_KEY][params['state']]
        digest = base64.urlsafe_b64encode(hashlib.sha256(pending['code_verifier'].encode()).digest()).rstrip(b'=')
        self.assertEqual(digest.decode(), params['code_challenge'])
        self.assertEqual(pending['tenant'], TENANT_A_SCHEMA)
        self.assertEqual(pending['next'], '/my-registrations/')

    def test_unsafe_next_is_replaced(self):
        client = self.client_for(TENANT_A_HOST)
        for bad in ('https://evil.test/x', '//evil.test/x', 'javascript:alert(1)'):
            with self.subTest(bad=bad):
                params, _ = self.start_login(client, next_path=bad)
                self.assertEqual(_session(client, TENANT_A_SCHEMA)[PENDING_KEY][params['state']]['next'], '/')

    def test_login_disabled_in_legacy_mode(self):
        with override_settings(EVENTOS_AUTH_MODE='legacy', EMPLOYER_AUTH_MODE='legacy'):
            self.assertEqual(self.client_for(TENANT_A_HOST).get('/auth/login/').status_code, 404)

    def test_unapproved_custom_domain_cannot_start_sso(self):
        response = self.client_for(TENANT_A_CUSTOM_HOST).get('/auth/login/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context['reason'], 'domain_not_approved')

    def test_unknown_host_has_no_login_route(self):
        # Unknown hosts are served the public schema, which has no end-user auth routes at all.
        self.assertEqual(self.client_for(UNKNOWN_HOST).get('/auth/login/').status_code, 404)
        self.assertEqual(self.client_for(PUBLIC_HOST).get('/auth/login/').status_code, 404)

    def test_inactive_domain_does_not_route(self):
        Domain.objects.filter(domain=TENANT_A_HOST).update(is_active=False)
        self.assertEqual(self.client_for(TENANT_A_HOST).get('/auth/login/').status_code, 404)

    def test_https_required_in_production_mode(self):
        with override_settings(EVENTOS_OIDC_REQUIRE_HTTPS=True):
            client = self.client_for(TENANT_A_HOST)
            self.assertEqual(client.get('/auth/login/').context['reason'], 'https_required')
            self.approve(TENANT_A_HOST, tls_ready=True)
            response = client.get('/auth/login/', secure=True)
            self.assertEqual(response.status_code, 302)
            self.assertIn('redirect_uri=https%3A%2F%2Fa.eventos.test%2Fauth%2Fcallback%2F', response['Location'])

    def test_auth_routes_bypass_subscription_gate(self):
        Company.objects.filter(pk=self.tenant_a.pk).update(is_active_subscription=False)
        self.assertEqual(self.client_for(TENANT_A_HOST).get('/auth/login/').status_code, 302)


class CallbackTests(KeycloakTestCase):
    def test_successful_login_creates_principal_session_without_local_user(self):
        users_before = self._user_count()
        client = self.client_for(TENANT_A_HOST)
        response = self.sso_login(client, next_path='/my-registrations/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/my-registrations/')
        page = client.get('/my-registrations/')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.wsgi_request.principal.person_id, 100)
        self.assertEqual(self._user_count(), users_before, 'no local Django user may be created')
        token_call = self.kc.token_requests[-1]
        self.assertEqual(token_call['grant_type'], 'authorization_code')
        self.assertTrue(token_call['code_verifier'])

    def _user_count(self):
        with schema_context(TENANT_A_SCHEMA):
            return get_user_model().objects.count()

    def _callback(self, client, state, code='whatever'):
        return client.get('/auth/callback/', {'state': state, 'code': code})

    def _assert_failed(self, response, reason, client=None):
        self.assertIn(response.status_code, (400, 403))
        self.assertEqual(response.context['reason'], reason)
        if client is not None:
            self.assertEqual(client.get('/my-registrations/').status_code, 302)

    def test_unknown_state_rejected(self):
        client = self.client_for(TENANT_A_HOST)
        self.start_login(client)
        self._assert_failed(self._callback(client, 'forged-state'), 'bad_state', client)

    def test_state_is_single_use(self):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        code = self.kc.issue_code(nonce=params['nonce'])
        self.assertEqual(self._callback(client, params['state'], code).status_code, 302)
        self._assert_failed(self._callback(client, params['state'], code), 'bad_state')

    def test_expired_state_rejected(self):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        with schema_context(TENANT_A_SCHEMA):
            session = client.session
            session[PENDING_KEY][params['state']]['expires_at'] = time.time() - 1
            session.save()
        self._assert_failed(self._callback(client, params['state']), 'state_expired')

    def test_nonce_mismatch_rejected(self):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        code = self.kc.issue_code(nonce='attacker-nonce')
        self._assert_failed(self._callback(client, params['state'], code), 'bad_nonce', client)

    def _login_with_id_claims(self, **id_claims):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        code = self.kc.issue_code(nonce=params['nonce'], id_claims=id_claims)
        return client, self._callback(client, params['state'], code)

    def test_wrong_issuer_rejected(self):
        client, response = self._login_with_id_claims(iss='https://evil.test/realms/skilljobs')
        self._assert_failed(response, 'bad_issuer', client)

    def test_wrong_audience_rejected(self):
        client, response = self._login_with_id_claims(aud='some-other-client')
        self._assert_failed(response, 'bad_audience', client)

    def test_expired_id_token_rejected(self):
        client, response = self._login_with_id_claims(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)
        self._assert_failed(response, 'expired', client)

    def test_missing_person_id_rejected(self):
        client, response = self._login_with_id_claims(person_id=None)
        self._assert_failed(response, 'missing_person_id', client)

    def test_foreign_azp_rejected(self):
        client, response = self._login_with_id_claims(azp='careeros-web')
        self._assert_failed(response, 'bad_azp', client)

    def test_signature_from_unpublished_key_rejected(self):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        from .fake_keycloak import _KEYS
        forged = self.kc.mint(nonce=params['nonce'], kid='k1', key=_KEYS['rogue'])
        code = self.kc.issue_code(nonce=params['nonce'])
        self.kc.codes[code]['tokens']['id_token'] = forged
        self._assert_failed(self._callback(client, params['state'], code), 'bad_signature', client)

    def test_unsigned_or_symmetric_tokens_rejected(self):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        code = self.kc.issue_code(nonce=params['nonce'])
        self.kc.codes[code]['tokens']['id_token'] = self.kc.mint(nonce=params['nonce'], alg='HS256', key='secret')
        self._assert_failed(self._callback(client, params['state'], code), 'bad_alg', client)

    def test_pkce_verifier_is_enforced_by_token_endpoint(self):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        code = self.kc.issue_code(nonce=params['nonce'])
        self.kc.codes[code]['challenge'] = 'not-the-real-challenge'
        self._assert_failed(self._callback(client, params['state'], code), 'invalid_grant', client)

    def test_callback_on_other_host_of_same_tenant_rejected(self):
        self.approve(TENANT_A_CUSTOM_HOST)
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        client.defaults['HTTP_HOST'] = TENANT_A_CUSTOM_HOST
        self._assert_failed(self._callback(client, params['state']), 'host_mismatch')

    def test_callback_on_other_tenant_rejected(self):
        # Tenant B's session store (its own schema) has never seen tenant A's state.
        self.approve(TENANT_B_HOST)
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        client.defaults['HTTP_HOST'] = TENANT_B_HOST
        self._assert_failed(self._callback(client, params['state']), 'bad_state')

    def test_domain_revoked_mid_flow_rejected(self):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        Domain.objects.filter(domain=TENANT_A_HOST).update(oidc_callback_approved=False)
        self._assert_failed(self._callback(client, params['state']), 'domain_not_approved')

    def test_idp_error_rejected(self):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        response = client.get('/auth/callback/', {'state': params['state'], 'error': 'access_denied'})
        self._assert_failed(response, 'idp_error')

    def test_session_key_rotates_on_login(self):
        client = self.client_for(TENANT_A_HOST)
        self.start_login(client)
        before = client.cookies['sessionid'].value
        self.sso_login(client)
        self.assertNotEqual(client.cookies['sessionid'].value, before)

    def test_tokens_never_appear_in_redirects(self):
        client = self.client_for(TENANT_A_HOST)
        response = self.sso_login(client)
        tokens = _session(client, TENANT_A_SCHEMA)[TOKENS_KEY]
        for value in (tokens['id_token'], tokens['access_token']):
            self.assertNotIn(value, response['Location'])


class BackendLinkPersonResolutionTests(KeycloakTestCase):
    settings_overrides = {'KEYCLOAK_PERSON_ID_SOURCE': 'backend_link', 'KEYCLOAK_BACKEND_AUDIENCE': 'skilljobs-api'}

    def _login(self, link_person=None):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        code = self.kc.issue_code(nonce=params['nonce'], id_claims={'person_id': None},
                                  access_claims={'aud': ['skilljobs-api', 'eventos-api'], 'person_id': None})
        if link_person:
            self.kc.backend_links[self.kc.codes[code]['tokens']['access_token']] = link_person
        return client, client.get('/auth/callback/', {'state': params['state'], 'code': code})

    def test_person_id_resolved_through_canonical_backend(self):
        client, response = self._login(link_person=555)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get('/my-registrations/').wsgi_request.principal.person_id, 555)

    def test_unlinked_identity_cannot_log_in(self):
        _, response = self._login(link_person=None)
        self.assertEqual(response.context['reason'], 'not_linked')


class LogoutAndSessionTests(KeycloakTestCase):
    def test_logout_clears_session_and_redirects_to_keycloak_end_session(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        id_token = _session(client, TENANT_A_SCHEMA)[TOKENS_KEY]['id_token']
        response = client.post('/auth/logout/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(f'{ISSUER}/protocol/openid-connect/logout?'))
        self.assertIn(f'id_token_hint={id_token}', response['Location'])
        self.assertIn('post_logout_redirect_uri=http%3A%2F%2Fa.eventos.test%2F', response['Location'])
        self.assertEqual(client.get('/my-registrations/').status_code, 302)
        self.assertNotIn(TOKENS_KEY, _session(client, TENANT_A_SCHEMA).keys())

    def test_logout_requires_post(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        self.assertEqual(client.get('/auth/logout/').status_code, 405)

    def _age_session(self, client, seconds):
        with schema_context(TENANT_A_SCHEMA):
            session = client.session
            session[TOKENS_KEY]['validated_at'] = time.time() - seconds
            session.save()

    def test_sso_session_ended_elsewhere_ends_eventos_session(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        self._age_session(client, 301)
        self.kc.refresh_result = ('error', 'invalid_grant')
        self.assertEqual(client.get('/my-registrations/').status_code, 302)

    def test_refresh_success_keeps_session(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        self._age_session(client, 301)
        self.kc.refresh_result = {'access_token': self.kc.access_token(), 'id_token': self.kc.mint(),
                                  'refresh_token': 'rt-2'}
        self.assertEqual(client.get('/my-registrations/').status_code, 200)
        self.assertEqual(_session(client, TENANT_A_SCHEMA)[TOKENS_KEY]['refresh_token'], 'rt-2')

    def test_keycloak_outage_grace_then_fail_closed(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        self.kc.down = True
        self._age_session(client, 400)
        self.assertEqual(client.get('/my-registrations/').status_code, 200)   # within grace window
        self._age_session(client, 700)
        self.assertEqual(client.get('/my-registrations/').status_code, 302)   # beyond it: fail closed

    def test_switching_back_to_legacy_mode_ends_keycloak_sessions(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        with override_settings(EVENTOS_AUTH_MODE='legacy', EMPLOYER_AUTH_MODE='legacy'):
            self.assertEqual(client.get('/my-registrations/').status_code, 302)

    def test_session_is_not_valid_on_another_tenant(self):
        self.approve(TENANT_B_HOST)
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        client.defaults['HTTP_HOST'] = TENANT_B_HOST
        self.assertEqual(client.get('/my-registrations/').status_code, 302)


class JWKSRotationTests(KeycloakTestCase):
    def test_new_signing_key_is_picked_up_after_rotation(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        fetches = self.kc.jwks_fetches
        self.kc.active_kids = ['k2', 'k1']     # Keycloak rotates in a new active key
        params, _ = self.start_login(client)
        code = self.kc.issue_code(nonce=params['nonce'])
        self.kc.codes[code]['tokens']['id_token'] = self.kc.mint(nonce=params['nonce'], kid='k2')
        # Unknown kid forces one refresh, rate-limited to every 10s, so step past the limiter.
        from identity.token_validation import jwks_cache
        jwks_cache._fetched_at -= 11
        self.assertEqual(client.get('/auth/callback/', {'state': params['state'], 'code': code}).status_code, 302)
        self.assertEqual(self.kc.jwks_fetches, fetches + 1)


class StepUpTests(KeycloakTestCase):
    settings_overrides = {'EVENTOS_PRIVILEGED_ACR': 'mfa'}

    def test_step_up_login_requires_requested_acr(self):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client, step_up='1')
        self.assertEqual(params['acr_values'], 'mfa')
        self.assertEqual(params['prompt'], 'login')
        code = self.kc.issue_code(nonce=params['nonce'], id_claims={'acr': '1'})
        response = client.get('/auth/callback/', {'state': params['state'], 'code': code})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.context['reason'], 'step_up_not_satisfied')


class DefenseInDepthTests(KeycloakTestCase):
    """Layers that are redundant with schema-local sessions today, pinned so they can't silently rot."""

    def test_callback_rejects_pending_request_bound_to_other_tenant(self):
        client = self.client_for(TENANT_A_HOST)
        params, _ = self.start_login(client)
        with schema_context(TENANT_A_SCHEMA):
            session = client.session
            session[PENDING_KEY][params['state']]['tenant'] = 'tenant_b'
            session.save()
        response = client.get('/auth/callback/', {'state': params['state'], 'code': 'x'})
        self.assertEqual(response.context['reason'], 'tenant_mismatch')

    def test_middleware_drops_session_bound_to_other_tenant(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client)
        from identity.principal import SESSION_KEY
        with schema_context(TENANT_A_SCHEMA):
            session = client.session
            session[SESSION_KEY]['tenant'] = 'tenant_b'
            session.save()
        self.assertEqual(client.get('/my-registrations/').status_code, 302)

    def test_validate_jwt_requires_person_claim_when_asked(self):
        from identity.exceptions import TokenValidationError
        from identity.token_validation import validate_jwt
        with self.assertRaises(TokenValidationError) as ctx:
            validate_jwt(self.kc.access_token(person_id=None), audience='eventos-api', require_person_id=True)
        self.assertEqual(ctx.exception.code, 'missing_person_id')


class AuditLogHygieneTests(KeycloakTestCase):
    def test_login_logout_audit_contains_no_secrets(self):
        client = self.client_for(TENANT_A_HOST)
        with self.assertLogs('eventos.audit', level='INFO') as logs:
            self.sso_login(client, id_claims={'email': 'secret-person@x.test'})
            tokens = dict(_session(client, TENANT_A_SCHEMA)[TOKENS_KEY])
            client.post('/auth/logout/')
        text = '\n'.join(logs.output)
        for event in ('auth.login.started', 'auth.login.succeeded', 'auth.logout'):
            self.assertIn(event, text)
        for secret in (tokens['id_token'], tokens['access_token'], tokens['refresh_token'], 'code-1',
                       'secret-person@x.test'):
            self.assertNotIn(secret, text)
        self.assertIn('correlation_id', text)
