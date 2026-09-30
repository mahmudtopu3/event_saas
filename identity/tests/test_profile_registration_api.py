# identity/tests/test_profile_registration_api.py
"""Profile source of truth (§45), PersonID registrations (§24), bearer API (§36), auth modes (§30/§34)."""
from django.contrib.auth import get_user_model
from django.test import override_settings
from django_tenants.utils import schema_context

from companies.models import Company
from events.models import Event, Registration
from identity.employer_client import Employer
from identity.models import LegacyUserLink
from identity.profile_client import PersonSummary
from tests.fixtures import TENANT_A_HOST, TENANT_A_SCHEMA

from .base import KeycloakTestCase

EMP = 7001


class ProfileSourceOfTruthTests(KeycloakTestCase):
    def setUp(self):
        super().setUp()
        Company.objects.filter(pk=self.tenant_a.pk).update(canonical_employer_id=EMP)
        self.employers.employers = {EMP: Employer(EMP)}
        self.entitlements.set(EMP)
        self.employers.add_member(900, EMP, roles=('owner',))
        self.organizer = self.client_for(TENANT_A_HOST)
        self.sso_login(self.organizer, id_claims={'person_id': 900, 'sub': 'kc-organizer'})
        creator = self.make_user(TENANT_A_SCHEMA, 'legacy-org')
        self.event = self.make_event(TENANT_A_SCHEMA, creator)
        with schema_context(TENANT_A_SCHEMA):
            self.reg = Registration.objects.create(event=self.event, person_id=100)

    def _attendees(self):
        return self.organizer.get(f'/organizer/events/{self.event.pk}/attendees/')

    def test_canonical_name_change_shows_on_next_render_without_eventos_write(self):
        self.profiles.people[100] = PersonSummary(100, 'Old Name')
        self.assertContains(self._attendees(), 'Old Name')
        with schema_context(TENANT_A_SCHEMA):
            before = Registration.objects.values().get(pk=self.reg.pk)

        self.profiles.people[100] = PersonSummary(100, 'New Name')    # changed in CareerOS
        response = self._attendees()
        self.assertContains(response, 'New Name')
        self.assertNotContains(response, 'Old Name')
        with schema_context(TENANT_A_SCHEMA):
            self.assertEqual(Registration.objects.values().get(pk=self.reg.pk), before)

    def test_attendee_list_uses_one_bulk_lookup(self):
        with schema_context(TENANT_A_SCHEMA):
            for pid in (101, 102, 103):
                Registration.objects.create(event=self.event, person_id=pid)
        self.profiles.calls.clear()
        self._attendees()
        bulk = [c for c in self.profiles.calls if c[0] == 'resolve_people']
        single = [c for c in self.profiles.calls if c[0] == 'get_person']
        self.assertEqual(len(bulk), 1)
        self.assertEqual(sorted(bulk[0][1]), [100, 101, 102, 103])
        self.assertEqual(single, [p for p in single if p[1] == 900])    # only the navbar's own name

    def test_profile_outage_degrades_gracefully(self):
        self.profiles.fail = True
        response = self._attendees()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Person #100')
        self.assertContains(response, 'Skill.jobs member')              # navbar fallback

    def test_navbar_name_is_live(self):
        self.profiles.people[900] = PersonSummary(900, 'Org Before')
        self.assertContains(self.organizer.get('/'), 'Org Before')
        self.profiles.people[900] = PersonSummary(900, 'Org After')
        self.assertContains(self.organizer.get('/'), 'Org After')


