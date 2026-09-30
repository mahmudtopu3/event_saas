"""Real-Keycloak end-to-end check of the EventOS browser flow (throwaway realm eventos-e2e)."""
import re
import sys
import time
from html import unescape
from urllib.parse import parse_qs, urlparse

import os

import requests

APP = 'http://localhost:8010'
KC = 'http://127.0.0.1:18081/realms/eventos-e2e'
E2E_USER = os.environ.get('E2E_USERNAME', 'jobseeker100')
E2E_PASSWORD = os.environ['E2E_PASSWORD']        # the test user's password from your local realm file
results = []


def check(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))


class LocalSession(requests.Session):
    """Browsers treat http://127.0.0.1 as a secure context and send Keycloak's Secure cookies; requests doesn't."""

    def send(self, request, **kwargs):
        for c in self.cookies:
            c.secure = False
        request.prepare_cookies(self.cookies)
        return super().send(request, **kwargs)


def kc_login_form(resp):
    m = re.search(r'<form[^>]+id="kc-form-login"[^>]+action="([^"]+)"', resp.text)
    return unescape(m.group(1)) if m else None


for _ in range(60):
    try:
        if requests.get(f'{KC}/.well-known/openid-configuration', timeout=2).ok:
            break
    except requests.RequestException:
        pass
    time.sleep(2)

s = LocalSession()

# 1. EVENTOS_AUTH_MODE=keycloak, EMPLOYER_AUTH_MODE=dual: login page offers SSO first and keeps
#    only the employer password fallback; local signup hands off to central account creation.
r = s.get(f'{APP}/accounts/login/?next=/my-registrations/', allow_redirects=False)
check('login page offers Continue with Skill.jobs', r.status_code == 200 and 'Continue with Skill.jobs' in r.text, r.status_code)
r = s.get(f'{APP}/accounts/signup/', allow_redirects=False)
check('signup redirects to central Keycloak account creation', r.status_code == 302 and 'signup=1' in r.headers.get('Location', ''))

# 2. /auth/login/ → Keycloak authorization endpoint with PKCE.
r = s.get(f'{APP}/auth/login/?next=/my-registrations/', allow_redirects=False)
auth = urlparse(r.headers.get('Location', ''))
q = {k: v[0] for k, v in parse_qs(auth.query).items()}
check('redirects to real Keycloak authorize endpoint', r.headers.get('Location', '').startswith(f'{KC}/protocol/openid-connect/auth'))
check('PKCE S256 + state + nonce present', q.get('code_challenge_method') == 'S256' and q.get('state') and q.get('nonce'))

# 3. Keycloak login page → submit credentials.
r = s.get(r.headers['Location'])
action = kc_login_form(r)
check('Keycloak shows password form (no existing SSO session)', action is not None)
r = s.post(action, data={'username': E2E_USER, 'password': E2E_PASSWORD, 'credentialId': ''}, allow_redirects=False)
cb = r.headers.get('Location', '')
check('Keycloak redirects back to exact registered callback', cb.startswith(f'{APP}/auth/callback/'), cb[:120])

# 4. Callback: code exchange (confidential client + PKCE), ID token validated via real JWKS.
r = s.get(cb, allow_redirects=False)
check('callback accepted and redirects to next', r.status_code == 302 and r.headers['Location'] == '/my-registrations/', f'{r.status_code} {r.text[:300]}')
r = s.get(f'{APP}/my-registrations/', allow_redirects=False)
check('protected page served to Keycloak principal', r.status_code == 200, r.status_code)

# 5. Register for the seeded event: stored by PersonID 100 (from the person_id claim).
r = s.get(f'{APP}/event/1/register/', allow_redirects=True)
check('registration succeeded', 'Successfully registered' in r.text)
r = s.get(f'{APP}/event/1/register/', allow_redirects=True)
check('duplicate registration prevented', 'already registered' in r.text)

# 6. SSO reuse: drop only the EventOS session; Keycloak session must satisfy login with NO password prompt.
kc_cookies = {c.name: c.value for c in s.cookies if c.domain.startswith('127.0.0.1')}
s2 = LocalSession()
for name, value in kc_cookies.items():
    s2.cookies.set(name, value, domain='127.0.0.1', path='/realms/eventos-e2e/')
r = s2.get(f'{APP}/auth/login/?next=/my-registrations/', allow_redirects=False)
r = s2.get(r.headers['Location'], allow_redirects=False)
silent = r.status_code == 302 and r.headers.get('Location', '').startswith(f'{APP}/auth/callback/')
check('second EventOS login completes silently via Keycloak SSO (no password)', silent, f'{r.status_code}')
if silent:
    r = s2.get(r.headers['Location'], allow_redirects=False)
    check('silent SSO callback accepted', r.status_code == 302 and s2.get(f'{APP}/my-registrations/', allow_redirects=False).status_code == 200)

# 7. Single logout: POST /auth/logout/ → Keycloak end_session → back to EventOS; SSO session gone.
s.get(f'{APP}/my-registrations/')
csrftoken = s.cookies.get('csrftoken')
r = s.post(f'{APP}/auth/logout/', headers={'X-CSRFToken': csrftoken or ''}, data={'csrfmiddlewaretoken': csrftoken or ''}, allow_redirects=False)
end = r.headers.get('Location', '')
check('logout redirects to Keycloak end_session with id_token_hint', end.startswith(f'{KC}/protocol/openid-connect/logout') and 'id_token_hint=' in end, end[:120])
r = s.get(end, allow_redirects=False)
check('Keycloak returns to registered post-logout URI', r.headers.get('Location', '').startswith(f'{APP}/'), f'{r.status_code} {r.headers.get("Location")}')
check('EventOS session is gone after logout', s.get(f'{APP}/my-registrations/', allow_redirects=False).status_code == 302)
r = s.get(f'{APP}/auth/login/', allow_redirects=False)
r = s.get(r.headers['Location'])
check('Keycloak SSO session ended (password form shown again)', kc_login_form(r) is not None)

# 8. Hostile inputs against the live server.
r = requests.get(f'{APP}/auth/callback/?state=forged&code=x')
check('forged state rejected by live server', r.status_code == 400 and 'bad_state' in r.text)
r = requests.get(f'{APP}/auth/login/', headers={'Host': 'evil.example:8010'}, allow_redirects=False)
check('unknown Host cannot start SSO (public schema, no route)', r.status_code == 404, r.status_code)

failed = [n for n, ok in results if not ok]
print(f'\n{len(results) - len(failed)}/{len(results)} checks passed')
sys.exit(1 if failed else 0)
