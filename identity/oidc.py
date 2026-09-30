# identity/oidc.py
"""
OIDC Authorization Code + PKCE (S256) client for the Keycloak realm.

Endpoints come from OIDC discovery; nothing environment-specific is hard-coded.
"""
import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

import requests

from . import conf
from .exceptions import OIDCFlowError, UpstreamUnavailable

_REQUIRED_DISCOVERY_KEYS = ('issuer', 'authorization_endpoint', 'token_endpoint', 'jwks_uri')

_discovery_lock = threading.Lock()
_discovery = {'doc': None, 'at': 0.0}


def clear_discovery_cache():
    with _discovery_lock:
        _discovery['doc'], _discovery['at'] = None, 0.0


def get_discovery():
    cfg = conf.keycloak()
    with _discovery_lock:
        doc = _discovery['doc']
        if doc and time.monotonic() - _discovery['at'] < cfg.discovery_cache_seconds:
            return doc
        url = f'{cfg.issuer_url}/.well-known/openid-configuration'
        try:
            resp = requests.get(url, timeout=cfg.http_timeout)
            resp.raise_for_status()
            doc = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise UpstreamUnavailable('oidc discovery failed', code='discovery_unavailable') from exc
        missing = [k for k in _REQUIRED_DISCOVERY_KEYS if not doc.get(k)]
        if missing:
            raise OIDCFlowError(f'discovery document missing {missing}', code='bad_discovery')
        # The issuer in the document must be the one we were configured for (OIDC Discovery §4.3).
        if doc['issuer'].rstrip('/') != cfg.issuer_url:
            raise OIDCFlowError('discovery issuer mismatch', code='issuer_mismatch')
        _discovery['doc'], _discovery['at'] = doc, time.monotonic()
        return doc


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def new_pkce_pair():
    verifier = _b64url(secrets.token_bytes(48))  # 64 chars, within RFC 7636's 43–128
    challenge = _b64url(hashlib.sha256(verifier.encode('ascii')).digest())
    return verifier, challenge


def new_state():
    return secrets.token_urlsafe(32)


def new_nonce():
    return secrets.token_urlsafe(32)


def authorization_url(*, redirect_uri, state, nonce, code_challenge, prompt=None):
    cfg = conf.keycloak()
    params = {
        'response_type': 'code',
        'client_id': cfg.client_id,
        'redirect_uri': redirect_uri,
        'scope': cfg.scopes,
        'state': state,
        'nonce': nonce,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    }
    if prompt:
        params['prompt'] = prompt
    return f"{get_discovery()['authorization_endpoint']}?{urlencode(params)}"


def _token_request(data):
    cfg = conf.keycloak()
    data = {**data, 'client_id': cfg.client_id}
    auth = None
    if cfg.client_secret:
        auth = (cfg.client_id, cfg.client_secret)  # client_secret_basic for confidential clients
    try:
        resp = requests.post(get_discovery()['token_endpoint'], data=data, auth=auth, timeout=cfg.http_timeout)
    except requests.RequestException as exc:
        raise UpstreamUnavailable('token endpoint unreachable', code='token_endpoint_unavailable') from exc
    if resp.status_code >= 500 or resp.status_code == 429:
        raise UpstreamUnavailable('token endpoint unavailable', code=f'token_endpoint_{resp.status_code}')
    if resp.status_code != 200:
        # Only the OAuth error code is surfaced; the body can echo request data.
        try:
            err = resp.json().get('error', 'token_error')
        except ValueError:
            err = 'token_error'
        raise OIDCFlowError('token request rejected', code=str(err)[:64])
    try:
        return resp.json()
    except ValueError as exc:
        raise OIDCFlowError('token response not json', code='bad_token_response') from exc


def exchange_code(*, code, redirect_uri, code_verifier):
    tokens = _token_request({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'code_verifier': code_verifier,
    })
    if not tokens.get('id_token') or not tokens.get('access_token'):
        raise OIDCFlowError('token response incomplete', code='incomplete_tokens')
    return tokens


def refresh(refresh_token):
    return _token_request({'grant_type': 'refresh_token', 'refresh_token': refresh_token})


def end_session_url(*, id_token_hint, post_logout_redirect_uri):
    endpoint = get_discovery().get('end_session_endpoint')
    if not endpoint:
        return None
    params = {'client_id': conf.keycloak().client_id, 'post_logout_redirect_uri': post_logout_redirect_uri}
    if id_token_hint:
        params['id_token_hint'] = id_token_hint
    return f'{endpoint}?{urlencode(params)}'
