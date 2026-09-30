# EventOS — Current State (audit before the CareerOS identity integration)

Branch: `careeros` · Audited HEAD: `8b56a69` (plus the uncommitted work listed in §0) · Date: 2026-09-30

## 0. Uncommitted work present at audit time (preserved)

| File | What it is |
|---|---|
| `eventsaas/settings.py` | Secrets/DB/DEBUG/ALLOWED_HOSTS read from env via `python-dotenv`; `SHOW_PUBLIC_IF_NO_TENANT_FOUND = True`; console email backend. |
| `eventsaas/middleware.py` | Fills `request.tenant` with the public tenant when the host is unknown. |
| `events/views.py`, `events/forms.py` (new) | `SignupForm` (first/last/email, per-tenant email uniqueness), `LoginForm`, `_safe_next_url()` open-redirect guard, auto-login after signup. |
| `templates/registration/{company_login,signup}.html` | Field-level errors, `next` propagation. |
| `companies/management/commands/setup_tenant.py` | New tenants start with `is_active_subscription=True`. |
| `readme.MD` | `pip install -r requirements.txt`, `.env` instructions. |

## 1. Architecture

Single Django 5.0 project (`eventsaas`), Python 3.12, PostgreSQL, **django-tenants 3.13** (schema-per-tenant), DRF, django-cors-headers, Pillow. Server-rendered Bootstrap templates plus a standalone static demo (`frontend/index.html`) that calls `/api/tenant-info/` and `/api/events/`.

Apps: `companies` (shared/public: tenants, domains, plans, orders, subscription middleware), `events` (tenant: Event, Registration), `api` (not an installed app; plain function views mounted in the tenant URLconf).

No Celery, no signals, no background jobs, no payment gateway, no outbound HTTP integrations. Email: console backend only (password-reset views exist via `django.contrib.auth.urls`).

## 2. Tenant architecture

- `TENANT_MODEL = companies.Company` (`TenantMixin`, `auto_create_schema=True`, **`auto_drop_schema=True`**, so deleting a Company drops its schema and all its data).
- `TENANT_DOMAIN_MODEL = companies.Domain` (`DomainMixin`: `domain` unique, `tenant` FK, `is_primary`). No extra fields: no active flag, no verification, no TLS/OIDC state.
- Middleware order: `TenantMainMiddleware` → `TenantURLConfMiddleware` → `SubscriptionMiddleware` → CORS → Security → Session → Common → CSRF → Auth → Messages → Clickjacking.
- Host → exact `Domain.domain` match (port and leading `www.` stripped). Unknown host → **public schema** (`SHOW_PUBLIC_IF_NO_TENANT_FOUND`).
- URLconfs: public = `eventsaas.urls`; tenant = `eventsaas.tenant_urls`.
- **SHARED_APPS and TENANT_APPS both contain `django.contrib.auth`, `sessions`, `admin`, `messages`, `contenttypes`.** Every tenant schema has its own `auth_user`, `django_session`, `django_admin_log`.

## 3. Authentication architecture

- Django `ModelBackend`, username + password, **per tenant schema**. The same human registered on two tenants is two unrelated `auth_user` rows with independent passwords. Sessions are per schema too, so a session cookie from tenant A doesn't authenticate on tenant B (pinned by a test).
- Routes (tenant): `/accounts/login/` → `events.views.company_login` (custom, safe-`next`), `/accounts/signup/` → `events.views.signup`, the rest of `django.contrib.auth.urls` (logout via POST, password change/reset).
- Public schema: only `/admin/` login (platform superusers in the public `auth_user`). Note: the public models' admins are also mounted on tenant hosts; before this work, a tenant superuser could add/edit public rows there (fixed: denied outside the public schema).
- Tenant staff: `setup_tenant` creates a **superuser inside the tenant schema** (default password `admin123`). Tenant admins manage events through the tenant's `/admin/`.
- DRF: no `REST_FRAMEWORK` settings, so the defaults apply (Session + Basic authentication, `AllowAny`).
- No SSO, OIDC, JWT, or external identity of any kind.

## 4. Django `User` dependencies

| Location | Dependency |
|---|---|
| `events.models.Event.created_by` | `FK(User, CASCADE)`, **non-null** |
| `events.models.Registration.user` | `FK(User, CASCADE)`, `unique_together(event, user)` |
| `events.admin` | `save_model` sets `created_by = request.user`; list/search on `user__username`, `user__email` |
| `events.views` | `request.user` for registration lookups/creation, `@login_required` ×3, `login()` in signup/login |
| `events.forms` | `UserCreationForm`/`AuthenticationForm`; stores first/last/email on the local user |
| `companies.views.company_users` | Lists **all** users (username, email, names, staff flags, last login) of any tenant schema |
| `companies.views.tenant_order`/`view_orders` | `@login_required` (any tenant user, not just admins) |
| `companies.views.subscription_check` | `request.user.is_authenticated` |
| `setup_tenant` command | `create_superuser` in the tenant schema |
| Templates | `user.username`, `user.is_staff`, `user.is_authenticated` in `base.html`, event detail, company users |

