# tests/fixtures.py
"""Shared tenant fixtures and helpers for the EventOS test suite."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from companies.models import Company, Domain

PUBLIC_HOST = 'public.eventos.test'
TENANT_A_SCHEMA = 'tenant_a'
TENANT_A_HOST = 'a.eventos.test'
TENANT_A_CUSTOM_HOST = 'events.company-a.test'
TENANT_B_SCHEMA = 'tenant_b'
TENANT_B_HOST = 'b.eventos.test'
UNKNOWN_HOST = 'unknown.example.test'


def ensure_fixture_tenants():
    """Create (idempotently) the public tenant plus tenants A and B with their domains."""
    connection.set_schema_to_public()
    public, _ = Company.objects.get_or_create(schema_name='public', defaults={'name': 'Public Schema'})
    Domain.objects.get_or_create(domain=PUBLIC_HOST, defaults={'tenant': public, 'is_primary': True})

    for schema, name, hosts in (
        (TENANT_A_SCHEMA, 'Tenant A', [(TENANT_A_HOST, True), (TENANT_A_CUSTOM_HOST, False)]),
        (TENANT_B_SCHEMA, 'Tenant B', [(TENANT_B_HOST, True)]),
    ):
        tenant = Company.objects.filter(schema_name=schema).first()
        if tenant is None:
            tenant = Company(
                schema_name=schema,
                name=name,
                is_active_subscription=True,
                on_trial=False,
                paid_until=None,
            )
            tenant.save(verbosity=0)
        for host, primary in hosts:
            Domain.objects.get_or_create(domain=host, defaults={'tenant': tenant, 'is_primary': primary})
    connection.set_schema_to_public()


class TenantTestCase(TestCase):
    """
    Base class for request-level tests against the fixture tenants.

    Requests pick their tenant through the Host header, exactly as in production.
    ORM work for a specific tenant must be wrapped in ``schema_context``.
    """

    def setUp(self):
        super().setUp()
        connection.set_schema_to_public()
        self.tenant_a = Company.objects.get(schema_name=TENANT_A_SCHEMA)
        self.tenant_b = Company.objects.get(schema_name=TENANT_B_SCHEMA)

    def tearDown(self):
        connection.set_schema_to_public()
        super().tearDown()

    # ── helpers ──────────────────────────────────────────────────────────
    def client_for(self, host):
        return Client(HTTP_HOST=host)

    def make_user(self, schema, username='alice', password='s3cret-pass!', **extra):
        with schema_context(schema):
            return get_user_model().objects.create_user(username=username, password=password, **extra)

    def login(self, client, schema, user):
        """Log `user` in on `client`; the session row lives in the tenant's own schema."""
        with schema_context(schema):
            client.force_login(user)

    def make_event(self, schema, creator, **overrides):
        from events.models import Event

        now = timezone.now()
        fields = {
            'title': 'Career Fair',
            'description': 'Annual fair',
            'location': 'Dhaka',
            'start_date': now + timedelta(days=7),
            'end_date': now + timedelta(days=7, hours=4),
            'status': 'published',
            'created_by': creator,
        }
        fields.update(overrides)
        with schema_context(schema):
            return Event.objects.create(**fields)
