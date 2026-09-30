# Real-Keycloak end-to-end check (local only)

Runs the EventOS browser flow against a **throwaway** Keycloak with its own realm
(`eventos-e2e`). It never touches the shared `skilljobs` realm. The realm has one
test user whose `person_id` attribute is emitted as the `person_id` claim.

> `eventos-e2e-realm.json` is **gitignored** because it holds throwaway test credentials. Create it
> locally before running. It needs: realm `eventos-e2e` (`sslRequired: none`, declarative user profile
> with `unmanagedAttributePolicy: ENABLED`); confidential client `eventos-web` (standard flow only,
> PKCE S256, redirect `http://localhost:8010/auth/callback/`, post-logout `http://localhost:8010/`)
> with a *User Attribute* mapper `person_id` (type long, ID and access token) and an *Audience* mapper
> `eventos-api` (access token); and user `jobseeker100` with attribute `person_id=100`. Choose your
> own client secret and password, then pass the same values to the server env and `e2e_flow.py`.

```bash
# 1. Disposable Postgres + Keycloak
docker run -d --name eventos-test-db -e POSTGRES_PASSWORD=eventos_test -e POSTGRES_DB=eventsaas \
  -p 127.0.0.1:55432:5432 postgres:16-alpine
docker exec eventos-test-db psql -U postgres -c "CREATE DATABASE eventos_e2e;"
docker run -d --name eventos-e2e-keycloak -p 127.0.0.1:18081:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD="$KC_ADMIN_PASSWORD" \
  -e KC_HOSTNAME=http://127.0.0.1:18081 \
  -v "$PWD/tools/keycloak_e2e/eventos-e2e-realm.json:/opt/keycloak/data/import/eventos-e2e-realm.json:ro" \
  quay.io/keycloak/keycloak:26.0 start-dev --import-realm

# 2. EventOS schema + a provisioned, SSO-approved tenant on "localhost"
export DATABASE_PORT=55432 DATABASE_PASSWORD=eventos_test DATABASE_NAME=eventos_e2e
python manage.py migrate_schemas --shared
python manage.py setup_public --domain 127.0.0.1
python manage.py provision_eventos_tenant --employer-id 7001 --name "E2E Employer" --actor e2e
python manage.py shell -c "from companies.models import *; c=Company.objects.get(canonical_employer_id=7001); \
Company.objects.filter(pk=c.pk).update(is_active_subscription=True); Domain.objects.create(domain='localhost', tenant=c)"
python manage.py manage_eventos_domain localhost --actor e2e --verify-ownership local-dev --approve-oidc
python manage.py shell -c "from django_tenants.utils import schema_context; from django.utils import timezone as t; \
from datetime import timedelta as d; from events.models import Event; \
exec('with schema_context(\"emp_7001\"): Event.objects.create(title=\"E2E\", description=\"d\", status=\"published\", start_date=t.now()+d(days=3), end_date=t.now()+d(days=3, hours=2))')"

# 3. Run EventOS in Keycloak mode, then the flow
EVENTOS_AUTH_MODE=keycloak EMPLOYER_AUTH_MODE=dual \
KEYCLOAK_ISSUER_URL=http://127.0.0.1:18081/realms/eventos-e2e KEYCLOAK_CLIENT_ID=eventos-web \
KEYCLOAK_CLIENT_SECRET="$E2E_CLIENT_SECRET" KEYCLOAK_API_AUDIENCE=eventos-api EVENTOS_OIDC_REQUIRE_HTTPS=false \
python manage.py runserver 127.0.0.1:8010 &
E2E_PASSWORD="$E2E_PASSWORD" python tools/keycloak_e2e/e2e_flow.py      # expects 18/18
```

It covers: SSO button and central signup hand-off, PKCE/state/nonce, the real login, the exact
callback, a PersonID-keyed registration, duplicate prevention, **silent SSO with no second
password prompt**, single logout via `end_session` with `id_token_hint`, forged-state rejection,
and unknown-Host rejection.
