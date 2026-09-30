# identity/tests/test_authorization_isolation.py
"""Company/EventOS authorization (§44) and tenant isolation negative tests (§43)."""
from django_tenants.utils import schema_context

from companies.models import Company, Domain
from identity.authorization import can_use_eventos
from identity.models import TenantRoleAssignment
from tests.fixtures import TENANT_A_HOST, TENANT_A_SCHEMA, TENANT_B_HOST, TENANT_B_SCHEMA

from .base import KeycloakTestCase

EMP_A, EMP_B = 7001, 7002
P1, P2 = 100, 200


class OrganizerAuthorizationTests(KeycloakTestCase):
    def setUp(self):
        super().setUp()
        Company.objects.filter(pk=self.tenant_a.pk).update(canonical_employer_id=EMP_A)
        Company.objects.filter(pk=self.tenant_b.pk).update(canonical_employer_id=EMP_B)
        self.tenant_a.refresh_from_db()
        self.tenant_b.refresh_from_db()
        from identity.employer_client import Employer
        self.employers.employers = {EMP_A: Employer(EMP_A), EMP_B: Employer(EMP_B)}
        self.entitlements.set(EMP_A)
        self.entitlements.set(EMP_B)

    def test_1_member_with_active_entitlement_gets_access(self):
        self.employers.add_member(P1, EMP_A, roles=('owner',))
        decision = can_use_eventos(EMP_A, P1, self.tenant_a)
        self.assertTrue(decision.allowed, decision.reason)

    def test_2_disabled_entitlement_denied(self):
        self.employers.add_member(P1, EMP_A, roles=('owner',))
        self.entitlements.set(EMP_A, status='disabled')
        self.assertEqual(can_use_eventos(EMP_A, P1, self.tenant_a).reason, 'entitlement_inactive')

    def test_3_authenticated_non_member_denied(self):
        self.assertEqual(can_use_eventos(EMP_A, P1, self.tenant_a).reason, 'not_a_member')

    def test_4_member_of_a_requesting_b_denied(self):
        self.employers.add_member(P1, EMP_A, roles=('owner',))
        self.assertEqual(can_use_eventos(EMP_B, P1, self.tenant_b).reason, 'not_a_member')
        # …and naming B's employer against A's tenant is a mismatch, not access.
        self.assertEqual(can_use_eventos(EMP_B, P1, self.tenant_a).reason, 'employer_tenant_mismatch')

    def test_5_central_revocation_takes_effect_on_next_request(self):
        self.employers.add_member(P1, EMP_A, roles=('owner',))
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client, id_claims={'person_id': P1})
        self.assertEqual(client.get('/organizer/').status_code, 200)
        self.employers.add_member(P1, EMP_A, roles=('owner',), status='revoked')
        self.assertEqual(client.get('/organizer/').status_code, 403)

    def test_5b_local_role_does_not_survive_central_revocation(self):
        with schema_context(TENANT_A_SCHEMA):
            TenantRoleAssignment.objects.create(person_id=P1, role='event_admin', granted_by='test')
        self.employers.add_member(P1, EMP_A, roles=('recruiter',))
        self.assertTrue(can_use_eventos(EMP_A, P1, self.tenant_a).allowed)
        self.employers.add_member(P1, EMP_A, roles=('recruiter',), status='revoked')
        self.assertEqual(can_use_eventos(EMP_A, P1, self.tenant_a).reason, 'not_a_member')

    def test_6_same_person_two_employers(self):
        self.employers.add_member(P2, EMP_A, roles=('owner',))
        self.employers.add_member(P2, EMP_B, roles=('admin',))
        self.assertTrue(can_use_eventos(EMP_A, P2, self.tenant_a).allowed)
        self.assertTrue(can_use_eventos(EMP_B, P2, self.tenant_b).allowed)

    def test_7_employer_id_alone_never_authenticates(self):
        self.assertEqual(can_use_eventos(EMP_A, None, self.tenant_a).reason, 'unauthenticated')
        response = self.client_for(TENANT_A_HOST).get('/organizer/', {'employer_id': EMP_A})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login/', response['Location'])

    def test_generic_recruiter_member_needs_eventos_permission(self):
        self.employers.add_member(P1, EMP_A, roles=('recruiter',))
        self.assertEqual(can_use_eventos(EMP_A, P1, self.tenant_a).reason, 'no_eventos_permission')

    def test_keycloak_role_claim_is_not_an_entitlement(self):
        client = self.client_for(TENANT_A_HOST)
        self.sso_login(client, id_claims={'person_id': P1, 'realm_access': {'roles': ['company', 'recruiter']}})
        self.assertEqual(client.get('/organizer/').status_code, 403)

    def test_unlinked_tenant_denied(self):
        Company.objects.filter(pk=self.tenant_a.pk).update(canonical_employer_id=None)
        self.tenant_a.refresh_from_db()
        self.assertEqual(can_use_eventos(EMP_A, P1, self.tenant_a).reason, 'tenant_not_linked')

    def test_upstream_outage_fails_closed(self):
        self.employers.add_member(P1, EMP_A, roles=('owner',))
        self.employers.fail = True
        self.assertTrue(can_use_eventos(EMP_A, P1, self.tenant_a).reason.startswith('upstream_unavailable'))

    def test_suspended_employer_denied(self):
        from identity.employer_client import Employer
        self.employers.employers[EMP_A] = Employer(EMP_A, status='suspended')
        self.employers.add_member(P1, EMP_A, roles=('owner',))
        self.assertEqual(can_use_eventos(EMP_A, P1, self.tenant_a).reason, 'employer_inactive')

    def test_default_unconfigured_clients_deny(self):
        with self.settings(EVENTOS_EMPLOYER_CLIENT='identity.employer_client.UnavailableEmployerClient'):
            from identity.service_http import reset_clients
            reset_clients()
            self.assertIn('upstream_unavailable', can_use_eventos(EMP_A, P1, self.tenant_a).reason)


