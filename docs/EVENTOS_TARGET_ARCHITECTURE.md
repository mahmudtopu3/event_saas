# EventOS: Target Architecture (CareerOS shared platform)

```
                         KEYCLOAK  (realm: skilljobs)
                         authentication · SSO · MFA · logout
                            |
                    +-------+-------+
                    |               |
                 Person P1       Person P2              ← one canonical PersonID per human
                 Jobseeker       Recruiter                (Skill.jobs CustomUser.pk, bigint)
                    |               |
                    |          Employer Membership      ← canonical (Skill.jobs), not EventOS
                    |               |
                    |           Employer EMP1           ← canonical EmployerID (bigint)
                    |               |      + EVENTOS entitlement (commercial capability)
          +---------+---------------+---------+
          |                                   |
       CareerOS                            EventOS
    canonical profile              tenant + event workflows
                                              |
                                 +------------+------------+
                                 |                         |
                        emp1.event.skill.jobs       events.emp1.com
```

**One Person, one Employer, many authorized tenant/domain views.**

## Authorities

| Concern | Authority | EventOS stores |
|---|---|---|
| Authentication, credentials, MFA, SSO session, logout | Keycloak | Nothing but a server-side session holding tokens for refresh/logout |
| Person identity & profile | Skill.jobs Person (today `CustomUser` + profiles) | `person_id` (bigint reference) |
| Employer identity, verification | Skill.jobs Employer | `Company.canonical_employer_id` |
| Employer membership / RecruiterID | Skill.jobs (contract pending) | nothing (checked live) |
| EVENTOS commercial entitlement | Entitlement service (contract pending; interim adapter available) | nothing (checked live) |
| Tenant routing, domains, schema | EventOS (django-tenants) | `Company`, `Domain` |
| Events, registrations, event roles | EventOS | `Event`, `Registration`, `TenantRoleAssignment` |

## Request pipeline

```
CorrelationId → EventOSTenant (Host → active Domain → schema) → URLConf → Subscription gate
  → Session → CSRF → Django Auth (legacy users) → PrincipalMiddleware (Keycloak principal)
  → view → [organizer_required: membership + entitlement + tenant link + EventOS permission]
```

- **Host routes a tenant; it never identifies a person.** The same PersonID may hold sessions on many tenants (one per tenant schema), with tenant-specific authorization in each.
- **Principal** (`identity.principal.Principal`): `subject`, `person_id`, `issuer`, `tenant_schema`, `scopes`, `acr`, `amr`, `sid`. `request.user` becomes a non-persistent `PrincipalUser` adapter, so `login_required` and templates keep working. No local `auth_user` row is created.
- **Browser**: OIDC Authorization Code + PKCE (S256), with state and nonce bound to tenant + host + approved Domain. See `EVENTOS_MULTI_DOMAIN_SSO.md`.
- **API**: `Authorization: Bearer <Keycloak access token>` → same Principal (`identity.authentication.KeycloakBearerAuthentication`). Public endpoints stay public.
- **Organizer access** = PersonID + canonical membership + EVENTOS entitlement + tenant↔employer link + EventOS permission, evaluated per request, so revocation is immediate.
- **Profile rendering**: `person_id → Profile API` on each render (bulk for lists). No sync, no local profile. Default: no cache.
- **Historical data**: `Registration.registration_snapshot` holds what was submitted *for that event* and is never an editable profile.
- **Domain events**: `events.domain_events.emit()` produces canonical envelopes (log today; the transactional outbox, ADR-SJ-003, comes later).

## Same person, many companies/tenants

`P200` can be organizer on `events.company-a.com`, attendee on `fair.company-b.com` and coordinator on `jobs.university-c.edu`. That's one PersonID with three sessions (one per tenant schema) and three authorization contexts. The selected employer is never baked into the token. It's derived from the tenant being accessed, then verified against canonical membership.

## Rollout flags

`EVENTOS_AUTH_MODE` (ordinary users) and `EMPLOYER_AUTH_MODE` (staff) ∈ `legacy | dual | keycloak`, read only via `identity.conf`. Phase D (Keycloak-only for staff too, local passwords disabled) is **not** executed by this release.

## Capability roadmap hooks (EV-01…EV-15)

Not delivered here. The identity layer provides what they need: PersonID on registrations (EV-02 check-in, EV-05 certificates, EV-12 CareerOS references), organizer authorization plus step-up (EV-06 verification, EV-04 refunds), the domain-event envelope (EV-10 attribution, EV-15 KPIs), and consented snapshots (EV-09 lead capture).
