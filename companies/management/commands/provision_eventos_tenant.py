from django.core.management.base import BaseCommand, CommandError

from companies.provisioning import ProvisioningError, provision_eventos_tenant


class Command(BaseCommand):
    help = 'Idempotently provision (or return) the EventOS tenant for a canonical Skill.jobs EmployerID.'

    def add_arguments(self, parser):
        parser.add_argument('--employer-id', type=int, required=True)
        parser.add_argument('--subdomain', help='Platform subdomain label, e.g. "acme" → acme.<EVENTOS_PLATFORM_BASE_DOMAIN>')
        parser.add_argument('--domain', help='Custom domain (created inactive until ownership is verified)')
        parser.add_argument('--name', help='Local display label (canonical name stays in Skill.jobs)')
        parser.add_argument('--actor', required=True, help='Who is running this (for the audit trail)')

    def handle(self, *args, **opts):
        try:
            result = provision_eventos_tenant(opts['employer_id'], requested_domain=opts['domain'],
                                              requested_subdomain=opts['subdomain'],
                                              display_name=opts['name'], actor=opts['actor'])
        except ProvisioningError as exc:
            raise CommandError(str(exc))
        verb = 'Created' if result.created else 'Existing'
        self.stdout.write(self.style.SUCCESS(f'{verb} tenant {result.company.schema_name} '
                                             f'for employer {result.company.canonical_employer_id}'))
        for d in result.domains:
            self.stdout.write(f'  {d.domain} active={d.is_active} oidc_approved={d.oidc_callback_approved}')
        self.stdout.write('Next: manage_eventos_domain … --approve-oidc, then register the callback in Keycloak '
                          '(see eventos_oidc_redirect_uris).')
