from django.core.management.base import BaseCommand, CommandError

from companies import provisioning
from companies.models import Domain


class Command(BaseCommand):
    help = 'Audited custom-domain lifecycle: verify ownership, TLS, activate, approve/revoke OIDC, disable.'

    def add_arguments(self, parser):
        parser.add_argument('domain')
        parser.add_argument('--actor', required=True)
        parser.add_argument('--verify-ownership', metavar='METHOD', help='e.g. dns-txt, admin-attestation')
        parser.add_argument('--tls-ready', action='store_true')
        parser.add_argument('--activate', action='store_true')
        parser.add_argument('--approve-oidc', action='store_true')
        parser.add_argument('--revoke-oidc', action='store_true')
        parser.add_argument('--disable', metavar='REASON')

    def handle(self, *args, **o):
        domain = Domain.objects.select_related('tenant').filter(domain=o['domain'].lower()).first()
        if domain is None:
            raise CommandError('unknown domain')
        actor = o['actor']
        try:
            if o['verify_ownership']:
                provisioning.verify_ownership(domain, actor, o['verify_ownership'])
            if o['tls_ready']:
                provisioning.mark_tls_ready(domain, actor)
            if o['activate']:
                provisioning.activate(domain, actor)
            if o['approve_oidc']:
                provisioning.approve_oidc(domain, actor)
            if o['revoke_oidc']:
                provisioning.revoke_oidc(domain, actor)
            if o['disable']:
                provisioning.disable(domain, actor, o['disable'])
        except provisioning.ProvisioningError as exc:
            raise CommandError(str(exc))
        domain.refresh_from_db()
        self.stdout.write(f'{domain.domain}: active={domain.is_active} verified={bool(domain.ownership_verified_at)} '
                          f'tls={domain.tls_ready} oidc_approved={domain.oidc_callback_approved}')
