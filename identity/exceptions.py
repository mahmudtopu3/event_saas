# identity/exceptions.py


class IdentityError(Exception):
    """Base class. `code` is a short, log-safe reason string (never contains token material)."""

    code = 'identity_error'

    def __init__(self, message='', code=None):
        super().__init__(message or self.code)
        if code:
            self.code = code


class TokenValidationError(IdentityError):
    code = 'invalid_token'


class OIDCFlowError(IdentityError):
    code = 'oidc_flow_error'


class HostNotPermitted(IdentityError):
    code = 'host_not_permitted'


class UpstreamUnavailable(IdentityError):
    """A canonical service (profile / employer / entitlement / Keycloak) could not be reached."""

    code = 'upstream_unavailable'
