# Baseline Test Results (before identity changes)

Date: 2026-09-30 · Branch `careeros` · HEAD `8b56a69` + preserved uncommitted work.

## How to run

django-tenants requires PostgreSQL. The tests use an isolated, disposable container, never a shared or production DB:

```bash
docker run -d --name eventos-test-db -e POSTGRES_PASSWORD=eventos_test \
  -e POSTGRES_DB=eventsaas -p 127.0.0.1:55432:5432 postgres:16-alpine
DATABASE_PORT=55432 DATABASE_PASSWORD=eventos_test venv/bin/python manage.py test
```

`eventsaas.test_runner.EventOSTestRunner` creates the public tenant plus `tenant_a` (domains `a.eventos.test` and custom `events.company-a.test`) and `tenant_b` (`b.eventos.test`) once per run.

## Pre-existing suite

`manage.py test` → **Ran 0 tests** (the repository had no tests).

## Characterization suite added (`tests/test_char_tenancy.py`, `tests/test_char_events.py`)

**37 tests, all passing.**

| Area | Tests |
|---|---|
| Tenant routing | public domain, public doesn't expose tenant routes, tenant subdomain, custom domain, unknown host → public, port ignored |
| Isolation | event invisible cross-tenant, users schema-local, session not valid cross-tenant |
| Subscription | active, inactive redirect, expired `paid_until`, bypass paths, inactive page, other tenant unaffected, public never gated |
| Events | published-only list with upcoming/past split, draft 404, registration-open rules, admin create+edit sets `created_by`, non-staff blocked from admin |
| Registration | login required, create in own schema only, duplicate prevented, cancel deletes, cancel-none error, my-registrations own only, capacity closes |
| Legacy auth | login page, safe `next`, unsafe `next` ignored, invalid login, logout, signup + auto-login, protected redirect |
| API | `/api/events/` public and tenant-scoped, `/api/tenant-info/` |

## Defect found while characterizing (fixed, behaviour-preserving)

`events.views.register_for_event` caught `IntegrityError` without a savepoint. Under autocommit this happened to work, but inside any enclosing transaction (tests, `ATOMIC_REQUESTS`) the duplicate path raised `TransactionManagementError`. It's now wrapped in `transaction.atomic()`. User-visible behaviour is unchanged.

## Known issues documented, not changed by the characterization step

See `docs/CURRENT_STATE.md` §11 (S1–S9).
