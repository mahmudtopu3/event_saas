# identity/conf.py
"""
Single place where identity/auth configuration and rollout flags are read.

Code elsewhere must ask this module ("is Keycloak login enabled here?") instead of
reading settings or comparing mode strings itself, so the rollout phases stay
centralised.
"""
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

LEGACY = 'legacy'
DUAL = 'dual'
KEYCLOAK = 'keycloak'
AUTH_MODES = (LEGACY, DUAL, KEYCLOAK)


def _mode(name):
    value = (getattr(settings, name, LEGACY) or LEGACY).strip().lower()
    if value not in AUTH_MODES:
        raise ImproperlyConfigured(f'{name} must be one of {AUTH_MODES}, got {value!r}')
    return value


def auth_mode():
    """Rollout mode for ordinary EventOS users (attendees / job seekers)."""
    return _mode('EVENTOS_AUTH_MODE')


def employer_auth_mode():
    """Rollout mode for company / recruiter / organizer staff."""
    return _mode('EMPLOYER_AUTH_MODE')


def keycloak_login_enabled():
    return auth_mode() in (DUAL, KEYCLOAK) or employer_auth_mode() in (DUAL, KEYCLOAK)


def legacy_login_enabled():
    """Local username/password login stays available unless *both* populations are Keycloak-only."""
    return not (auth_mode() == KEYCLOAK and employer_auth_mode() == KEYCLOAK)


def legacy_signup_enabled():
    """Local EventOS signup (a new local password) is gone once ordinary users are Keycloak-only."""
    return auth_mode() != KEYCLOAK


def password_login_allowed_for(user):
    """Legacy password login per population: staff = employer side, everyone else = ordinary user."""
    mode = employer_auth_mode() if (user.is_staff or user.is_superuser) else auth_mode()
    return mode != KEYCLOAK


def acr_satisfies(acr, required):
    """Keycloak LoA values are ordered integers: a higher level satisfies a lower requirement."""
    if not required:
        return True
    acr, required = str(acr or ''), str(required)
    if acr.isdigit() and required.isdigit():
        return int(acr) >= int(required)
    return acr == required


@dataclass(frozen=True)
class KeycloakSettings:
    issuer_url: str
    client_id: str
    client_secret: str
    audience: str
    api_audience: str
    scopes: str
    post_logout_redirect_uri: str
    person_id_claim: str
    http_timeout: float
    discovery_cache_seconds: int
    jwks_cache_seconds: int
    clock_skew_seconds: int
    allowed_algorithms: tuple
    login_request_ttl_seconds: int
    session_revalidate_seconds: int
    require_https: bool
    registration_url: str


def keycloak():
    issuer = getattr(settings, 'KEYCLOAK_ISSUER_URL', '') or ''
    client_id = getattr(settings, 'KEYCLOAK_CLIENT_ID', '') or ''
    if not issuer or not client_id:
        raise ImproperlyConfigured('KEYCLOAK_ISSUER_URL and KEYCLOAK_CLIENT_ID are required for Keycloak login')
    return KeycloakSettings(
        issuer_url=issuer.rstrip('/'),
        client_id=client_id,
        client_secret=getattr(settings, 'KEYCLOAK_CLIENT_SECRET', '') or '',
        audience=getattr(settings, 'KEYCLOAK_AUDIENCE', '') or client_id,
        api_audience=getattr(settings, 'KEYCLOAK_API_AUDIENCE', '') or getattr(settings, 'KEYCLOAK_AUDIENCE', '') or client_id,
        scopes=getattr(settings, 'KEYCLOAK_SCOPES', 'openid'),
        post_logout_redirect_uri=getattr(settings, 'KEYCLOAK_POST_LOGOUT_REDIRECT_URI', '') or '',
        person_id_claim=getattr(settings, 'KEYCLOAK_PERSON_ID_CLAIM', 'person_id'),
        http_timeout=float(getattr(settings, 'KEYCLOAK_HTTP_TIMEOUT', 5)),
        discovery_cache_seconds=int(getattr(settings, 'KEYCLOAK_DISCOVERY_CACHE_SECONDS', 3600)),
        jwks_cache_seconds=int(getattr(settings, 'KEYCLOAK_JWKS_CACHE_SECONDS', 600)),
        clock_skew_seconds=int(getattr(settings, 'KEYCLOAK_CLOCK_SKEW_SECONDS', 30)),
        allowed_algorithms=tuple(getattr(settings, 'KEYCLOAK_ALLOWED_ALGORITHMS', ('RS256', 'PS256', 'ES256'))),
        login_request_ttl_seconds=int(getattr(settings, 'KEYCLOAK_LOGIN_REQUEST_TTL_SECONDS', 600)),
        session_revalidate_seconds=int(getattr(settings, 'KEYCLOAK_SESSION_REVALIDATE_SECONDS', 300)),
        require_https=bool(getattr(settings, 'EVENTOS_OIDC_REQUIRE_HTTPS', not settings.DEBUG)),
        registration_url=getattr(settings, 'KEYCLOAK_REGISTRATION_URL', '') or '',
    )
