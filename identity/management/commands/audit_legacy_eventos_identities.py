import json

from django.core.management.base import BaseCommand

from identity.backfill import audit_identities


class Command(BaseCommand):
    help = 'Read-only report of legacy (tenant-local) identities vs canonical PersonID coverage per tenant.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **o):
        rows = audit_identities()
        if o['json']:
            self.stdout.write(json.dumps(rows, indent=2))
            return
        for r in rows:
            self.stdout.write(
                f"{r['schema']}: users={r['legacy_users']} linked={r['legacy_users_linked']} "
                f"registrations={r['registrations']} (person_id={r['registrations_with_person_id']}, "
                f"legacy_only={r['registrations_legacy_only']}) events={r['events']} "
                f"(creator_person_id={r['events_with_creator_person_id']}) "
                f"duplicate_email_groups={r['duplicate_email_groups']}")
