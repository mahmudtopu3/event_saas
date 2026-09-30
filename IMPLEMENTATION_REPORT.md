# EventOS × CareerOS Shared Identity: Implementation Report

## A. Git

| | |
|---|---|
| Branch | `careeros` (verified before any change; nothing on `main` touched, nothing merged or pushed) |
| Starting commit | `8b56a69 updated admin logic` |
| Ending commit | **Uncommitted.** All work is in the `careeros` working tree, awaiting review/commit. |
| Pre-existing uncommitted work | Preserved and built on: env-based settings, `events/forms.py` (Signup/Login forms), safe-`next` login, public-schema fallback middleware, `setup_tenant` active subscription, template error rendering, readme. |

Modified: `.env.example`, `api/{urls,views}.py`, `companies/{admin,middleware,models,views}.py`, `events/{admin,models,views}.py`, `eventsaas/{middleware,settings,tenant_urls}.py`, `templates/base.html`, `templates/registration/company_login.html` (plus the pre-existing modifications listed above).
New: `identity/` (app, 3 management commands, tests), `companies/provisioning.py`, `companies/management/commands/{provision_eventos_tenant,manage_eventos_domain}.py`, `events/{registrants,organizer,organizer_urls,domain_events}.py`, 3 migrations, `templates/identity/`, `templates/events/organizer/`, `eventsaas/test_runner.py`, `tests/`, `tools/keycloak_e2e/`, `docs/*`.

> `requirements.txt` had been gitignored since the initial commit (boilerplate next to `*.sqlite3`/`db.json`). It was never committed, although the readme depends on it, and there is no other manifest. The ignore rule was removed so it is now the tracked canonical manifest (D9). Version pinning is a follow-up.

## B. Architecture discovered

See `docs/CURRENT_STATE.md`. Key facts: django-tenants schema-per-tenant; **`auth_user` and sessions are per tenant schema** (the same human = N unrelated local accounts); events and registrations FK to the local User; organizers use only the tenant Django admin; APIs are public; subscription middleware gates tenants; custom domains are plain `Domain` rows with no lifecycle. There were no tests. Security findings S1–S9 are recorded there.

Canonical platform (backendmain, read-only audit): Keycloak realm `skilljobs` with `careeros-web`, `talentos-web`, `skilljobs-api`; **no `eventos-web` client and no `person_id` mapper**. PersonID convention = `CustomUser.pk` (bigint). `sub` → user via `KeycloakIdentity`, exposed by `POST /api/auth/keycloak/status/`. **No** Person-by-ID/bulk API, **no** employer-membership/RecruiterID model, **no** EVENTOS entitlement, **no** S2S auth, and the employer *is* a company `CustomUser`.

## C. Changes implemented

1. **Characterization tests first** (37). They pinned current behaviour and exposed a latent transaction bug in registration, fixed without changing behaviour.
2. **`identity` package**: config and rollout flags (`conf`), OIDC client with discovery and PKCE (`oidc`), signature-verified JWT validation with a JWKS rotation cache (`token_validation`), `Principal` + non-persistent `PrincipalUser`, tenant-bound server-side sessions, login/callback/logout views, `PrincipalMiddleware` (tenant binding, refresh-based revalidation, fail-closed), DRF `KeycloakBearerAuthentication`, host/domain SSO eligibility (`hosts`), PersonID resolution (claim / backend link), profile/employer/entitlement clients (interface + in-memory + fail-closed default + backendmain adapter where real endpoints exist), `EventOSAccessService`, decorators (`principal_required`, `organizer_required`, `strong_auth_required`), audit logging with correlation IDs and secret scrubbing, backfill/audit tooling.
3. **Events**: identity-agnostic registration ownership (`registrants.owner_q`), PersonID writes, a minimal **organizer workspace** (`/organizer/`: list, create draft, edit, publish (step-up capable), and attendees with bulk profile resolution), and a domain-event envelope.
4. **Companies**: canonical linkage fields, idempotent `provision_eventos_tenant`, audited domain lifecycle (verify / TLS / activate / approve-revoke OIDC / disable) via CLI and admin actions, inactive domains stop routing, `/auth/` bypasses the subscription gate.
5. **Security fixes** directly relevant to auth: the unauthenticated tenant-user PII page (`/company/<schema>/users/`) now requires platform staff; the open redirect in `subscription_check` is fixed; no secret/DB-password fallbacks when `DEBUG=False`; CORS is explicit-origins in production (never allow-all with credentials); secure/HttpOnly/SameSite=Lax cookies, CSRF trusted origins, proxy SSL header and HSTS via env; the insecure key value is removed from `.env.example`.

## D. Models changed

`Company` (+`canonical_employer_id` unique, +`canonical_tenant_id`), `Domain` (+`is_active`, `ownership_verified_at`, `tls_ready`, `oidc_callback_approved`, `approved_by`, `disabled_reason`), new `DomainAuditEvent`; `Event` (+`created_by_person_id`, `created_by` nullable), `Registration` (+`person_id`, `registration_snapshot`, `snapshot_version`, `user` nullable, partial unique `(event, person_id)`, check "has identity"); new `identity.TenantRoleAssignment`, `identity.LegacyUserLink`.

