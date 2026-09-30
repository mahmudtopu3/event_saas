# identity/decorators.py
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect

from . import conf
from .authorization import access_service


def _same_host_referer(request):
    from urllib.parse import urlparse

    ref = urlparse(request.headers.get('Referer', ''))
    if ref.netloc == request.get_host() and ref.path.startswith('/') and not ref.path.startswith('//'):
        return ref.path
    return '/'


def principal_required(view):
    """Require a Keycloak-authenticated person (not a legacy local-password user)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if getattr(request, 'principal', None) is None:
            return redirect_to_login(request.get_full_path(), login_url='/auth/login/')
        return view(request, *args, **kwargs)

    return wrapper


def organizer_required(view):
    """Person + employer membership + EVENTOS entitlement + tenant link + EventOS permission."""

    @wraps(view)
    @principal_required
    def wrapper(request, *args, **kwargs):
        decision = access_service.organizer_access(request)
        if not decision.allowed:
            raise PermissionDenied(decision.reason)
        request.organizer_decision = decision
        return view(request, *args, **kwargs)

    return wrapper


def strong_auth_required(view):
    """
    Privileged actions (publish, exports, refunds, certificates, tenant admin) require the
    Keycloak authentication level in EVENTOS_PRIVILEGED_ACR when that setting is configured.
    Step-up happens at Keycloak (MFA/passkey); EventOS never asks for a password itself.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        required = getattr(settings, 'EVENTOS_PRIVILEGED_ACR', '')
        principal = getattr(request, 'principal', None)
        if required and principal is not None and not conf.acr_satisfies(principal.acr, required):
            # After step-up the browser returns with a GET, so for POST actions come back to the
            # page the action was launched from rather than to the POST-only endpoint.
            back = request.get_full_path() if request.method == 'GET' else _same_host_referer(request)
            query = urlencode({'next': back, 'step_up': '1'})
            return HttpResponseRedirect(f'/auth/login/?{query}')
        return view(request, *args, **kwargs)

    return wrapper
