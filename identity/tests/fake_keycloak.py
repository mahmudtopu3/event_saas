# identity/tests/fake_keycloak.py
"""
In-process stand-in for the Keycloak realm (and the canonical backend) at the HTTP boundary.

Tokens are real RS256 JWTs signed with generated keys and published through a JWKS
endpoint, so signature/issuer/audience/expiry/nonce validation runs unmodified.
"""
import base64
import hashlib
import time
from unittest import mock
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from django.test import override_settings

from identity import oidc
from identity.service_http import reset_clients
from identity.token_validation import jwks_cache

ISSUER = 'https://kc.test/realms/skilljobs'
CLIENT_ID = 'eventos-web'
API_AUDIENCE = 'eventos-api'
BACKEND = 'https://backend.test'

_KEYS = {kid: rsa.generate_private_key(public_exponent=65537, key_size=2048) for kid in ('k1', 'k2', 'rogue')}


def keycloak_settings(**extra):
    base = dict(
        EVENTOS_AUTH_MODE='dual', EMPLOYER_AUTH_MODE='dual',
        KEYCLOAK_ISSUER_URL=ISSUER, KEYCLOAK_CLIENT_ID=CLIENT_ID, KEYCLOAK_CLIENT_SECRET='',
        KEYCLOAK_API_AUDIENCE=API_AUDIENCE, KEYCLOAK_SCOPES='openid', KEYCLOAK_PERSON_ID_SOURCE='claim',
        KEYCLOAK_POST_LOGOUT_REDIRECT_URI='', EVENTOS_OIDC_REQUIRE_HTTPS=False, EVENTOS_PRIVILEGED_ACR='',
        KEYCLOAK_SESSION_REVALIDATE_SECONDS=300, KEYCLOAK_ALLOWED_AZP=[],
        SKILLJOBS_BACKEND_URL=BACKEND,
        EVENTOS_PROFILE_CLIENT='identity.profile_client.InMemoryProfileClient',
        EVENTOS_EMPLOYER_CLIENT='identity.employer_client.InMemoryEmployerClient',
        EVENTOS_ENTITLEMENT_CLIENT='identity.entitlement_client.InMemoryEntitlementClient',
    )
    base.update(extra)
    return override_settings(**base)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


