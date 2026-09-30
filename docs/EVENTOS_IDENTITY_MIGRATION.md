# EventOS: Identity Migration Runbook

Sequence: **ADD → BACKFILL → VERIFY → SWITCH READS → SWITCH WRITES → OBSERVE → DEPRECATE → REMOVE LATER.** This release completes ADD and provides the BACKFILL/VERIFY tooling. Nothing destructive is included.

## What changed in the schema (all additive)

| Table | Change |
|---|---|
| `events_registration` (each tenant) | `+ person_id bigint NULL` (indexed), `+ registration_snapshot jsonb`, `+ snapshot_version`, `user_id` now NULL-able, `+ UNIQUE(event_id, person_id) WHERE person_id IS NOT NULL`, `+ CHECK(user_id IS NOT NULL OR person_id IS NOT NULL)`. Legacy `UNIQUE(event_id, user_id)` kept. |
| `events_event` (each tenant) | `+ created_by_person_id bigint NULL`, `created_by_id` now NULL-able |
| `identity_legacyuserlink` (each tenant) | new: `user_id` (1:1) → `person_id`, `source` |
| `identity_tenantroleassignment` (each tenant) | new: `person_id`, `role`, grant/revoke timestamps |
| `companies_company` (public) | `+ canonical_employer_id bigint UNIQUE NULL`, `+ canonical_tenant_id bigint NULL` |
| `companies_domain` (public) | `+ is_active` (default TRUE), `ownership_verified_at`, `tls_ready`, `oidc_callback_approved` (default FALSE), `approved_by`, `disabled_reason` |
| `companies_domainauditevent` (public) | new, append-only |

Apply (staging first, **never automatically in production**; take a backup first):
```bash
pg_dump -Fc "$DATABASE_URL" > eventos-pre-identity.dump
python manage.py migrate_schemas --shared      # public: companies
python manage.py migrate_schemas               # every tenant: events, identity
```

## Phases

| Phase | Flags | Behaviour |
|---|---|---|
| **A: Legacy** (default) | `EVENTOS_AUTH_MODE=legacy`, `EMPLOYER_AUTH_MODE=legacy` | Identical to today. `/auth/*` returns 404, and bearer headers are ignored. |
| **B: Dual** | `dual` / `dual` | "Continue with Skill.jobs" plus the legacy form and legacy signup. Legacy users can **link** their account (proof of both credentials in one session → `LegacyUserLink(source=self_service_proof)`). |
| **C: Keycloak default** | `keycloak` / `dual` | Ordinary users: `/accounts/signup/` → central account creation (`prompt=create`), and password login is refused for non-staff. Staff keep a password fallback. |
| **D: Keycloak only** | `keycloak` / `keycloak` | `/accounts/login/` → `/auth/login/`. Tenant `/admin/` remains break-glass only. **Not executed by this release.** |

Rolling a flag back ends existing Keycloak sessions on the next request (legacy mode drops principals) and changes no data.

## Backfill (BACKFILL / VERIFY)

1. Inventory (read-only, no personal data printed):
   `python manage.py audit_legacy_eventos_identities --json`
2. Obtain an **authoritative** mapping `schema_name,legacy_user_id,person_id,source`. EventOS legacy users live in EventOS's own per-tenant `auth_user`, so no backend table maps them. Acceptable sources:
   - self-service proof links collected in Phase B (already in `identity_legacyuserlink`);
   - a verified migration process run by the identity team (e.g. the user proves the legacy password, then Keycloak links it).
   **Email, username or name matching isn't acceptable**, and the tooling has no such mode.
3. Dry run (default): `python manage.py backfill_eventos_person_ids --mapping map.csv --from-links --report report.json`
   The report lists `mapped`, `already_mapped`, `unmapped`, `ambiguous` (a user → several people, or several users in a tenant → one person), `conflicting` (an existing link disagrees), `unknown_user`, `invalid`, and `registration_conflicts` (the person already has a Keycloak registration for that event). Nothing ambiguous is merged.
4. Review the report, then `--apply`. It creates links, sets `Registration.person_id` and `Event.created_by_person_id` where they're null, and skips collisions.
5. Verify: rerun the audit, then check `registrations_legacy_only` and `events_with_creator_person_id` per tenant.

## Switching reads and writes

- Writes: Keycloak requests already write `person_id` (and `created_by_person_id`). Linked legacy users write both `user` and `person_id`.
- Reads: `events.registrants.owner_q` already reads by PersonID for principals and by `user OR linked person_id` for legacy users.
- **Later (separate approval):** once `registrations_legacy_only == 0` for a tenant and Phase D has been stable, make `(event, person_id)` the only uniqueness and drop `user_id` in a new migration. That isn't part of this release.

## Remaining local-user dependencies (intentional for now)

Tenant Django admin (break-glass/legacy staff), `setup_tenant` (creates a local tenant superuser), `company_users` (now platform-staff-only), `Event.created_by`/`Registration.user` (legacy rows), password reset views (legacy mode).