## E. Migrations

`companies/0005_canonical_linkage_and_domain_lifecycle`, `events/0002_person_id_additive`, `identity/0001_initial`. All additive. Each file's header documents purpose, impact, rollback and tests. None were run against any real/shared database. They were applied only to disposable test databases.

## E2. Independent security review

13 findings. 11 were confirmed defects and fixed, each with a regression test (mutation-checked). They are listed by the review's numbering.

| # | Finding | Status |
|---|---|---|
| 1 | Token-endpoint 5xx/429 logged users out; concurrent refresh with rotation killed valid sessions | Fixed |
| 2 | Keycloak login ignored linked legacy registrations → duplicates | Fixed |
| 3 | `alg`/key mismatch in a bearer token → 500 on every /api/ call | Fixed |
| 4 | Tenant superusers could add/edit public Company/Domain/Plan/Order via tenant admin (pre-existing) | Fixed |
| 5 | Renaming/re-pointing a domain kept SSO approval; `is_active` changes unaudited | Fixed |
| 6 | Provisioning name clash → crash; domain attach race | Fixed |
| 7 | JWKS fetch with no failure backoff, lock held during HTTP | **Deferred** (resilience improvement) |
| 8 | Staff password login at `/admin/login/` in keycloak mode | By design (break-glass); password reset in keycloak mode deferred |
| 9 | DRF Basic auth dropped in legacy mode; `/auth/logout/` not 404 in legacy | Fixed |
| 10 | Step-up redirected back to a POST-only URL; exact `acr` match locked out higher levels | Fixed |
| 11 | Malformed backend JSON → 500 on page render | Fixed |
| 12 | Legacy-link race; `person_id` not unique per tenant | Fixed (`identity/0001` regenerated; never deployed) |
| 13 | Disabled domain served the public site | Disabled domains fixed (404). Unknown-host fallback **deferred (D13)** |

The real-Keycloak E2E was rerun after the fixes: 18/18.

## F. Authentication

- **Legacy** (default): byte-for-byte current behaviour. `/auth/*` returns 404, and bearer headers are ignored.
- **Keycloak**: OIDC Code + PKCE S256, state (single-use, TTL) + nonce, bound to tenant + host + approved Domain; ID-token validation (sig/alg allow-list/iss/aud/exp/iat/nonce/azp); session rotation; refresh-token revalidation (default 300s) so logout elsewhere ends EventOS sessions; single logout via `end_session` + `id_token_hint`; step-up via `acr_values`/`EVENTOS_PRIVILEGED_ACR`; bearer API auth with audience `eventos-api` and an optional `azp` allow-list.
- **Dual**: both paths; legacy users can link accounts by proof. In `keycloak`/`dual` (C), ordinary password login and signup are off, and staff keep the fallback. Phase D is not executed.

## G. Person identity

`person_id` (bigint) is the only human reference EventOS writes for Keycloak users. No `auth_user` row, no names/emails stored. Token profile claims are ignored.

## H. Employer integration

`canonical_employer_id` on the tenant. Membership/RecruiterID via `EmployerClient` (no canonical API exists yet → default denies). Same person across employers/tenants is supported by design and tested.

## I. EventOS entitlement

