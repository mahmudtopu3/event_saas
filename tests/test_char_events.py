# tests/test_char_events.py
"""Characterization: event pages, registration lifecycle, legacy auth and public APIs."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from django_tenants.utils import schema_context

from events.models import Event, Registration

from .fixtures import TENANT_A_HOST, TENANT_A_SCHEMA, TenantTestCase


class EventPagesTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.creator = self.make_user(TENANT_A_SCHEMA, 'organizer', is_staff=True)
        self.client_a = self.client_for(TENANT_A_HOST)

    def test_list_shows_only_published_events_split_upcoming_past(self):
        now = timezone.now()
        self.make_event(TENANT_A_SCHEMA, self.creator, title='Upcoming Pub')
        self.make_event(TENANT_A_SCHEMA, self.creator, title='Past Pub',
                        start_date=now - timedelta(days=3), end_date=now - timedelta(days=2))
        self.make_event(TENANT_A_SCHEMA, self.creator, title='Draft One', status='draft')
        self.make_event(TENANT_A_SCHEMA, self.creator, title='Cancelled One', status='cancelled')

        response = self.client_a.get('/')
        self.assertEqual([e.title for e in response.context['upcoming_events']], ['Upcoming Pub'])
        self.assertEqual([e.title for e in response.context['past_events']], ['Past Pub'])
        self.assertNotContains(response, 'Draft One')
        self.assertNotContains(response, 'Cancelled One')

    def test_detail_published_ok_draft_404(self):
        pub = self.make_event(TENANT_A_SCHEMA, self.creator)
        draft = self.make_event(TENANT_A_SCHEMA, self.creator, status='draft')
        self.assertEqual(self.client_a.get(f'/event/{pub.pk}/').status_code, 200)
        self.assertEqual(self.client_a.get(f'/event/{draft.pk}/').status_code, 404)

    def test_registration_open_rules(self):
        now = timezone.now()
        with schema_context(TENANT_A_SCHEMA):
            ev = Event(title='x', description='x', start_date=now + timedelta(days=1),
                       end_date=now + timedelta(days=2), status='published', created_by=self.creator)
            self.assertTrue(ev.is_registration_open)
            ev.registration_deadline = now - timedelta(minutes=1)
            self.assertFalse(ev.is_registration_open)
            ev.registration_deadline = None
            ev.status = 'draft'
            self.assertFalse(ev.is_registration_open)

    def test_create_and_edit_event_through_tenant_admin_sets_created_by(self):
        with schema_context(TENANT_A_SCHEMA):
            admin_user = get_user_model().objects.create_superuser('boss', 'boss@a.test', 'pw-123456!')
        self.login(self.client_a, TENANT_A_SCHEMA, admin_user)
        now = timezone.now()
        payload = {
            'title': 'Admin Made', 'description': 'd', 'location': 'l', 'status': 'published',
            'start_date_0': (now + timedelta(days=5)).strftime('%Y-%m-%d'), 'start_date_1': '10:00:00',
            'end_date_0': (now + timedelta(days=5)).strftime('%Y-%m-%d'), 'end_date_1': '12:00:00',
            'registration_deadline_0': '', 'registration_deadline_1': '', 'max_attendees': '',
            'created_by': admin_user.pk,
        }
        response = self.client_a.post('/admin/events/event/add/', payload)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context.get('errors'))
        with schema_context(TENANT_A_SCHEMA):
            event = Event.objects.get(title='Admin Made')
            self.assertEqual(event.created_by, admin_user)

        payload['title'] = 'Admin Edited'
        response = self.client_a.post(f'/admin/events/event/{event.pk}/change/', payload)
        self.assertEqual(response.status_code, 302)
        with schema_context(TENANT_A_SCHEMA):
            self.assertEqual(Event.objects.get(pk=event.pk).title, 'Admin Edited')

    def test_non_staff_cannot_use_tenant_admin(self):
        user = self.make_user(TENANT_A_SCHEMA, 'plain')
        self.login(self.client_a, TENANT_A_SCHEMA, user)
        response = self.client_a.get('/admin/events/event/add/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])


class RegistrationLifecycleTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.creator = self.make_user(TENANT_A_SCHEMA, 'organizer')
        self.event = self.make_event(TENANT_A_SCHEMA, self.creator)
        self.attendee = self.make_user(TENANT_A_SCHEMA, 'attendee')
        self.client_a = self.client_for(TENANT_A_HOST)

    def test_register_requires_login(self):
        response = self.client_a.get(f'/event/{self.event.pk}/register/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'/accounts/login/?next=/event/{self.event.pk}/register/')

    def test_register_creates_confirmed_registration_in_tenant_schema(self):
        self.login(self.client_a, TENANT_A_SCHEMA, self.attendee)
        response = self.client_a.get(f'/event/{self.event.pk}/register/')
        self.assertRedirects(response, f'/event/{self.event.pk}/', fetch_redirect_response=False)
        with schema_context(TENANT_A_SCHEMA):
            reg = Registration.objects.get(event=self.event, user=self.attendee)
            self.assertEqual(reg.status, 'confirmed')
        from .fixtures import TENANT_B_SCHEMA
        with schema_context(TENANT_B_SCHEMA):
            self.assertEqual(Registration.objects.count(), 0)

    def test_duplicate_registration_is_prevented(self):
        self.login(self.client_a, TENANT_A_SCHEMA, self.attendee)
        self.client_a.get(f'/event/{self.event.pk}/register/')
        response = self.client_a.get(f'/event/{self.event.pk}/register/', follow=True)
        self.assertContains(response, 'already registered')
        with schema_context(TENANT_A_SCHEMA):
            self.assertEqual(Registration.objects.filter(event=self.event).count(), 1)

    def test_cancel_deletes_registration(self):
        self.login(self.client_a, TENANT_A_SCHEMA, self.attendee)
        self.client_a.get(f'/event/{self.event.pk}/register/')
        self.client_a.get(f'/event/{self.event.pk}/cancel/')
        with schema_context(TENANT_A_SCHEMA):
            self.assertFalse(Registration.objects.filter(event=self.event).exists())

    def test_cancel_without_registration_reports_error(self):
        self.login(self.client_a, TENANT_A_SCHEMA, self.attendee)
        response = self.client_a.get(f'/event/{self.event.pk}/cancel/', follow=True)
        self.assertContains(response, 'No registration found')

    def test_my_registrations_lists_only_own(self):
        other = self.make_user(TENANT_A_SCHEMA, 'other')
        second = self.make_event(TENANT_A_SCHEMA, self.creator, title='Second')
        with schema_context(TENANT_A_SCHEMA):
            Registration.objects.create(event=self.event, user=self.attendee)
            Registration.objects.create(event=second, user=other)
        self.login(self.client_a, TENANT_A_SCHEMA, self.attendee)
        response = self.client_a.get('/my-registrations/')
        self.assertEqual([r.event.title for r in response.context['registrations']], [self.event.title])

    def test_capacity_full_closes_registration(self):
        full = self.make_event(TENANT_A_SCHEMA, self.creator, title='Tiny', max_attendees=1)
        other = self.make_user(TENANT_A_SCHEMA, 'first')
        with schema_context(TENANT_A_SCHEMA):
            Registration.objects.create(event=full, user=other)
        self.login(self.client_a, TENANT_A_SCHEMA, self.attendee)
        response = self.client_a.get(f'/event/{full.pk}/register/', follow=True)
        self.assertContains(response, 'Registration is closed')


class LegacyAuthTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user(TENANT_A_SCHEMA, 'alice', password='s3cret-pass!')
        self.client_a = self.client_for(TENANT_A_HOST)

    def test_login_page_renders(self):
        response = self.client_a.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/company_login.html')

    def test_valid_login_redirects_to_safe_next(self):
        response = self.client_a.post('/accounts/login/?next=/my-registrations/',
                                      {'username': 'alice', 'password': 's3cret-pass!'})
        self.assertEqual(response['Location'], '/my-registrations/')

    def test_unsafe_next_is_ignored(self):
        response = self.client_a.post('/accounts/login/',
                                      {'username': 'alice', 'password': 's3cret-pass!', 'next': 'https://evil.test/'})
        self.assertEqual(response['Location'], '/')

    def test_invalid_login_rerenders_with_error(self):
        response = self.client_a.post('/accounts/login/', {'username': 'alice', 'password': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)

    def test_logout_post_ends_session(self):
        self.login(self.client_a, TENANT_A_SCHEMA, self.user)
        response = self.client_a.post('/accounts/logout/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client_a.get('/my-registrations/').status_code, 302)

    def test_signup_creates_tenant_local_user_and_logs_in(self):
        response = self.client_a.post('/accounts/signup/', {
            'username': 'newbie', 'first_name': 'New', 'last_name': 'Bie', 'email': 'newbie@x.test',
            'password1': 'Very-long-pass-123', 'password2': 'Very-long-pass-123',
        })
        self.assertEqual(response.status_code, 302)
        with schema_context(TENANT_A_SCHEMA):
            self.assertTrue(get_user_model().objects.filter(username='newbie').exists())
        self.assertEqual(self.client_a.get('/my-registrations/').status_code, 200)

    def test_protected_page_redirects_anonymous(self):
        response = self.client_a.get('/my-registrations/')
        self.assertEqual(response['Location'], '/accounts/login/?next=/my-registrations/')


class PublicApiTests(TenantTestCase):
    def test_events_api_is_public_and_tenant_scoped(self):
        creator = self.make_user(TENANT_A_SCHEMA, 'org')
        self.make_event(TENANT_A_SCHEMA, creator, title='API Fair')
        self.make_event(TENANT_A_SCHEMA, creator, title='Hidden', status='draft')
        data = self.client_for(TENANT_A_HOST).get('/api/events/').json()
        self.assertEqual([e['title'] for e in data], ['API Fair'])
        self.assertEqual(data[0]['registrations_count'], 0)

    def test_tenant_info_api(self):
        data = self.client_for(TENANT_A_HOST).get('/api/tenant-info/').json()
        self.assertEqual(data['schema_name'], TENANT_A_SCHEMA)
        self.assertEqual(data['name'], 'Tenant A')