class TenantIsolationTests(KeycloakTestCase):
    """P1 is authorized (organizer) in tenant A only."""

    def setUp(self):
        super().setUp()
        Company.objects.filter(pk=self.tenant_a.pk).update(canonical_employer_id=EMP_A)
        Company.objects.filter(pk=self.tenant_b.pk).update(canonical_employer_id=EMP_B)
        from identity.employer_client import Employer
        self.employers.employers = {EMP_A: Employer(EMP_A), EMP_B: Employer(EMP_B)}
        self.entitlements.set(EMP_A)
        self.entitlements.set(EMP_B)
        self.employers.add_member(P1, EMP_A, roles=('owner',))
        self.approve(TENANT_B_HOST)
        self.creator = self.make_user(TENANT_B_SCHEMA, 'b-org')
        self.b_event = self.make_event(TENANT_B_SCHEMA, self.creator, title='B private fair', status='draft')

    def _p1_on(self, host):
        client = self.client_for(host)
        self.sso_login(client, id_claims={'person_id': P1})
        return client

    def test_organizer_in_a_is_not_organizer_in_b(self):
        self.assertEqual(self._p1_on(TENANT_A_HOST).get('/organizer/').status_code, 200)
        self.assertEqual(self._p1_on(TENANT_B_HOST).get('/organizer/').status_code, 403)

    def test_knowing_event_id_of_b_gives_nothing_on_a(self):
        client = self._p1_on(TENANT_A_HOST)
        for path in (f'/organizer/events/{self.b_event.pk}/edit/', f'/organizer/events/{self.b_event.pk}/attendees/'):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 404)

    def test_changing_host_does_not_carry_session(self):
        client = self._p1_on(TENANT_A_HOST)
        client.defaults['HTTP_HOST'] = TENANT_B_HOST
        response = client.get('/organizer/')
        self.assertEqual(response.status_code, 302)          # anonymous on B → must log in there
        self.assertIn('/auth/login/', response['Location'])

    def test_tenant_or_employer_query_parameters_are_ignored(self):
        client = self._p1_on(TENANT_B_HOST)
        for params in ({'employer_id': EMP_A}, {'tenant': TENANT_A_SCHEMA}, {'schema_name': TENANT_A_SCHEMA}):
            with self.subTest(params=params):
                self.assertEqual(client.get('/organizer/', params).status_code, 403)

    def test_publish_on_b_denied_for_a_organizer(self):
        client = self._p1_on(TENANT_B_HOST)
        self.assertEqual(client.post(f'/organizer/events/{self.b_event.pk}/publish/').status_code, 403)
        with schema_context(TENANT_B_SCHEMA):
            self.b_event.refresh_from_db()
            self.assertEqual(self.b_event.status, 'draft')

    def test_inactive_tenant_subscription_gates_organizer(self):
        client = self._p1_on(TENANT_A_HOST)
        Company.objects.filter(pk=self.tenant_a.pk).update(is_active_subscription=False)
        response = client.get('/organizer/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/subscription-check/'))

    def test_disabled_domain_stops_serving_tenant(self):
        client = self._p1_on(TENANT_A_HOST)
        Domain.objects.filter(domain=TENANT_A_HOST).update(is_active=False)
        self.assertEqual(client.get('/organizer/').status_code, 404)

    def test_bearer_token_on_other_tenant_only_sees_that_tenant(self):
        from events.models import Registration
        with schema_context(TENANT_B_SCHEMA):
            self.b_event.status = 'published'
            self.b_event.save()
            Registration.objects.create(event=self.b_event, person_id=P1)
        token = self.kc.access_token(person_id=P1)
        a = self.client_for(TENANT_A_HOST).get('/api/my-registrations/', HTTP_AUTHORIZATION=f'Bearer {token}')
        b = self.client_for(TENANT_B_HOST).get('/api/my-registrations/', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(a.json(), [])
        self.assertEqual([r['event_title'] for r in b.json()], ['B private fair'])
