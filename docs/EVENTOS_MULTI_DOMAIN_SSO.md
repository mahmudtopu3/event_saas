# EventOS: Multi-Domain SSO

EventOS serves platform subdomains (`acme.event.skill.jobs`) and approved custom domains (`events.acme.com`) from one deployment. There is **one** Keycloak client, `eventos-web`, with an **exact** redirect URI per approved host. There are no wildcards and no custom domain-trust SSO.

## Domain states (`companies.Domain`)

| Field | Meaning |
|---|---|
| `is_active` (default True) | Routes to the tenant. False → **404** (never the tenant, never the public site). This is the safe revoke path. |
| `ownership_verified_at` | Required before a **custom** domain can be activated or SSO-approved |
| `tls_ready` | Required for SSO when `EVENTOS_OIDC_REQUIRE_HTTPS` (production) |
| `oidc_callback_approved` (default False) | Host may run `/auth/login` and `/auth/callback` |
| `approved_by`, `disabled_reason` | Audit context |

Every change goes through `companies.provisioning` (CLI `manage_eventos_domain`, or the public admin actions) and writes an append-only `DomainAuditEvent`. SSO approval can't be toggled as a raw admin field.

## Custom domain onboarding

1. `provision_eventos_tenant --employer-id N --domain events.acme.com --actor you`: the domain is created **inactive**.
2. Verify ownership (DNS TXT or administrative attestation): `manage_eventos_domain events.acme.com --verify-ownership dns-txt --actor you`
3. Provision TLS, then `--tls-ready`.
4. `--activate` (routing on).
5. `--approve-oidc` (SSO on in EventOS).
6. `eventos_oidc_redirect_uris`, then the Keycloak admin adds the exact URIs to `eventos-web`.
7. To revoke: `--revoke-oidc` (SSO off, routing on) or `--disable "reason"` (both off).

A hostname is never trusted just because DNS points at EventOS.

## Login binding and callback validation

`/auth/login/` (tenant host only; the public schema has no end-user login):
- Host must be a Domain of **this** tenant, active, OIDC-approved, and HTTPS + `tls_ready` in production. Otherwise: `unknown_domain` / `tenant_mismatch` / `domain_inactive` / `domain_not_approved` / `https_required`.
- Stores, per state (single use, 10-min TTL, ≤5 concurrent) in the **tenant-schema** session: nonce, PKCE verifier, redirect_uri (derived from the Domain, never from input), host, tenant, domain id, safe `next` (same-host relative path only).

`/auth/callback/` rejects: unknown or replayed state (`bad_state`), expired state, IdP error, `host_mismatch`, `tenant_mismatch`, a domain disabled or unapproved since login started, a redirect-URI/domain change (`callback_mismatch`), token-endpoint rejection (e.g. PKCE failure → `invalid_grant`), and any token-validation failure (`bad_signature`, `bad_issuer`, `bad_audience`, `expired`, `bad_nonce`, `bad_alg`, `bad_azp`, `missing_person_id`). On success the session key rotates and any legacy login in that session is dropped.

Because Django sessions live in each tenant's schema, a session or pending login from tenant A doesn't exist on tenant B. The explicit tenant checks are kept as defense in depth (and are tested).

## Cookies

`SameSite=Lax` (required: the callback is a cross-site top-level GET), `HttpOnly`, `Secure` in production. Cookies are host-only, so custom domains don't share cookies, and each tenant host signs in independently. The **Keycloak** SSO session makes the second host's login silent (verified E2E).

## Logout

`POST /auth/logout/` flushes the EventOS session, then redirects to Keycloak `end_session_endpoint` with `id_token_hint` and the host's registered post-logout URI. Other EventOS hosts notice within `KEYCLOAK_SESSION_REVALIDATE_SECONDS` (refresh fails → session ends). If Keycloak is unreachable (network error, 5xx or 429) there's a one-window grace period, then the session fails closed. Only an explicit OAuth rejection (e.g. `invalid_grant`) ends a session immediately, unless a parallel request already rotated the refresh token.
