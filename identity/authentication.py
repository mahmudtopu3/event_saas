# identity/authentication.py
"""DRF bearer authentication with Keycloak access tokens → the same Principal as the browser flow."""
from django.conf import settings
from django_tenants.utils import get_public_schema_name
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from . import conf
from .exceptions import IdentityError
from .observability import audit
from .person_resolution import resolve_person_id
from .principal import Principal, PrincipalUser
from .token_validation import validate_jwt


class KeycloakBearerAuthentication(BaseAuthentication):
    keyword = b'bearer'

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword:
            return None
        if not conf.keycloak_login_enabled():
            return None             # legacy mode: behave exactly as before (header ignored)
        if len(parts) != 2:
            raise exceptions.AuthenticationFailed('Invalid Authorization header.')
        tenant = getattr(request, 'tenant', None)
        if tenant is None or tenant.schema_name == get_public_schema_name():
            raise exceptions.AuthenticationFailed('Unknown tenant.')

        token = parts[1].decode('ascii', errors='replace')
        cfg = conf.keycloak()
        try:
            claims = validate_jwt(token, audience=cfg.api_audience, require_person_id=False, token_type='Bearer')
            allowed_azp = tuple(getattr(settings, 'KEYCLOAK_ALLOWED_AZP', ()) or ())
            if allowed_azp and claims.get('azp') not in allowed_azp:
                raise exceptions.AuthenticationFailed('Client not allowed.')
            person_id = resolve_person_id(claims, access_token=token, tenant_schema=tenant.schema_name)
        except IdentityError as exc:
            audit('auth.bearer.rejected', tenant=tenant.schema_name, reason=exc.code)
            raise exceptions.AuthenticationFailed('Invalid token.')

        principal = Principal.from_claims(claims, person_id=person_id, tenant_schema=tenant.schema_name,
                                          source='bearer')
        request._request.principal = principal
        return PrincipalUser(principal), principal

    def authenticate_header(self, request):
        return 'Bearer realm="eventos"'
