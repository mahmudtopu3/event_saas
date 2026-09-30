# eventsaas/test_runner.py
"""
Test runner that provisions the public schema and a fixed set of tenant schemas
once per test run.

Creating a django-tenants schema runs every tenant migration, which is far too
slow to repeat per test class. The fixture tenants are committed right after the
test database is created, so ordinary ``TestCase`` classes can use them and
their per-test transactions still roll back any row-level changes.
"""
from django.test.runner import DiscoverRunner


class EventOSTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)
        from tests.fixtures import ensure_fixture_tenants

        ensure_fixture_tenants()
        return old_config
