# identity/middleware.py
import time

from django.contrib.auth import logout as django_logout

from . import conf, oidc, sessions
from .exceptions import OIDCFlowError, TokenValidationError, UpstreamUnavailable
from .observability import audit
from .principal import PrincipalUser
from .token_validation import validate_jwt


class PrincipalMiddleware:
    """
    Loads the Keycloak Principal from the session (place after AuthenticationMiddleware).

    - Session bound to another tenant → dropped.
    - Keycloak disabled by rollback of the auth-mode flags → Keycloak sessions end.
    - Every KEYCLOAK_SESSION_REVALIDATE_SECONDS the refresh token is used; if Keycloak
      says the SSO session is gone (logout elsewhere, disabled user), the EventOS session
      ends. If Keycloak is unreachable, a grace period of one extra window applies,
      then the session fails closed.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.principal = None
        principal = sessions.load_principal(request.session) if hasattr(request, 'session') else None
        if principal is not None:
            if self._still_valid(request, principal):
                request.principal = principal
                request.user = PrincipalUser(principal)
            else:
                django_logout(request)
        return self.get_response(request)

    def _still_valid(self, request, principal):
        if not conf.keycloak_login_enabled():
            return False
        if principal.tenant_schema != getattr(request.tenant, 'schema_name', None):
            audit('tenant.access.denied', reason='session_tenant_mismatch', person_id=principal.person_id)
            return False
        cfg = conf.keycloak()
        toks = sessions.tokens(request.session)
        age = time.time() - float(toks.get('validated_at') or 0)
        if age < cfg.session_revalidate_seconds:
            return True
        if not toks.get('refresh_token'):
            return False
        try:
            fresh = oidc.refresh(toks['refresh_token'])
            if fresh.get('id_token'):
                claims = validate_jwt(fresh['id_token'], audience=cfg.client_id, require_person_id=False)
                if claims['sub'] != principal.subject:
                    return False
        except (OIDCFlowError, TokenValidationError) as exc:
            if self._refreshed_concurrently(request, toks):
                return True
            audit('auth.session.revoked', person_id=principal.person_id, reason=exc.code)
            return False
        except UpstreamUnavailable:
            return age < 2 * cfg.session_revalidate_seconds
        sessions.update_tokens(request.session, fresh)
        return True

    @staticmethod
    def _refreshed_concurrently(request, toks):
        """With refresh-token rotation, a parallel request may have used our refresh token first."""
        stored = request.session.__class__(request.session.session_key).load()
        latest = (stored or {}).get(sessions.TOKENS_KEY) or {}
        if latest.get('refresh_token') and latest['refresh_token'] != toks.get('refresh_token'):
            sessions.update_tokens(request.session, latest)
            return True
        return False
