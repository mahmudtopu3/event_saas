# identity/tests/test_domains_provisioning.py
"""Idempotent tenant provisioning (§8), custom-domain lifecycle (§18), legacy backfill (§25)."""
import csv
import io
import json
import os
import tempfile

from django.core.management import call_command
from django.test import override_settings
from django_tenants.utils import schema_context

from companies import provisioning
from companies.models import Company, Domain, DomainAuditEvent
from events.models import Event, Registration
from identity.backfill import read_mapping, run_backfill
from identity.models import LegacyUserLink
from tests.fixtures import TENANT_A_CUSTOM_HOST, TENANT_A_HOST, TENANT_A_SCHEMA, TENANT_B_SCHEMA, TenantTestCase


@override_settings(EVENTOS_PLATFORM_BASE_DOMAIN='event.skill.jobs')
class ProvisioningTests(TenantTestCase):
    def test_provisioning_is_idempotent_and_links_canonical_employer(self):
        first = provisioning.provision_eventos_tenant(4242, requested_subdomain='acme', display_name='Acme',
                                                      actor='test')
        again = provisioning.provision_eventos_tenant(4242, requested_subdomain='acme', actor='test')
        self.assertTrue(first.created)
        self.assertFalse(again.created)
        self.assertEqual(first.company.pk, again.company.pk)
        self.assertEqual(Company.objects.filter(canonical_employer_id=4242).count(), 1)
        self.assertEqual(first.company.schema_name, 'emp_4242')
        self.assertEqual(Domain.objects.filter(domain='acme.event.skill.jobs').count(), 1)
        # Commercial state is not granted by provisioning.
        self.assertFalse(first.company.is_active_subscription)

    def test_existing_tenant_can_be_linked_then_reused(self):
        Company.objects.filter(pk=self.tenant_a.pk).update(canonical_employer_id=5151)
        result = provisioning.provision_eventos_tenant(5151, actor='test')
        self.assertFalse(result.created)
        self.assertEqual(result.company.schema_name, TENANT_A_SCHEMA)

    def test_custom_domain_starts_inactive_and_cannot_be_hijacked(self):
        Company.objects.filter(pk=self.tenant_a.pk).update(canonical_employer_id=5151)
        result = provisioning.provision_eventos_tenant(5151, requested_domain='events.acme-corp.test', actor='t')
        domain = result.domains[0]
        self.assertFalse(domain.is_active)
        self.assertFalse(domain.oidc_callback_approved)
        Company.objects.filter(pk=self.tenant_b.pk).update(canonical_employer_id=6161)
        with self.assertRaises(provisioning.ProvisioningError):
            provisioning.provision_eventos_tenant(6161, requested_domain='events.acme-corp.test', actor='t')

    def test_invalid_input_rejected(self):
        for kwargs in ({'requested_subdomain': 'bad_label!'}, {'requested_domain': 'http://x'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(provisioning.ProvisioningError):
                provisioning.provision_eventos_tenant(1, actor='t', **kwargs)


class DomainLifecycleTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.custom = Domain.objects.get(domain=TENANT_A_CUSTOM_HOST)

    def test_custom_domain_needs_verified_ownership_before_sso_or_activation(self):
        Domain.objects.filter(pk=self.custom.pk).update(is_active=False)
        self.custom.refresh_from_db()
        with self.assertRaises(provisioning.ProvisioningError):
            provisioning.activate(self.custom, 'ops')
        provisioning.verify_ownership(self.custom, 'ops', 'dns-txt')
        provisioning.activate(self.custom, 'ops')
        provisioning.approve_oidc(self.custom, 'ops')
        self.custom.refresh_from_db()
        self.assertTrue(self.custom.oidc_callback_approved)
        actions = list(DomainAuditEvent.objects.filter(domain=self.custom).values_list('action', flat=True))
        self.assertEqual(sorted(actions), ['activated', 'oidc_approved', 'ownership_verified'])

    def test_unverified_custom_domain_cannot_be_sso_approved(self):
        with self.assertRaises(provisioning.ProvisioningError):
            provisioning.approve_oidc(self.custom, 'ops')

    def test_disable_stops_routing_and_sso_and_is_audited(self):
        provisioning.verify_ownership(self.custom, 'ops', 'dns-txt')
        provisioning.approve_oidc(self.custom, 'ops')
        provisioning.disable(self.custom, 'ops', 'customer offboarded')
        self.custom.refresh_from_db()
        self.assertFalse(self.custom.is_active)
        self.assertFalse(self.custom.oidc_callback_approved)
        self.assertEqual(self.client_for(TENANT_A_CUSTOM_HOST).get('/').status_code, 404)
        self.assertTrue(DomainAuditEvent.objects.filter(domain=self.custom, action='disabled').exists())
        self.assertEqual(self.client_for(TENANT_A_HOST).get('/').status_code, 200)

    def test_redirect_uri_listing_only_includes_approved_active_domains(self):
        Domain.objects.filter(domain=TENANT_A_HOST).update(oidc_callback_approved=True)
        out = io.StringIO()
        call_command('eventos_oidc_redirect_uris', stdout=out)
        data = json.loads(out.getvalue())
        self.assertEqual(data['redirectUris'], [f'https://{TENANT_A_HOST}/auth/callback/'])
        self.assertNotIn('*', out.getvalue())

    def test_company_users_page_now_requires_platform_staff(self):
        from tests.fixtures import PUBLIC_HOST
        response = self.client_for(PUBLIC_HOST).get(f'/company/{TENANT_A_SCHEMA}/users/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])


class BackfillTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.u1 = self.make_user(TENANT_A_SCHEMA, 'u1', email='dup@x.test')
        self.u2 = self.make_user(TENANT_A_SCHEMA, 'u2', email='dup@x.test')
        self.u3 = self.make_user(TENANT_A_SCHEMA, 'u3')
        self.u4 = self.make_user(TENANT_A_SCHEMA, 'u4')
        self.u5 = self.make_user(TENANT_A_SCHEMA, 'u5')
        self.event = self.make_event(TENANT_A_SCHEMA, self.u1)
        with schema_context(TENANT_A_SCHEMA):
            self.reg1 = Registration.objects.create(event=self.event, user=self.u1)
            LegacyUserLink.objects.create(user=self.u4, person_id=444, source='earlier')
            LegacyUserLink.objects.create(user=self.u5, person_id=555, source='earlier')

    def _mapping(self, rows):
        fh = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, newline='')
        writer = csv.writer(fh)
        writer.writerow(['schema_name', 'legacy_user_id', 'person_id', 'source'])
        writer.writerows(rows)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def _rows(self):
        return self._mapping([
            (TENANT_A_SCHEMA, self.u1.pk, 111, 'idp'),     # mapped
            (TENANT_A_SCHEMA, self.u2.pk, 222, 'idp'),     # mapped (same email as u1 is irrelevant)
            (TENANT_A_SCHEMA, self.u3.pk, 333, 'idp'),     # ambiguous: u3 → two persons
            (TENANT_A_SCHEMA, self.u3.pk, 334, 'idp'),
            (TENANT_A_SCHEMA, self.u4.pk, 444, 'idp'),     # already mapped
            (TENANT_A_SCHEMA, self.u5.pk, 999, 'idp'),     # conflicting with existing link
            (TENANT_A_SCHEMA, 987654, 1, 'idp'),           # unknown user
            ('no_such_schema', 1, 1, 'idp'),               # invalid schema
            ('garbage', 'x', '', ''),                      # invalid line
        ])

    def test_dry_run_reports_and_writes_nothing(self):
        report = run_backfill(mapping_rows=read_mapping(self._rows()), schemas=[TENANT_A_SCHEMA, TENANT_B_SCHEMA])
        s = report['summary']
        self.assertEqual((s['mapped'], s['ambiguous'], s['already_mapped'], s['conflicting'], s['unknown_user']),
                         (2, 1, 1, 1, 1))
        self.assertEqual(s['invalid'], 2)
        self.assertFalse(s['applied'])
        with schema_context(TENANT_A_SCHEMA):
            self.assertEqual(LegacyUserLink.objects.count(), 2)
            self.assertIsNone(Registration.objects.get(pk=self.reg1.pk).person_id)

    def test_apply_links_and_stamps_rows_but_skips_ambiguous(self):
        report = run_backfill(mapping_rows=read_mapping(self._rows()), apply=True, schemas=[TENANT_A_SCHEMA])
        self.assertTrue(report['summary']['applied'])
        with schema_context(TENANT_A_SCHEMA):
            self.assertEqual(LegacyUserLink.objects.get(user=self.u1).person_id, 111)
            self.assertFalse(LegacyUserLink.objects.filter(user=self.u3).exists())
            self.assertEqual(LegacyUserLink.objects.get(user=self.u5).person_id, 555)   # conflict untouched
            self.assertEqual(Registration.objects.get(pk=self.reg1.pk).person_id, 111)
            self.assertEqual(Event.objects.get(pk=self.event.pk).created_by_person_id, 111)

    def test_two_users_one_person_in_same_tenant_is_ambiguous(self):
        rows = self._mapping([(TENANT_A_SCHEMA, self.u1.pk, 777, 'idp'), (TENANT_A_SCHEMA, self.u2.pk, 777, 'idp')])
        report = run_backfill(mapping_rows=read_mapping(rows), apply=True, schemas=[TENANT_A_SCHEMA])
        self.assertEqual(report['summary']['ambiguous'], 2)
        with schema_context(TENANT_A_SCHEMA):
            self.assertFalse(LegacyUserLink.objects.filter(person_id=777).exists())

    def test_registration_collision_is_reported_not_merged(self):
        with schema_context(TENANT_A_SCHEMA):
            Registration.objects.create(event=self.event, person_id=111)    # already registered via Keycloak
        rows = self._mapping([(TENANT_A_SCHEMA, self.u1.pk, 111, 'idp')])
        report = run_backfill(mapping_rows=read_mapping(rows), apply=True, schemas=[TENANT_A_SCHEMA])
        self.assertEqual(report['summary']['registration_conflicts'], 1)
        with schema_context(TENANT_A_SCHEMA):
            self.assertIsNone(Registration.objects.get(pk=self.reg1.pk).person_id)

    def test_command_is_dry_run_by_default_and_writes_json_report(self):
        report_path = tempfile.mktemp(suffix='.json')
        self.addCleanup(lambda: os.path.exists(report_path) and os.unlink(report_path))
        out = io.StringIO()
        call_command('backfill_eventos_person_ids', mapping=self._rows(), schema=[TENANT_A_SCHEMA],
                     report=report_path, stdout=out)
        self.assertIn('DRY RUN', out.getvalue())
        with open(report_path) as fh:
            self.assertEqual(json.load(fh)['summary']['mapped'], 2)
        with schema_context(TENANT_A_SCHEMA):
            self.assertFalse(LegacyUserLink.objects.filter(user=self.u1).exists())

    def test_audit_command_outputs_counts_without_personal_data(self):
        out = io.StringIO()
        call_command('audit_legacy_eventos_identities', json=True, stdout=out)
        text = out.getvalue()
        row = next(r for r in json.loads(text) if r['schema'] == TENANT_A_SCHEMA)
        self.assertEqual(row['legacy_users'], 5)
        self.assertEqual(row['duplicate_email_groups'], 1)
        self.assertNotIn('dup@x.test', text)
        self.assertNotIn('u1', text)
