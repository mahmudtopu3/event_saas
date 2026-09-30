# identity/hosts.py
"""
Which hosts may run the Keycloak login flow.

A hostname only routes a tenant; it never identifies a person. To start or
finish a login, the request host must be a known, active Domain of the request's
tenant, explicitly approved for OIDC, and (in production) served over HTTPS
with TLS marked ready. The callback URL is always derived from that Domain,
never from request parameters.
"""
from django_tenants.utils import get_public_schema_name, schema_context

from . import conf
from .exceptions import HostNotPermitted


def _hostname(request):
    return request.get_host().split(':')[0].lower().rstrip('.')


def oidc_domain_for_request(request):
    from companies.models import Domain

    tenant = getattr(request, 'tenant', None)
    if tenant is None or tenant.schema_name == get_public_schema_name():
        raise HostNotPermitted('public schema has no end-user login', code='public_schema')

    hostname = _hostname(request)
    with schema_context(get_public_schema_name()):
        domain = Domain.objects.filter(domain=hostname).select_related('tenant').first()
    if domain is None:
        raise HostNotPermitted('unknown domain', code='unknown_domain')
    if domain.tenant_id != tenant.pk:
        raise HostNotPermitted('domain belongs to another tenant', code='tenant_mismatch')
    if not domain.is_active:
        raise HostNotPermitted('domain disabled', code='domain_inactive')
    if not domain.oidc_callback_approved:
        raise HostNotPermitted('domain not approved for SSO', code='domain_not_approved')
    if conf.keycloak().require_https and not (request.is_secure() and domain.tls_ready):
        raise HostNotPermitted('https required', code='https_required')
    return domain


def base_url(request, domain):
    """scheme://host[:port] for this approved domain (port kept only for local development)."""
    scheme = 'https' if request.is_secure() else 'http'
    host = request.get_host()
    port = host.split(':', 1)[1] if ':' in host else ''
    return f"{scheme}://{domain.domain}{':' + port if port else ''}"


def redirect_uri(request, domain):
    return f'{base_url(request, domain)}/auth/callback/'


def post_logout_redirect_uri(request, domain):
    template = conf.keycloak().post_logout_redirect_uri
    if template:
        return template.replace('{host}', domain.domain)
    return f'{base_url(request, domain)}/'
