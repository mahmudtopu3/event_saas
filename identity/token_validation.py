# identity/token_validation.py
"""
Signature-verified JWT validation against the Keycloak realm JWKS.

Nothing in EventOS reads a token payload without going through `validate_jwt`.
"""
import threading
import time

import jwt
import requests

from . import conf
from .exceptions import TokenValidationError, UpstreamUnavailable
from .oidc import get_discovery


class JWKSCache:
    """
    Caches the realm signing keys. An unknown `kid` forces one refresh (key rotation);
    refreshes are rate-limited so garbage `kid`s can't be used to hammer Keycloak.
    """

    MIN_REFRESH_INTERVAL = 10

    def __init__(self):
        self._lock = threading.Lock()
        self._keys = {}
        self._fetched_at = 0.0

    def clear(self):
        with self._lock:
            self._keys, self._fetched_at = {}, 0.0

    def _fetch(self, cfg):
        jwks_uri = get_discovery()['jwks_uri']
        try:
            resp = requests.get(jwks_uri, timeout=cfg.http_timeout)
            resp.raise_for_status()
            jwk_set = jwt.PyJWKSet.from_dict(resp.json())
        except (requests.RequestException, ValueError, jwt.PyJWKSetError) as exc:
            raise UpstreamUnavailable('jwks fetch failed', code='jwks_unavailable') from exc
        self._keys = {k.key_id: k for k in jwk_set.keys if k.key_id and getattr(k, 'public_key_use', 'sig') in (None, 'sig')}
        self._fetched_at = time.monotonic()

    def get_key(self, kid):
        cfg = conf.keycloak()
        with self._lock:
            age = time.monotonic() - self._fetched_at
            if not self._keys or age > cfg.jwks_cache_seconds:
                self._fetch(cfg)
            elif kid not in self._keys and age > self.MIN_REFRESH_INTERVAL:
                self._fetch(cfg)
            key = self._keys.get(kid)
        if key is None:
            raise TokenValidationError('unknown signing key', code='unknown_kid')
        return key


jwks_cache = JWKSCache()


def validate_jwt(token, *, audience, nonce=None, require_person_id=True, token_type=None):
    """
    Verify signature, algorithm allow-list, issuer, audience, exp/iat/nbf and (optionally) nonce.
    Returns the verified claims dict. Raises TokenValidationError on any failure.
    """
    cfg = conf.keycloak()
    if not token or not isinstance(token, str):
        raise TokenValidationError('missing token', code='missing_token')
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenValidationError('malformed token', code='malformed_token') from exc

    alg = header.get('alg')
    if alg not in cfg.allowed_algorithms:
        raise TokenValidationError('algorithm not allowed', code='bad_alg')
    key = jwks_cache.get_key(header.get('kid'))

    try:
        claims = jwt.decode(
            token,
            key=key.key,
            algorithms=[alg],
            audience=audience,
            issuer=get_discovery()['issuer'],
            leeway=cfg.clock_skew_seconds,
            options={'require': ['exp', 'iat', 'iss', 'sub'], 'verify_signature': True},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenValidationError('token expired', code='expired') from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenValidationError('wrong audience', code='bad_audience') from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenValidationError('wrong issuer', code='bad_issuer') from exc
    except jwt.InvalidSignatureError as exc:
        raise TokenValidationError('bad signature', code='bad_signature') from exc
    except jwt.PyJWTError as exc:
        raise TokenValidationError('invalid token', code='invalid_token') from exc
    except (TypeError, ValueError) as exc:
        # e.g. header alg=ES256 with an RSA kid: PyJWT raises a non-PyJWTError.
        raise TokenValidationError('key does not match algorithm', code='bad_alg') from exc

    if token_type and claims.get('typ') and claims['typ'].lower() != token_type.lower():
        raise TokenValidationError('wrong token type', code='bad_typ')
    if nonce is not None and claims.get('nonce') != nonce:
        raise TokenValidationError('nonce mismatch', code='bad_nonce')
    if require_person_id and not str(claims.get(cfg.person_id_claim) or '').strip():
        raise TokenValidationError('person id claim missing', code='missing_person_id')
    return claims