class PersonIdRegistrationTests(KeycloakTestCase):
    def setUp(self):
        super().setUp()
        creator = self.make_user(TENANT_A_SCHEMA, 'org')
        self.event = self.make_event(TENANT_A_SCHEMA, creator)
        self.client_a = self.client_for(TENANT_A_HOST)

    def _users(self):
        with schema_context(TENANT_A_SCHEMA):
            return get_user_model().objects.count()

    def test_keycloak_registration_stores_person_id_only(self):
        users = self._users()
        self.sso_login(self.client_a, id_claims={'person_id': 100, 'name': 'Token Name', 'email': 'a@x.test'})
        self.client_a.get(f'/event/{self.event.pk}/register/')
        with schema_context(TENANT_A_SCHEMA):
            reg = Registration.objects.get(event=self.event)
            self.assertEqual((reg.person_id, reg.user_id), (100, None))
        self.assertEqual(self._users(), users, 'no local profile/user row for Keycloak users')

    def test_duplicate_keycloak_registration_prevented(self):
        self.sso_login(self.client_a, id_claims={'person_id': 100})
        self.client_a.get(f'/event/{self.event.pk}/register/')
        response = self.client_a.get(f'/event/{self.event.pk}/register/', follow=True)
        self.assertContains(response, 'already registered')
        with schema_context(TENANT_A_SCHEMA):
            self.assertEqual(Registration.objects.filter(event=self.event).count(), 1)

    def test_cancel_and_my_registrations_by_person(self):
        self.sso_login(self.client_a, id_claims={'person_id': 100})
        self.client_a.get(f'/event/{self.event.pk}/register/')
        self.assertEqual(len(self.client_a.get('/my-registrations/').context['registrations']), 1)
        self.client_a.get(f'/event/{self.event.pk}/cancel/')
        with schema_context(TENANT_A_SCHEMA):
            self.assertFalse(Registration.objects.filter(event=self.event).exists())

    def test_legacy_registrations_remain_valid_alongside(self):
        legacy = self.make_user(TENANT_A_SCHEMA, 'legacy')
        with schema_context(TENANT_A_SCHEMA):
            Registration.objects.create(event=self.event, user=legacy)
        self.login(self.client_a, TENANT_A_SCHEMA, legacy)
        self.assertEqual(len(self.client_a.get('/my-registrations/').context['registrations']), 1)

    def test_no_email_username_or_name_identity_merge(self):
        legacy = self.make_user(TENANT_A_SCHEMA, 'person100', email='same@x.test', first_name='Same')
        with schema_context(TENANT_A_SCHEMA):
            Registration.objects.create(event=self.event, user=legacy)
        self.sso_login(self.client_a, id_claims={'person_id': 100, 'email': 'same@x.test',
                                                 'preferred_username': 'person100', 'name': 'Same'})
        self.assertEqual(len(self.client_a.get('/my-registrations/').context['registrations']), 0)
        with schema_context(TENANT_A_SCHEMA):
            self.assertFalse(LegacyUserLink.objects.exists())

    def test_proof_based_link_joins_histories(self):
        legacy = self.make_user(TENANT_A_SCHEMA, 'legacy')
        with schema_context(TENANT_A_SCHEMA):
            Registration.objects.create(event=self.event, user=legacy)
        self.login(self.client_a, TENANT_A_SCHEMA, legacy)
        params, _ = self.start_login(self.client_a, link='1')
        code = self.kc.issue_code(nonce=params['nonce'], id_claims={'person_id': 100})
        self.client_a.get('/auth/callback/', {'state': params['state'], 'code': code})
        with schema_context(TENANT_A_SCHEMA):
            self.assertEqual(LegacyUserLink.objects.get(user=legacy).person_id, 100)
        # Legacy row is not mutated by linking; the backfill (--from-links) stamps person_id later.
        from identity.backfill import run_backfill
        run_backfill(apply=True, from_links=True, schemas=[TENANT_A_SCHEMA])
        self.assertEqual(len(self.client_a.get('/my-registrations/').context['registrations']), 1)

    def test_keycloak_created_event_records_creator_person_id(self):
        Company.objects.filter(pk=self.tenant_a.pk).update(canonical_employer_id=EMP)
        self.employers.employers = {EMP: Employer(EMP)}
        self.entitlements.set(EMP)
        self.employers.add_member(100, EMP, roles=('owner',))
        self.sso_login(self.client_a, id_claims={'person_id': 100})
        response = self.client_a.post('/organizer/events/new/', {
            'title': 'KC Fair', 'description': 'd', 'location': 'x',
            'start_date': '2030-01-01T10:00', 'end_date': '2030-01-01T12:00',
        })
        self.assertEqual(response.status_code, 302)
        with schema_context(TENANT_A_SCHEMA):
            event = Event.objects.get(title='KC Fair')
            self.assertEqual((event.created_by_person_id, event.created_by_id, event.status), (100, None, 'draft'))


