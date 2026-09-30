# ADR-EVENTOS-001: EventOS joins the Skill.jobs shared identity (Keycloak + canonical Person/Employer)

- Status: Proposed (implemented behind flags on `careeros`, default off)
- Date: 2026-09-30
- Numbering: the platform cites `ADR-SJ-001…005` inline, but no ADR register exists in either repo. To avoid a conflicting `ADR-SJ` number, this uses a local `ADR-EVENTOS-` prefix. It should be registered in the platform ADR register (and renumbered there if desired).
- Related: ADR-SJ-003 (transactional outbox), cited in backendmain docs.

## Context

EventOS is a django-tenants SaaS with per-tenant-schema `auth_user`: every tenant has its own local users, passwords and profile fields. Skill.jobs is consolidating on Keycloak (realm `skilljobs`) for authentication, with one canonical Person and one canonical Employer.

## Decision

1. Keycloak authenticates. EventOS is an OIDC client (`eventos-web`) using Authorization Code + PKCE, and a resource server (`eventos-api`) for bearer tokens.
2. EventOS identifies humans only by canonical **PersonID** (bigint, the current Skill.jobs convention: `CustomUser.pk`), resolved from a verified `person_id` claim (target) or the backend's existing Keycloak link endpoint (interim). There's no local user row for Keycloak users and no local profile.
3. Profile data is read live from the Person API at render time (bulk for lists). No sync, no default cache.
4. Tenants reference the canonical Employer via `Company.canonical_employer_id`. The Company model stays as tenant infrastructure only.
5. Organizer access requires membership + entitlement + tenant link + EventOS permission, evaluated per request. Keycloak roles are never entitlements.
6. django-tenants, host routing and custom domains are retained. Each SSO-capable host is explicitly approved and registered with an exact redirect URI.
7. Migration is additive and phased via `EVENTOS_AUTH_MODE` / `EMPLOYER_AUTH_MODE`. Legacy columns and logins remain until backfill evidence justifies removal.

## Consequences

- Positive: one identity across products, immediate central revocation, no duplicated profiles, a tenant-safe SSO design, reversible rollout.
- Negative / pending: Keycloak redirect URIs must be kept in sync with approved domains. Membership, entitlement, and bulk-person APIs must be built in the Shared Core before Keycloak organizer access can be enabled (the defaults fail closed). Each tenant host keeps its own EventOS session (silent SSO keeps this seamless).

## Alternatives rejected

- Central callback host with a cross-domain session hand-off: that's a custom domain-trust SSO, which is explicitly disallowed.
- Wildcard redirect URIs: unsafe, and unsupported for hosts in Keycloak anyway.
- Shadow local `auth_user` per Keycloak user: it recreates the duplicate-identity problem.
- Matching legacy accounts by email: unsafe account takeover and merge risk.
