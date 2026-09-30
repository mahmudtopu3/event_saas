import json

from django.core.management.base import BaseCommand, CommandError

from identity.backfill import read_mapping, run_backfill


class Command(BaseCommand):
    help = ('Backfill canonical PersonIDs onto legacy registrations/events from an AUTHORITATIVE mapping. '
            'Dry run by default; never matches by email, username or name.')

    def add_arguments(self, parser):
        parser.add_argument('--mapping', help='CSV with columns schema_name,legacy_user_id,person_id[,source]')
        parser.add_argument('--from-links', action='store_true',
                            help='Also backfill rows for users already linked (LegacyUserLink)')
        parser.add_argument('--schema', action='append', help='Limit to tenant schema(s)')
        parser.add_argument('--apply', action='store_true', help='Write changes (default: dry run)')
        parser.add_argument('--dry-run', action='store_true', help='Explicit dry run (the default)')
        parser.add_argument('--report', help='Write the full machine-readable JSON report here')

    def handle(self, *args, **o):
        if o['apply'] and o['dry_run']:
            raise CommandError('--apply and --dry-run are mutually exclusive')
        if not o['mapping'] and not o['from_links']:
            raise CommandError('provide --mapping and/or --from-links')
        rows = read_mapping(o['mapping']) if o['mapping'] else []
        report = run_backfill(mapping_rows=rows, apply=o['apply'], from_links=o['from_links'], schemas=o['schema'])
        if o['report']:
            with open(o['report'], 'w') as fh:
                json.dump(report, fh, indent=2, sort_keys=True)
        mode = 'APPLIED' if o['apply'] else 'DRY RUN (no changes written)'
        self.stdout.write(f'{mode}: ' + json.dumps(report['summary'], sort_keys=True))
