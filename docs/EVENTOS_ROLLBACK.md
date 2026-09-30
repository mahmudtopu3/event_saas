# EventOS: Rollback

Ordered from least to most invasive. Every step is reversible, and no step deletes registrations.

## 1. Feature-flag rollback (seconds, no deploy of code)

```
EVENTOS_AUTH_MODE=legacy
EMPLOYER_AUTH_MODE=legacy
```
Restart. Effects: `/auth/*` returns 404, Keycloak sessions end on their next request (users see the legacy login), and bearer headers are ignored. Registrations made via Keycloak stay in the DB with `person_id` and no `user`. They aren't visible to legacy users until linked, and aren't lost.

## 2. Per-domain SSO rollback

`manage.py manage_eventos_domain <host> --revoke-oidc --actor <you>` (SSO off, routing on), or `--disable "<reason>"` (both off). Audited. Reactivate with `--activate` / `--approve-oidc`.

## 3. Organizer access rollback

Set `EVENTOS_EMPLOYER_CLIENT` / `EVENTOS_ENTITLEMENT_CLIENT` back to the `Unavailable*` defaults. All Keycloak organizer access is then denied, while legacy tenant-admin staff are unaffected.

## 4. Code rollback

Redeploy the previous commit. The new columns are nullable/defaulted, so old code runs against the new schema unchanged. Leaving migrations applied is the safe default.

## 5. Schema rollback (only if required; take a backup first)

```bash
pg_dump -Fc "$DATABASE_URL" > eventos-before-rollback.dump
python manage.py migrate_schemas identity zero            # drops links/role grants (rebuildable from mapping)
```
For `events 0002`: the legacy FKs become NOT NULL again, so first decide what to do with Keycloak-only rows:
```sql
-- per tenant schema: inspect first
SELECT count(*) FROM events_registration WHERE user_id IS NULL;
SELECT count(*) FROM events_event WHERE created_by_id IS NULL;
```
Either keep the migration applied (recommended), or reassign those rows to a designated technical user **with explicit approval**. Then run `python manage.py migrate_schemas events 0001`.
`companies 0005`: `python manage.py migrate_schemas --shared companies 0004` drops only the new columns and the audit table.

## 6. Keycloak

Remove `eventos-web` redirect URIs or disable the client. EventOS in legacy mode doesn't contact Keycloak.
