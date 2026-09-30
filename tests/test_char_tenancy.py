# tests/test_char_tenancy.py
"""Characterization: tenant routing, custom domains and subscription gating (pre-Keycloak behaviour)."""
from datetime import timedelta

from django.utils import timezone

from companies.models import Company

from .fixtures import (
    PUBLIC_HOST, TENANT_A_CUSTOM_HOST, TENANT_A_HOST, TENANT_A_SCHEMA,
    TENANT_B_HOST, TENANT_B_SCHEMA, UNKNOWN_HOST, TenantTestCase,
)


class TenantRoutingTests(TenantTestCase):
    def test_public_domain_serves_portfolio_homepage(self):
        response = self.client_for(PUBLIC_HOST).get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'companies/public_homepage.html')

    def test_public_domain_does_not_expose_tenant_routes(self):
        self.assertEqual(self.client_for(PUBLIC_HOST).get('/my-registrations/').status_code, 404)

    def test_tenant_subdomain_serves_tenant_event_list(self):
        response = self.client_for(TENANT_A_HOST).get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/event_list.html')
        self.assertEqual(response.wsgi_request.tenant.schema_name, TENANT_A_SCHEMA)

    def test_custom_domain_routes_to_owning_tenant(self):
        response = self.client_for(TENANT_A_CUSTOM_HOST).get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.tenant.schema_name, TENANT_A_SCHEMA)

    def test_unknown_domain_falls_back_to_public_schema(self):
        # SHOW_PUBLIC_IF_NO_TENANT_FOUND = True: unknown hosts get the public site, never a tenant.
        response = self.client_for(UNKNOWN_HOST).get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.tenant.schema_name, 'public')
        self.assertTemplateUsed(response, 'companies/public_homepage.html')

    def test_host_port_is_ignored_for_tenant_resolution(self):
        response = self.client_for(f'{TENANT_B_HOST}:8000').get('/')
        self.assertEqual(response.wsgi_request.tenant.schema_name, TENANT_B_SCHEMA)


class CrossTenantIsolationTests(TenantTestCase):
    def test_event_of_tenant_a_is_invisible_on_tenant_b(self):
        creator = self.make_user(TENANT_A_SCHEMA, 'organizer')
        event = self.make_event(TENANT_A_SCHEMA, creator, title='A-only fair')

        a = self.client_for(TENANT_A_HOST)
        b = self.client_for(TENANT_B_HOST)
        self.assertContains(a.get('/'), 'A-only fair')
        self.assertNotContains(b.get('/'), 'A-only fair')
        self.assertEqual(a.get(f'/event/{event.pk}/').status_code, 200)
        self.assertEqual(b.get(f'/event/{event.pk}/').status_code, 404)

    def test_users_are_schema_local(self):
        # Today auth_user lives in every tenant schema: the same username is two unrelated accounts.
        self.make_user(TENANT_A_SCHEMA, 'sam')
        self.make_user(TENANT_B_SCHEMA, 'sam')

    def test_session_from_tenant_a_is_not_valid_on_tenant_b(self):
        user = self.make_user(TENANT_A_SCHEMA, 'alice')
        client = self.client_for(TENANT_A_HOST)
        self.login(client, TENANT_A_SCHEMA, user)
        self.assertEqual(client.get('/my-registrations/').status_code, 200)

        client.defaults['HTTP_HOST'] = TENANT_B_HOST
        response = client.get('/my-registrations/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])


class SubscriptionMiddlewareTests(TenantTestCase):
    def _set(self, **fields):
        Company.objects.filter(pk=self.tenant_a.pk).update(**fields)

    def test_active_subscription_serves_site(self):
        self.assertEqual(self.client_for(TENANT_A_HOST).get('/').status_code, 200)

    def test_inactive_subscription_redirects_to_subscription_check(self):
        self._set(is_active_subscription=False)
        response = self.client_for(TENANT_A_HOST).get('/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/subscription-check/?next=/')

    def test_expired_paid_until_redirects(self):
        self._set(paid_until=timezone.now().date() - timedelta(days=1))
        response = self.client_for(TENANT_A_HOST).get('/api/events/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/subscription-check/'))

    def test_inactive_tenant_still_reaches_bypassed_paths(self):
        self._set(is_active_subscription=False)
        client = self.client_for(TENANT_A_HOST)
        for path in ('/accounts/login/', '/plans/', '/subscription-check/'):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

    def test_subscription_check_page_renders_for_inactive_tenant(self):
        self._set(is_active_subscription=False)
        response = self.client_for(TENANT_A_HOST).get('/subscription-check/?next=/')
        self.assertTemplateUsed(response, 'subscriptions/inactive.html')

    def test_other_tenant_unaffected_by_inactive_tenant(self):
        self._set(is_active_subscription=False)
        self.assertEqual(self.client_for(TENANT_B_HOST).get('/').status_code, 200)

    def test_public_schema_is_never_subscription_gated(self):
        self.assertEqual(self.client_for(PUBLIC_HOST).get('/').status_code, 200)