`EntitlementClient` (`EVENTOS` capability), separate from Keycloak and membership. The default denies. An interim `LocalTenantSubscriptionEntitlementClient` (EventOS's own subscription as the entitlement) is available but **not enabled** (decision D5).

## J. Tenant / custom-domain behaviour

Routing unchanged for all existing domains (`is_active` defaults True). SSO requires explicit, audited approval per host; custom domains require verified ownership; production requires HTTPS + `tls_ready`. `eventos_oidc_redirect_uris` prints the exact Keycloak registrations (no wildcards).

## K. Regression results

`manage.py test`: **148 tests, all passing** (37 characterization + 99 identity + 12 review regressions) on a disposable Postgres 16. The characterization suite passed unchanged before and after the identity work. `makemigrations --check`: no changes. `check --deploy` with production env: only the HSTS subdomain/preload advisories remain (environment decisions).

## L. Tenant isolation (all passing)

Organizer in A ≠ organizer in B; B's event IDs return 404 on A; changing Host doesn't carry a session; tenant/employer query params are ignored; publishing on B is denied; an inactive subscription gates organizers; a disabled domain stops serving; bearer on A doesn't see B's rows; OIDC state from A is rejected on B; callbacks on another host of the same tenant are rejected; defense-in-depth tenant checks are pinned.

## M. OIDC security (all passing)

PKCE (challenge = S256(verifier), and the token endpoint rejects a wrong verifier), state unknown/replayed/expired, nonce, issuer, audience, signature from an unpublished key, `alg` HS256/none, expiry, missing PersonID, foreign `azp`, unsafe `next` (absolute, `//`, `javascript:`), unapproved/unknown/inactive domain, HTTPS requirement, session rotation, logout + end_session, logout requires POST, SSO revoked → session ends, outage grace then fail-closed, flag rollback ends sessions, JWKS rotation (one refresh), step-up acr, backend-link resolution, bearer valid/invalid (7 variants), audit logs free of tokens, codes and emails. Mutation checks confirmed the critical checks are actually exercised.

**Real Keycloak 26 E2E** (`tools/keycloak_e2e`, throwaway realm; the shared `skilljobs` realm was not modified): **18/18**, including silent SSO with no second password prompt, a PersonID-keyed registration with 0 local users, and single logout.

## N. Profile source of truth (all passing)

"Old Name" → canonical change → next render "New Name" with the registration row byte-identical; the attendee list makes one bulk call (no N+1); a profile outage renders fallbacks; no local user/profile is created; no email/username/name merge; proof-based link joins histories only after backfill.

## O. Remaining legacy `User` dependencies

Tenant Django admin (break-glass / legacy staff), `setup_tenant` (local tenant superuser, default password), `Event.created_by` / `Registration.user` (legacy rows), `company_users` view (now staff-only), password-reset views, `SignupForm` in legacy/dual.

## P. Remaining duplicated profile fields

Local `auth_user.first_name/last_name/email` for legacy accounts (per tenant). There are no new copies.

## Q. Remaining duplicated employer fields

`Company.name`, `contact_email`, `phone`, `logo`, `description` (classified in `docs/EVENTOS_COMPANY_INTEGRATION.md`; not removed).

## R. Required Keycloak administrator configuration

Create `eventos-web` (confidential, standard flow only, PKCE S256, exact redirect / post-logout URIs from `eventos_oidc_redirect_uris`); an `eventos-api` audience mapper; either a `person_id` user-attribute mapper (target) or a `skilljobs-api` audience (interim backend link); optionally LoA/MFA for step-up. Details: `docs/EVENTOS_KEYCLOAK_SETUP.md`.

## S. Required main Skill.jobs backend integration

1. Write a `person_id` user attribute into Keycloak on identity link (enables `claim` mode).
2. Person-by-ID and bulk-resolve APIs; employer + membership (+RecruiterID, status, roles) APIs; an EVENTOS entitlement API; a Keycloak service account (client credentials) for EventOS. Proposed contracts are in `docs/EVENTOS_COMPANY_INTEGRATION.md`.
3. TalentOS: an "Events" navigation item gated by the entitlement, linking to the tenant URL.
4. Fix `api/sso/views.py` `build_sso_user_payload`: it reads `profile.company_name`, but the field is `name`, so company display names come back blank.

## T. Open decisions

| # | Decision | Default taken |
|---|---|---|
| D1 | PersonID source: `claim` needs backendmain to write the attribute; `backend_link` works today but costs an HTTP call per login (cached 5 min for bearer) | `claim` configured; `backend_link` available |
| D2 | PersonID type is bigint per the current convention. If the Shared Core moves to UUID, a new additive column is needed | bigint |
| D3 | backendmain models company = login account (EmployerID = a user's PK). The Shared Core must separate human and employer | EventOS keeps separate fields |
| D4 | Membership API / RecruiterID contract doesn't exist | fail closed |
| D5 | Entitlement authority doesn't exist. Use EventOS subscription as interim? | fail closed (interim adapter available) |
| D6 | Organizer-grade membership roles | `owner,admin` + local `TenantRoleAssignment` |
| D7 | Keycloak redirect URI sync: manual list vs. Admin API automation | manual (read-only generator) |
| D8 | ADR numbering: no register found; used `ADR-EVENTOS-001` | local prefix |
| D9 | `requirements.txt` was gitignored | ignore rule removed; pinning is a follow-up |
| D10 | Registrations/cancels are still state-changing GETs (S5) | preserved to avoid changing UX; recommend POST |
| D11 | Production `SECRET_KEY` rotation invalidates sessions and reset links if prod used the fallback key | not rotated (no prod changes) |
| D13 | Unknown-host routing: **deferred**. Disabled known domains return 404 (fixed). Unknown hosts still use the pre-existing `SHOW_PUBLIC_IF_NO_TENANT_FOUND=True` public fallback. Changing unknown-host routing needs a separate owner decision | deferred, not changed |
| D14 | PersonID staged rollout (claim with backend_link fallback, then claim-only) needs a fallback chain; today the source is either/or | future work |
| D12 | Back-channel logout endpoint (instant cross-tenant logout) vs. refresh revalidation (≤ 5 min) | revalidation |

## U. Rollback

`docs/EVENTOS_ROLLBACK.md`: flags → per-domain revoke → client defaults → code redeploy (schema is forward-compatible) → optional schema rollback with a backup and explicit handling of Keycloak-only rows.

## V. Recommended next phase

1. Review and commit on `careeros`.
2. Staging: apply migrations, create `eventos-web` in the staging realm, run Phase B (`dual`) with `backend_link`, and collect self-service links.
3. backendmain: `person_id` attribute mapper, then membership/entitlement/person APIs; implement the `Http*Client`s and switch settings.
4. Backfill (dry-run → review → apply), then Phase C.
5. Convert register/cancel to POST; add back-channel logout if near-instant cross-host logout is required; then start EV-01/EV-02 on top of PersonID.