Profile data (first/last name, email) is stored **locally per tenant**, which duplicates identity.

## 5. Company / Domain

`Company` fields, classified in `docs/EVENTOS_COMPANY_INTEGRATION.md`: `schema_name`, `name` (unique), `description`, `contact_email`, `logo`, `phone`, `subscription_start`, `paid_until`, `on_trial`, `is_active_subscription`, `current_plan`, `created_at`, `updated_at`.
Provisioning: `manage.py setup_tenant "<name>" <domain>` (non-idempotent: it errors if the schema/domain exists) and Django admin in the public schema. Also `setup_public` for the public tenant/domain.

## 6. Event lifecycle

Events are created and edited **only through the tenant Django admin** (no organizer UI). Status: `draft` / `published` / `cancelled`. Only `published` events appear in the list, detail and API. `is_registration_open` = published ∧ before deadline ∧ capacity not reached. `start_date`/`end_date` are timezone-aware (UTC storage, `TIME_ZONE='UTC'`).

## 7. Registration lifecycle

- `GET /event/<pk>/register/` (**state change on GET, no CSRF**) creates a `confirmed` Registration. Duplicates are rejected by `unique_together(event,user)` + `IntegrityError` → message.
- `GET /event/<pk>/cancel/` **deletes** the row (the `cancelled` status is never used).
- Capacity counts all registrations regardless of status. `waitlist` is unused.
- `/my-registrations/` lists the current user's registrations.

## 8. API

Tenant-only, **public, unauthenticated**: `GET /api/tenant-info/`, `GET /api/events/` (N+1 `registrations.count()`). There are no protected API endpoints.

## 9. Subscriptions

`companies.middleware.SubscriptionMiddleware` (tenant requests only): if `not is_active_subscription or paid_until < today` → `302 /subscription-check/?next=<path>`. Bypass prefixes: `/accounts/`, `/login/`, `/logout/`, `/subscription-check/`, `/plans/`, `/order/`, `/orders/`, `/static/`, `/media/`, `/favicon.ico`. Orders are placed by tenant users and approved in the public admin, which extends `paid_until` by 30 or 365 days. There's no payment integration.

## 10. Custom domains

A custom domain is just another `Domain` row pointing at the tenant (created in the public admin). There is no ownership verification, active/disabled state, TLS readiness, audit trail, or approval workflow. `ALLOWED_HOSTS` defaults to `*`, so any Host header reaches the app and unknown hosts get the public site.

## 11. Security-sensitive settings / findings

| # | Finding | Severity |
|---|---|---|
| S1 | `SECRET_KEY` has a hard-coded insecure fallback; `DEBUG` defaults to True; `DATABASE_PASSWORD` fallback `admin123` | High (prod) |
| S2 | `ALLOWED_HOSTS='*'` by default | Medium |
| S3 | `CORS_ALLOW_ALL_ORIGINS=True` **with** `CORS_ALLOW_CREDENTIALS=True` | High |
| S4 | `/company/<schema>/users/` on the public domain is **unauthenticated** and leaks every tenant user's email, name and staff flags | **High (PII)** |
| S5 | Register and cancel are state-changing GETs (CSRF-able) | Medium |
| S6 | `setup_tenant` default admin password `admin123`, echoed to stdout | Medium |
| S7 | No secure-cookie / HSTS / proxy-SSL / CSRF_TRUSTED_ORIGINS settings | Medium (prod) |
| S8 | `subscription_check` redirects to an unvalidated `next` (open redirect) | Medium |
| S9 | `auto_drop_schema=True`: deleting a Company in admin irreversibly drops tenant data | Operational |

## 12. Existing tests

None (`events/tests.py`, `companies/tests.py` were empty stubs). See `docs/BASELINE_TEST_RESULTS.md` for the characterization suite added in this work.

## 13. Migration history

`companies`: 0001 initial → 0002 logo → 0003 `is_active_subscription`, `updated_at` → 0004 Plan, Order, `subscription_start`, `current_plan`. `events`: 0001 initial. All additive. No data migrations.

## 14. Known risks for the identity migration

1. Per-schema `auth_user` means that mapping a legacy user to a PersonID must be done **per tenant schema**. Two tenants can hold two different legacy users who are the same human, and must never be auto-merged by email or username.
2. `Event.created_by` is non-null. Keycloak-created events need either a nullable legacy FK or a local technical user. Chosen approach: make it nullable (additive, reversible) and add `created_by_person_id`.
3. `unique_together(event,user)` can't cover Keycloak registrations. A partial unique constraint on `(event, person_id) WHERE person_id IS NOT NULL` is additive and safe.
4. Tenant admin, `company_users` and `setup_tenant` all depend on local users. Keep them as break-glass technical admin during Phases A–C.
5. The subscription middleware must not trap `/auth/*`.

## 15. Behaviour that must stay unchanged

Everything pinned in `tests/test_char_*.py`: public/tenant/custom-domain routing, unknown host → public, cross-tenant isolation, subscription gating and bypasses, event list/detail/status rules, admin create/edit with `created_by`, register/duplicate/cancel/my-registrations/capacity, legacy login/logout/signup/safe `next`, public APIs.
