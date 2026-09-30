# identity/context_processors.py
from django.utils.functional import SimpleLazyObject

from . import conf, sessions
from .profile_client import display_name


def identity(request):
    principal = getattr(request, 'principal', None)

    def _name():
        # Read live from the canonical Person API only when a template renders it.
        access_token = sessions.tokens(request.session).get('access_token') if hasattr(request, 'session') else None
        return display_name(principal.person_id, access_token=access_token,
                            tenant_schema=getattr(request.tenant, 'schema_name', None))

    return {
        'eventos_principal': principal,
        'eventos_display_name': SimpleLazyObject(_name) if principal else '',
        'keycloak_login_enabled': conf.keycloak_login_enabled(),
        'legacy_login_enabled': conf.legacy_login_enabled(),
        'legacy_signup_enabled': conf.legacy_signup_enabled(),
    }
