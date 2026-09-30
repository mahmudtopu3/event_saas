# EventOS: Company / Employer Integration

## Concepts (kept separate)

| Concept | Owner | EventOS interface |
|---|---|---|
| Human identity | Keycloak (`sub`) → Skill.jobs PersonID | `Principal.person_id` |
| Company | Skill.jobs Employer (EmployerID) | `Company.canonical_employer_id` |
| Person ↔ company | Canonical employer membership (+ RecruiterID) | `identity.employer_client.EmployerClient` |
| "May this company use EventOS?" | Entitlement authority (`EVENTOS`) | `identity.entitlement_client.EntitlementClient` |
| "May this person organize here?" | EventOS | `identity.authorization.EventOSAccessService` |

A company is **never** a Keycloak user in EventOS, and EmployerID is never used as a human identity. **Discovered caveat:** in today's backendmain, a "company" login *is* a `CustomUser` (group `company`), and `CompanyProfile.user` is that account. So for legacy company accounts, PersonID and EmployerID can be the same number. EventOS keeps them in separate fields and never conflates them, and the canonical fix belongs to backendmain (open decision D3).

## Organizer access decision

`can_use_eventos(employer_id, person_id, tenant)` is allowed only if all of these hold:
1. `person_id` authenticated
2. tenant is not public and `tenant.canonical_employer_id` is set
3. `employer_id == tenant.canonical_employer_id` (the tenant determines the employer; `?employer_id=` is ignored)
4. employer exists and `status == active`
5. `EVENTOS` entitlement `active`
6. membership of person in employer `active`
7. an EventOS permission: a membership role in `EVENTOS_ORGANIZER_MEMBERSHIP_ROLES` (default `owner,admin`), or an active `TenantRoleAssignment` in the tenant

It's evaluated on every organizer request. Nothing is cached in the session, so a central revocation applies on the next request. Upstream errors → deny (`upstream_unavailable:*`). Every denial writes a `tenant.access.denied` audit line with the reason.

## Launch flow (TalentOS → EventOS)

```
Recruiter in TalentOS (Keycloak session) → employer EMP123, EVENTOS active → "Events" nav item
→ link to EventOS tenant URL for EMP123 (e.g. https://emp123.event.skill.jobs/organizer/)
→ EventOS /auth/login → Keycloak (existing SSO session: no password prompt) → /auth/callback
→ EventOS validates PersonID + membership + entitlement + tenant → organizer workspace
```

What TalentOS needs: the tenant URL for an employer. Proposed contract: EventOS exposes a read-only, service-authenticated `GET /internal/tenants/by-employer/<id>` later. For now it's the primary Domain of the Company with that `canonical_employer_id`. Any query-string context is only a hint; EventOS re-verifies everything server-side. If the entitlement is disabled, EventOS returns 403 (`entitlement_inactive`), and TalentOS should hide the item or show an upgrade state (commercial policy stays in TalentOS).

## Provisioning

`companies.provisioning.provision_eventos_tenant(canonical_employer_id, requested_domain=None, requested_subdomain=None)`:
- Idempotent: keyed by unique `canonical_employer_id`, deterministic schema `emp_<id>`, and a concurrent create resolves to the winner.
- Stores only tenant infrastructure and a local display label. It doesn't grant a subscription or entitlement. Custom domains start inactive.
- CLI: `manage.py provision_eventos_tenant --employer-id … [--subdomain …] [--domain …] --actor …`.
- To link an existing tenant, set `canonical_employer_id` in the public admin ("Skill.jobs linkage"). Provisioning then returns it.

## Company field classification

| Field | Class |
|---|---|
| `id`, `schema_name`, `domains` (Domain rows), `auto_create_schema`, `auto_drop_schema` | 1. Tenant infrastructure |
| `logo`, `description` (as event-site branding) | 2. EventOS configuration |
| `current_plan`, `subscription_start`, `paid_until`, `on_trial`, `is_active_subscription`, `Order`, `Plan` | 3. Subscription data (EventOS-owned commercial state, and the interim EVENTOS entitlement source) |
| `name`, `contact_email`, `phone` | 4. Duplicated employer information |
| `name`, `contact_email`, `phone`, `logo` | 5. Canonicalize later (read from the Employer API once available). **Not removed in this release.** |
| `canonical_employer_id`, `canonical_tenant_id` | Canonical linkage (new) |

## Canonical contracts: what exists vs. what's needed

| Need | Exists in backendmain? | EventOS today |
|---|---|---|
| sub → PersonID | Yes, via `POST /api/auth/keycloak/status/` (user token) | `KEYCLOAK_PERSON_ID_SOURCE=backend_link` |
| Current user's profile | Yes, `GET /api/auth/sso/me/` (user token) | `BackendMainProfileClient.get_me` |
| Person by ID (S2S) | **No** | `contract_missing` → neutral fallback label |
| Bulk resolve | **No** | `contract_missing` → "Person #id" |
| Employer by ID | **No** (`CompanyProfile` exists, but no API) | `UnavailableEmployerClient` (deny) |
| Person ↔ employer membership, RecruiterID | **No** (only Department/Section sub-users under one company) | `UnavailableEmployerClient` (deny) |
| EVENTOS entitlement | **No** | `UnavailableEntitlementClient` (deny), or interim `LocalTenantSubscriptionEntitlementClient` |
| Service-to-service auth | **No** (only a legacy-migration token) | `EVENTOS_SERVICE_TOKEN` header slot |

### Desired contracts (proposal for the Shared Core)

```
GET  /api/v1/people/{person_id}                 → {person_id, display_name}
POST /api/v1/people:resolve {"person_ids":[..]} → {"people":[{person_id, display_name}]}
GET  /api/v1/employers/{employer_id}            → {employer_id, status, verification_state}
GET  /api/v1/employers/{employer_id}/members/{person_id}
                                                → {person_id, employer_id, recruiter_id, roles:[..], status}
GET  /api/v1/employers/{employer_id}/entitlements/EVENTOS → {capability, status}
Auth: Keycloak client-credentials (service account for eventos-api), audience skilljobs-api
Headers: X-Correlation-ID, X-Purpose, X-EventOS-Tenant / X-Skilljobs-Tenant-ID
```

When these exist, implement `Http*Client` classes behind the same interfaces and switch the `EVENTOS_*_CLIENT` settings. No caller changes.