class FakeKeycloak:
    def __init__(self):
        self.active_kids = ['k1']
        self.codes = {}               # code -> {'tokens':..., 'redirect_uri':..., 'challenge':...}
        self.token_requests = []
        self.refresh_result = None    # dict of tokens, or ('error', code)
        self.jwks_fetches = 0
        self.backend_links = {}       # access_token -> person_id (backend_link mode)
        self.down = False

    # ── token minting ────────────────────────────────────────────────────
    def mint(self, *, kid='k1', alg='RS256', key=None, lifetime=300, **claims):
        now = int(time.time())
        payload = {'iss': ISSUER, 'sub': 'kc-sub-100', 'aud': CLIENT_ID, 'iat': now, 'exp': now + lifetime,
                   'person_id': 100, 'azp': CLIENT_ID, 'sid': 'sso-1'}
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        signing_key = key if key is not None else _KEYS[kid]
        return jwt.encode(payload, signing_key, algorithm=alg, headers={'kid': kid})

    def access_token(self, **claims):
        claims.setdefault('aud', API_AUDIENCE)
        claims.setdefault('typ', 'Bearer')
        claims.setdefault('scope', 'openid')
        return self.mint(**claims)

    def issue_code(self, *, nonce, id_claims=None, access_claims=None, refresh_token='rt-1'):
        code = f'code-{len(self.codes) + 1}'
        self.codes[code] = {
            'tokens': {
                'id_token': self.mint(nonce=nonce, **(id_claims or {})),
                'access_token': self.access_token(**(access_claims or {})),
                'refresh_token': refresh_token, 'token_type': 'Bearer',
            },
        }
        return code

    # ── HTTP boundary ────────────────────────────────────────────────────
    def _jwks(self):
        self.jwks_fetches += 1
        keys = []
        for kid in self.active_kids:
            jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_KEYS[kid].public_key(), as_dict=True)
            keys.append({**jwk, 'kid': kid, 'use': 'sig', 'alg': 'RS256'})
        return {'keys': keys}

    def get(self, url, **kwargs):
        if self.down:
            import requests
            raise requests.ConnectionError('down')
        if url == f'{ISSUER}/.well-known/openid-configuration':
            return FakeResponse(200, {
                'issuer': ISSUER,
                'authorization_endpoint': f'{ISSUER}/protocol/openid-connect/auth',
                'token_endpoint': f'{ISSUER}/protocol/openid-connect/token',
                'jwks_uri': f'{ISSUER}/protocol/openid-connect/certs',
                'end_session_endpoint': f'{ISSUER}/protocol/openid-connect/logout',
            })
        if url == f'{ISSUER}/protocol/openid-connect/certs':
            return FakeResponse(200, self._jwks())
        raise AssertionError(f'unexpected GET {url}')

    def post(self, url, data=None, auth=None, **kwargs):
        if self.down:
            import requests
            raise requests.ConnectionError('down')
        assert url == f'{ISSUER}/protocol/openid-connect/token', url
        self.token_requests.append(dict(data))
        if data['grant_type'] == 'refresh_token':
            if isinstance(self.refresh_result, tuple):
                return FakeResponse(400, {'error': self.refresh_result[1]})
            return FakeResponse(200, self.refresh_result or {'access_token': self.access_token()})
        entry = self.codes.pop(data.get('code'), None)
        if entry is None:
            return FakeResponse(400, {'error': 'invalid_grant'})
        # PKCE: the verifier must hash to the challenge sent on the authorization request.
        expected = entry.get('challenge')
        if expected:
            digest = base64.urlsafe_b64encode(hashlib.sha256(data['code_verifier'].encode()).digest()).rstrip(b'=')
            if digest.decode() != expected:
                return FakeResponse(400, {'error': 'invalid_grant'})
        if entry.get('redirect_uri') and entry['redirect_uri'] != data.get('redirect_uri'):
            return FakeResponse(400, {'error': 'invalid_grant'})
        return FakeResponse(200, entry['tokens'])

    def request(self, method, url, headers=None, json=None, timeout=None):
        """Canonical backend (service_http.request_json)."""
        if self.down:
            import requests
            raise requests.ConnectionError('down')
        if url == f'{BACKEND}/api/auth/keycloak/status/':
            pid = self.backend_links.get(json['access_token'])
            if pid is None:
                return FakeResponse(200, {'linked': False, 'onboarding_complete': False, 'code': 'no_local_account'})
            return FakeResponse(200, {'linked': True, 'onboarding_complete': True, 'code': 'ok',
                                      'user': {'id': pid, 'uuid': 'u', 'email': 'x@y.test', 'groups': []}})
        raise AssertionError(f'unexpected {method} {url}')

    # ── wiring ───────────────────────────────────────────────────────────
    def start(self, testcase):
        oidc.clear_discovery_cache()
        jwks_cache.clear()
        reset_clients()
        cache.clear()
        for target, fn in (('requests.get', self.get), ('requests.post', self.post),
                           ('requests.request', self.request)):
            patcher = mock.patch(target, side_effect=fn)
            patcher.start()
            testcase.addCleanup(patcher.stop)
        testcase.addCleanup(oidc.clear_discovery_cache)
        testcase.addCleanup(jwks_cache.clear)
        testcase.addCleanup(reset_clients)


def authorize_params(response):
    """Parse the Keycloak authorization redirect issued by /auth/login/."""
    assert response.status_code == 302, response.status_code
    url = urlparse(response['Location'])
    return {k: v[0] for k, v in parse_qs(url.query).items()}, f'{url.scheme}://{url.netloc}{url.path}'
