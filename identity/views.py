# identity/views.py
"""/auth/login/, /auth/callback/, /auth/logout/: Keycloak OIDC for EventOS tenant hosts."""
import time
from urllib.parse import urlencode

from django.contrib.auth import logout as django_logout
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import conf, oidc, sessions
from .exceptions import IdentityError
from .hosts import oidc_domain_for_request, post_logout_redirect_uri, redirect_uri
from .models import LegacyUserLink
from .observability import audit
from .person_resolution import BACKEND_LINK, resolve_person_id, source as person_source
from .principal import Principal
from .token_validation import validate_jwt


def _safe_path(request, value, default='/'):
    """Only same-host relative paths are accepted as post-login targets."""
    if value and value.startswith('/') and not value.startswith('//') and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return default


def _failure(request, reason, status=400):
    audit('auth.login.failed', tenant=getattr(request.tenant, 'schema_name', None), reason=reason)
    return render(request, 'identity/login_error.html', {'reason': reason}, status=status)


def _require_enabled():
    if not conf.keycloak_login_enabled():
        raise Http404('Keycloak login is not enabled')


@never_cache
@require_GET
def login(request):
    _require_enabled()
    try:
        domain = oidc_domain_for_request(request)
        cfg = conf.keycloak()
        verifier, challenge = oidc.new_pkce_pair()
        state, nonce = oidc.new_state(), oidc.new_nonce()
        callback = redirect_uri(request, domain)
        pending = {
            'nonce': nonce,
            'code_verifier': verifier,
            'redirect_uri': callback,
            'host': request.get_host(),
            'tenant': request.tenant.schema_name,
            'domain_id': domain.pk,
            'next': _safe_path(request, request.GET.get('next')),
            'step_up': bool(request.GET.get('step_up')),
            'expires_at': time.time() + cfg.login_request_ttl_seconds,
        }
        # Dual-mode account linking: a user already signed in with a legacy EventOS
        # password proves control of the Keycloak identity by completing this login.
        if request.GET.get('link') and request.user.is_authenticated and getattr(request.user, 'pk', None):
            pending['link_user_id'] = request.user.pk
        sessions.add_pending(request.session, state, pending)
        prompt = 'create' if request.GET.get('signup') else ('login' if pending['step_up'] else None)
        url = oidc.authorization_url(redirect_uri=callback, state=state, nonce=nonce,
                                     code_challenge=challenge, prompt=prompt)
        acr = _step_up_acr() if pending['step_up'] else None
        if acr:
            url += '&' + urlencode({'acr_values': acr})
    except IdentityError as exc:
        return _failure(request, exc.code)
    audit('auth.login.started', tenant=request.tenant.schema_name, host=domain.domain)
    return HttpResponseRedirect(url)


def _step_up_acr():
    from django.conf import settings

    return getattr(settings, 'EVENTOS_PRIVILEGED_ACR', '') or ''


@never_cache
@require_GET
def callback(request):
    _require_enabled()
    state = request.GET.get('state')
    pending = sessions.pop_pending(request.session, state)
    if pending is None:
        return _failure(request, 'bad_state')
    if pending['expires_at'] < time.time():
        return _failure(request, 'state_expired')
    if request.GET.get('error'):
        return _failure(request, 'idp_error')
    if pending['host'] != request.get_host():
        return _failure(request, 'host_mismatch')
    if pending['tenant'] != request.tenant.schema_name:
        return _failure(request, 'tenant_mismatch')
    code = request.GET.get('code')
    if not code:
        return _failure(request, 'missing_code')

    try:
        domain = oidc_domain_for_request(request)          # re-checked: may have been disabled meanwhile
        if domain.pk != pending['domain_id'] or redirect_uri(request, domain) != pending['redirect_uri']:
            return _failure(request, 'callback_mismatch')
        cfg = conf.keycloak()
        tokens = oidc.exchange_code(code=code, redirect_uri=pending['redirect_uri'],
                                    code_verifier=pending['code_verifier'])
        id_claims = validate_jwt(tokens['id_token'], audience=cfg.client_id, nonce=pending['nonce'],
                                 require_person_id=False)
        if id_claims.get('azp') and id_claims['azp'] != cfg.client_id:
            return _failure(request, 'bad_azp')
        if person_source() == BACKEND_LINK:
            # The access token is what the backend inspects; prove it belongs to the same subject.
            from django.conf import settings

            access_claims = validate_jwt(tokens['access_token'],
                                         audience=getattr(settings, 'KEYCLOAK_BACKEND_AUDIENCE', 'skilljobs-api'),
                                         require_person_id=False, token_type='Bearer')
            if access_claims['sub'] != id_claims['sub']:
                return _failure(request, 'subject_mismatch')
        person_id = resolve_person_id(id_claims, access_token=tokens['access_token'],
                                      tenant_schema=request.tenant.schema_name)
    except IdentityError as exc:
        return _failure(request, exc.code)

    required_acr = _step_up_acr()
    if pending.get('step_up') and not conf.acr_satisfies(id_claims.get('acr'), required_acr):
        return _failure(request, 'step_up_not_satisfied', status=403)

    link_user_id = pending.get('link_user_id')
    link_result = None
    if link_user_id and getattr(request.user, 'pk', None) == link_user_id:
        link_result = _link_legacy_user(request.user, person_id)

    principal = Principal.from_claims(id_claims, person_id=person_id, tenant_schema=request.tenant.schema_name)
    django_logout(request)          # drops any legacy login and rotates the session key (fixation)
    sessions.store_login(request.session, principal, tokens)
    audit('auth.login.succeeded', tenant=principal.tenant_schema, person_id=person_id,
          acr=principal.acr or None, legacy_link=link_result)
    return HttpResponseRedirect(pending['next'])


def _link_legacy_user(user, person_id):
    existing = LegacyUserLink.objects.filter(user=user).first()
    if existing:
        return 'already_linked' if existing.person_id == person_id else 'conflict_user_linked_elsewhere'
    if LegacyUserLink.objects.filter(person_id=person_id).exists():
        return 'conflict_person_linked_elsewhere'
    try:
        with transaction.atomic():
            LegacyUserLink.objects.create(user=user, person_id=person_id, source='self_service_proof')
    except IntegrityError:
        return 'conflict_concurrent_link'
    return 'linked'


@never_cache
@require_POST
def logout(request):
    _require_enabled()
    tokens = sessions.tokens(request.session)
    was_keycloak = sessions.load_principal(request.session) is not None
    person = getattr(getattr(request, 'principal', None), 'person_id', None)
    django_logout(request)          # flushes the whole EventOS session (principal + tokens)
    audit('auth.logout', tenant=getattr(request.tenant, 'schema_name', None), person_id=person)
    target = '/'
    if was_keycloak and conf.keycloak_login_enabled():
        try:
            domain = oidc_domain_for_request(request)
            url = oidc.end_session_url(id_token_hint=tokens.get('id_token'),
                                       post_logout_redirect_uri=post_logout_redirect_uri(request, domain))
            target = url or target
        except IdentityError:
            pass                    # local session is gone regardless; SSO logout best effort
    return HttpResponseRedirect(target)