class BearerApiTests(KeycloakTestCase):
    def setUp(self):
        super().setUp()
        creator = self.make_user(TENANT_A_SCHEMA, 'org')
        self.event = self.make_event(TENANT_A_SCHEMA, creator)
        with schema_context(TENANT_A_SCHEMA):
            Registration.objects.create(event=self.event, person_id=100)
            Registration.objects.create(event=self.make_event(TENANT_A_SCHEMA, creator, title='Other'), person_id=200)
        self.client_a = self.client_for(TENANT_A_HOST)

    def _get(self, token):
        return self.client_a.get('/api/my-registrations/', HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_valid_bearer_returns_only_own_rows(self):
        response = self._get(self.kc.access_token(person_id=100))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['event_title'] for r in response.json()], [self.event.title])

    def test_invalid_bearer_tokens_rejected(self):
        from .fake_keycloak import _KEYS
        cases = {
            'garbage': 'not-a-jwt',
            'id_token_audience': self.kc.mint(),                                  # aud=eventos-web, not eventos-api
            'expired': self.kc.access_token(iat=1, exp=2),
            'wrong_issuer': self.kc.access_token(iss='https://evil.test/realms/skilljobs'),
            'forged_signature': self.kc.access_token(key=_KEYS['rogue']),
            'missing_person': self.kc.access_token(person_id=None),
            'refresh_typ': self.kc.access_token(typ='Refresh'),
        }
        for name, token in cases.items():
            with self.subTest(name):
                self.assertEqual(self._get(token).status_code, 401)

    def test_anonymous_denied(self):
        self.assertIn(self.client_a.get('/api/my-registrations/').status_code, (401, 403))

    def test_azp_allow_list(self):
        with override_settings(KEYCLOAK_ALLOWED_AZP=['eventos-web']):
            self.assertEqual(self._get(self.kc.access_token(azp='careeros-web')).status_code, 401)
            self.assertEqual(self._get(self.kc.access_token(azp='eventos-web')).status_code, 200)

    def test_public_api_stays_public(self):
        self.assertEqual(self.client_a.get('/api/events/').status_code, 200)

    def test_legacy_mode_ignores_bearer_on_public_api(self):
        with override_settings(EVENTOS_AUTH_MODE='legacy', EMPLOYER_AUTH_MODE='legacy'):
            response = self.client_a.get('/api/events/', HTTP_AUTHORIZATION='Bearer whatever')
            self.assertEqual(response.status_code, 200)


class AuthModeTests(KeycloakTestCase):
    def test_keycloak_mode_redirects_legacy_login_and_signup(self):
        with override_settings(EVENTOS_AUTH_MODE='keycloak', EMPLOYER_AUTH_MODE='keycloak'):
            client = self.client_for(TENANT_A_HOST)
            login = client.get('/accounts/login/?next=/my-registrations/')
            self.assertEqual(login['Location'], '/auth/login/?next=%2Fmy-registrations%2F')
            signup = client.get('/accounts/signup/')
            self.assertEqual(signup['Location'], '/auth/login/?next=%2F&signup=1')
            params, _ = self.start_login(client, signup='1')
            self.assertEqual(params['prompt'], 'create')

    def test_dual_mode_keeps_legacy_login_and_shows_sso(self):
        response = self.client_for(TENANT_A_HOST).get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Continue with Skill.jobs')
        self.assertContains(response, 'name="password"')

    def test_ordinary_password_login_blocked_but_employer_fallback_kept(self):
        self.make_user(TENANT_A_SCHEMA, 'attendee', password='pw-123456!')
        self.make_user(TENANT_A_SCHEMA, 'staff', password='pw-123456!', is_staff=True)
        with override_settings(EVENTOS_AUTH_MODE='keycloak', EMPLOYER_AUTH_MODE='dual'):
            client = self.client_for(TENANT_A_HOST)
            blocked = client.post('/accounts/login/', {'username': 'attendee', 'password': 'pw-123456!'})
            self.assertEqual(blocked.status_code, 200)
            self.assertContains(blocked, 'no longer available')
            allowed = client.post('/accounts/login/', {'username': 'staff', 'password': 'pw-123456!'})
            self.assertEqual(allowed.status_code, 302)
