# EventOS: Keycloak Setup

> Nothing here is applied automatically. A Keycloak administrator makes these changes per environment. EventOS never mutates Keycloak.

## Discovered realm facts (backendmain `infrastructure/keycloak/realm/skilljobs-realm.json`)

- Realm `skilljobs`. Issuer: local `http://localhost:8081/realms/skilljobs`, staging `https://accounts-stage.skill.jobs/realms/skilljobs`, production not pinned (docs name `accounts.skill.jobs`).
- Existing clients: `careeros-web`, `talentos-web` (public, PKCE S256), `skilljobs-api` (bearer-only). **There's no EventOS client yet.**
- **There's no `person_id` claim mapper.** The `sub` → person link lives in backendmain `KeycloakIdentity(issuer, subject) → CustomUser`.
- Realm roles `jobseeker`, `company`, `department`, `section` are observational only. EventOS never treats them as entitlements.

## 1. Create client `eventos-web`

| Setting | Value |
|---|---|
| Client type | OpenID Connect, **confidential** (client secret; server-side app). A public client + PKCE also works: leave `KEYCLOAK_CLIENT_SECRET` empty. |
| Flows | Standard flow **only** (no implicit, no direct access grants) |
| PKCE | `pkce.code.challenge.method = S256` |
| Valid redirect URIs | **Exact** URIs from `python manage.py eventos_oidc_redirect_uris`. **Never** a wildcard. |
| Post-logout redirect URIs | Same command (`post.logout.redirect.uris`) |
| Web origins | Same command |
| Front/back-channel logout | Not required (EventOS revalidates with the refresh token every `KEYCLOAK_SESSION_REVALIDATE_SECONDS`) |

Every time a domain is approved (`manage_eventos_domain … --approve-oidc`), rerun the command and add the new exact URIs. Automating this with the Keycloak Admin API is an open decision (see the report).

## 2. Token contents

Minimum contract: `sub`, `person_id`, `iss`, `aud`, `iat`, `exp`, `scope`, plus `sid`, `acr`, `azp`. **Don't** add profile data (name/phone/CV...). EventOS ignores it anyway.

**Target (`KEYCLOAK_PERSON_ID_SOURCE=claim`)**: add a protocol mapper to `eventos-web` (ideally a shared client scope `skilljobs-person`):
- Mapper type: *User Attribute*, user attribute `person_id`, claim `person_id`, JSON type `long`, in ID token and access token.
- Requires backendmain to write the `person_id` user attribute (= `CustomUser.pk`) when it creates or links a `KeycloakIdentity`. **This is backendmain work** (open decision).

**Interim (`KEYCLOAK_PERSON_ID_SOURCE=backend_link`)**: no Keycloak change beyond the audience below. EventOS calls the existing `POST {SKILLJOBS_BACKEND_URL}/api/auth/keycloak/status/` with the user's access token. Requires the `skilljobs-api` audience on EventOS access tokens (mapper `skilljobs-api-audience`, same as `careeros-web`).

## 3. API audience

For bearer calls to EventOS `/api/`: add an *Audience* mapper (`included.custom.audience = eventos-api`, access token) on each client allowed to call EventOS, and set `KEYCLOAK_API_AUDIENCE=eventos-api`. Optionally restrict callers with `KEYCLOAK_ALLOWED_AZP`. This closes the gap noted in backendmain, where `azp` isn't checked.

## 4. Step-up for privileged actions

Configure Keycloak *Level of Authentication* (browser flow conditions with OTP/WebAuthn) and set `EVENTOS_PRIVILEGED_ACR` to the LoA value that means "MFA" (e.g. `2`). Observed on Keycloak 26: password login gives `acr=1`, and silent SSO reuse gives `acr=0`. EventOS sends `acr_values` and `prompt=login` and verifies the returned `acr`.

## 5. EventOS environment

```
EVENTOS_AUTH_MODE=dual            # then keycloak
EMPLOYER_AUTH_MODE=dual
KEYCLOAK_ISSUER_URL=https://accounts.skill.jobs/realms/skilljobs
KEYCLOAK_CLIENT_ID=eventos-web
KEYCLOAK_CLIENT_SECRET=<from vault>
KEYCLOAK_API_AUDIENCE=eventos-api
KEYCLOAK_PERSON_ID_SOURCE=claim   # or backend_link (interim)
SKILLJOBS_BACKEND_URL=https://<backend>
EVENTOS_OIDC_REQUIRE_HTTPS=True
```

Validation performed by EventOS: discovery issuer equals the configured issuer; RS256/PS256/ES256 only (no `none`/HS*); JWKS signature with rotation (unknown `kid` → one rate-limited refresh); `iss`, `aud`, `exp`, `iat` with 30s skew; `nonce`; `azp`; `typ=Bearer` for API tokens; state is single-use with a 10-minute TTL; PKCE S256; the redirect URI is derived from the approved Domain.

## 6. Verified locally

`tools/keycloak_e2e/` runs the full flow against a real Keycloak 26 (throwaway realm): 18/18 checks passed, including silent SSO with no second password prompt and single logout.
