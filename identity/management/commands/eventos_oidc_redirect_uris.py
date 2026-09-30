import json

from django.core.management.base import BaseCommand

from companies.models import Domain


class Command(BaseCommand):
    help = ('Print the exact redirect / post-logout URIs and web origins that the Keycloak EventOS client '
            'must have registered (active + OIDC-approved domains only). Read-only; never changes Keycloak.')

    def add_arguments(self, parser):
        parser.add_argument('--scheme', default='https')
        parser.add_argument('--port', default='')

    def handle(self, *args, **o):
        port = f":{o['port']}" if o['port'] else ''
        hosts = (Domain.objects.filter(is_active=True, oidc_callback_approved=True)
                 .exclude(tenant__schema_name='public').order_by('domain').values_list('domain', flat=True))
        base = [f"{o['scheme']}://{h}{port}" for h in hosts]
        self.stdout.write(json.dumps({
            'redirectUris': [f'{b}/auth/callback/' for b in base],
            'post.logout.redirect.uris': [f'{b}/' for b in base],
            'webOrigins': base,
        }, indent=2))
