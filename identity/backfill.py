# identity/backfill.py
"""
Legacy tenant-local user → canonical PersonID backfill.

Inputs are authoritative only:
  * a mapping file (schema_name, legacy_user_id, person_id[, source]) produced by a
    verified identity process, and/or
  * existing LegacyUserLink rows (e.g. self-service proof: the person signed in with
    both the legacy password and Keycloak in one session).

Never matches on email, username or name. Anything ambiguous or conflicting is
reported and skipped. Dry run unless apply=True.
"""
import csv
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count
from django_tenants.utils import get_public_schema_name, schema_context

from companies.models import Company


def read_mapping(path):
    rows = []
    with open(path, newline='') as fh:
        for n, raw in enumerate(csv.DictReader(fh), start=2):
            try:
                rows.append({
                    'schema_name': raw['schema_name'].strip(),
                    'legacy_user_id': int(raw['legacy_user_id']),
                    'person_id': int(raw['person_id']),
                    'source': (raw.get('source') or 'mapping_file').strip()[:64],
                    'line': n,
                })
            except (KeyError, TypeError, ValueError):
                rows.append({'invalid_line': n})
    return rows


def _empty_report():
    return {k: [] for k in ('mapped', 'already_mapped', 'unmapped', 'ambiguous', 'conflicting',
                            'unknown_user', 'invalid', 'registration_conflicts')}


def _classify(schema, rows, report):
    from .models import LegacyUserLink

    User = get_user_model()
    by_user, by_person = defaultdict(set), defaultdict(set)
    for r in rows:
        by_user[r['legacy_user_id']].add(r['person_id'])
        by_person[r['person_id']].add(r['legacy_user_id'])

    existing = {l.user_id: l.person_id for l in LegacyUserLink.objects.all()}
    linked_people = defaultdict(set)
    for uid, pid in existing.items():
        linked_people[pid].add(uid)
    user_ids = set(User.objects.values_list('pk', flat=True))

    to_link = {}
    for uid, pids in by_user.items():
        entry = {'schema': schema, 'legacy_user_id': uid}
        if uid not in user_ids:
            report['unknown_user'].append(entry)
            continue
        if len(pids) > 1:
            report['ambiguous'].append({**entry, 'why': 'user maps to several persons', 'person_ids': sorted(pids)})
            continue
        pid = next(iter(pids))
        entry['person_id'] = pid
        if len(by_person[pid]) > 1:
            report['ambiguous'].append({**entry, 'why': 'several legacy users in this tenant map to one person',
                                        'legacy_user_ids': sorted(by_person[pid])})
            continue
        if uid in existing:
            key = 'already_mapped' if existing[uid] == pid else 'conflicting'
            report[key].append({**entry, 'existing_person_id': existing[uid]} if key == 'conflicting' else entry)
            continue
        if linked_people[pid]:
            report['conflicting'].append({**entry, 'why': 'person already linked to another legacy user',
                                          'linked_user_ids': sorted(linked_people[pid])})
            continue
        report['mapped'].append(entry)
        to_link[uid] = pid

    mapped_or_linked = set(by_user) | set(existing)
    for uid in sorted(user_ids - mapped_or_linked):
        report['unmapped'].append({'schema': schema, 'legacy_user_id': uid})
    return to_link


def _apply(schema, to_link, sources, report, from_links):
    from events.models import Event, Registration
    from .models import LegacyUserLink

    with transaction.atomic():
        for uid, pid in to_link.items():
            LegacyUserLink.objects.create(user_id=uid, person_id=pid, source=sources.get(uid, 'mapping_file'))
        links = dict(LegacyUserLink.objects.values_list('user_id', 'person_id')) if from_links else to_link
        for uid, pid in links.items():
            for reg in Registration.objects.filter(user_id=uid, person_id__isnull=True):
                clash = Registration.objects.filter(event_id=reg.event_id, person_id=pid).exclude(pk=reg.pk)
                if clash.exists():
                    report['registration_conflicts'].append(
                        {'schema': schema, 'registration_id': reg.pk, 'event_id': reg.event_id, 'person_id': pid})
                    continue
                Registration.objects.filter(pk=reg.pk).update(person_id=pid)
            Event.objects.filter(created_by_id=uid, created_by_person_id__isnull=True).update(created_by_person_id=pid)


def run_backfill(*, mapping_rows=(), apply=False, from_links=False, schemas=None):
    report = _empty_report()
    rows_by_schema = defaultdict(list)
    for r in mapping_rows:
        if 'invalid_line' in r:
            report['invalid'].append({'line': r['invalid_line']})
        else:
            rows_by_schema[r['schema_name']].append(r)

    tenants = Company.objects.exclude(schema_name=get_public_schema_name())
    if schemas:
        tenants = tenants.filter(schema_name__in=schemas)
    known = set(tenants.values_list('schema_name', flat=True))
    for schema in sorted(set(rows_by_schema) - known):
        report['invalid'].append({'schema': schema, 'why': 'unknown tenant schema'})

    for schema in sorted(known):
        rows = rows_by_schema.get(schema, [])
        with schema_context(schema):
            to_link = _classify(schema, rows, report)
            if apply:
                sources = {r['legacy_user_id']: r['source'] for r in rows}
                _apply(schema, to_link, sources, report, from_links)

    report['summary'] = {k: len(v) for k, v in report.items() if isinstance(v, list)}
    report['summary']['applied'] = bool(apply)
    return report


def audit_identities():
    """Read-only counts per tenant (no emails/usernames are output)."""
    from events.models import Event, Registration
    from .models import LegacyUserLink

    User = get_user_model()
    out = []
    for schema in Company.objects.exclude(schema_name=get_public_schema_name()).order_by('schema_name') \
            .values_list('schema_name', flat=True):
        with schema_context(schema):
            dup_email_groups = (User.objects.exclude(email='').values('email').order_by()
                                .annotate(n=Count('id'))
                                .filter(n__gt=1).count())
            out.append({
                'schema': schema,
                'legacy_users': User.objects.count(),
                'legacy_users_linked': LegacyUserLink.objects.count(),
                'registrations': Registration.objects.count(),
                'registrations_with_person_id': Registration.objects.filter(person_id__isnull=False).count(),
                'registrations_legacy_only': Registration.objects.filter(person_id__isnull=True).count(),
                'events': Event.objects.count(),
                'events_with_creator_person_id': Event.objects.filter(created_by_person_id__isnull=False).count(),
                # Informational only. Shared emails are never used to merge identities.
                'duplicate_email_groups': dup_email_groups,
            })
    return out
