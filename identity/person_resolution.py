# identity/person_resolution.py
"""
Keycloak subject → canonical PersonID.

KEYCLOAK_PERSON_ID_SOURCE:
  'claim'        (target) read the verified `person_id` claim (KEYCLOAK_PERSON_ID_CLAIM)
                 that a Keycloak protocol mapper adds to the ID/access token.
  'backend_link' (interim) ask the canonical backend who this token belongs to:
                 POST {SKILLJOBS_BACKEND_URL}/api/auth/keycloak/status/ (existing endpoint,
                 backed by backendmain's KeycloakIdentity (iss, sub) link). The token must
                 carry the backend's audience (`skilljobs-api`).

EventOS never infers a person from email, username or name.
"""
from django.conf import settings
from django.core.cache import cache

from . import conf
from .exceptions import TokenValidationError, UpstreamUnavailable
from .service_http import CallContext, backend_url, request_json

CLAIM = 'claim'
BACKEND_LINK = 'backend_link'


def source():
    value = getattr(settings, 'KEYCLOAK_PERSON_ID_SOURCE', CLAIM)
    if value not in (CLAIM, BACKEND_LINK):
        raise TokenValidationError('bad KEYCLOAK_PERSON_ID_SOURCE', code='bad_person_source')
    return value


def _positive_int(value):
    try:
        pid = int(str(value).strip())
    except (TypeError, ValueError):
        raise TokenValidationError('person id not an integer', code='bad_person_id')
    if pid <= 0:
        raise TokenValidationError('person id not positive', code='bad_person_id')
    return pid


def resolve_person_id(claims, *, access_token=None, tenant_schema=None):
    """`claims` must already be signature-verified. Returns the canonical PersonID (int)."""
    if source() == CLAIM:
        value = claims.get(conf.keycloak().person_id_claim)
        if value in (None, ''):
            raise TokenValidationError('person id claim missing', code='missing_person_id')
        return _positive_int(value)

    cache_key = f"eventos:person-link:{claims['iss']}:{claims['sub']}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    if not access_token:
        raise TokenValidationError('access token required for backend link', code='missing_person_id')
    status, body = request_json(
        'POST', backend_url('/api/auth/keycloak/status/'),
        CallContext(purpose='eventos.resolve_person', tenant_schema=tenant_schema),
        json={'access_token': access_token},
    )
    if status == 401:
        raise TokenValidationError('backend rejected token', code='backend_rejected_token')
    if status != 200:
        raise UpstreamUnavailable('person link lookup failed', code=f'person_link_{status}')
    if not body.get('linked'):
        raise TokenValidationError('no Skill.jobs account linked', code='not_linked')
    if not body.get('onboarding_complete'):
        raise TokenValidationError('Skill.jobs onboarding incomplete', code='onboarding_incomplete')
    user = body.get('user')
    pid = _positive_int(user.get('id') if isinstance(user, dict) else None)
    cache.set(cache_key, pid, int(getattr(settings, 'KEYCLOAK_PERSON_LINK_CACHE_SECONDS', 300)))
    return pid
