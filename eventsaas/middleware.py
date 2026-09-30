from django.http import Http404
from django.utils.deprecation import MiddlewareMixin
from django_tenants.middleware.main import TenantMainMiddleware
from django_tenants.utils import get_public_schema_name, get_tenant_model


class EventOSTenantMiddleware(TenantMainMiddleware):
    """
    Stock django-tenants host routing, except a Domain switched off (`is_active=False`)
    no longer routes to its tenant. It's then treated exactly like an unknown host,
    which gives custom domains a safe disable/revoke path without deleting the row.
    """

    def get_tenant(self, domain_model, hostname):
        domain = domain_model.objects.select_related('tenant').get(domain=hostname)
        if not domain.is_active:
            raise Http404('Domain disabled')
        return domain.tenant


class TenantURLConfMiddleware(MiddlewareMixin):
    def process_request(self, request):
        tenant = getattr(request, 'tenant', None)

        # With SHOW_PUBLIC_IF_NO_TENANT_FOUND, unknown hostnames are served from the
        # public schema but django-tenants leaves request.tenant unset. Fill it in so
        # downstream middleware, views and templates can rely on request.tenant.
        if tenant is None:
            tenant = get_tenant_model().objects.get(schema_name=get_public_schema_name())
            request.tenant = tenant

        # Force URLconf based on tenant schema
        if tenant.schema_name == get_public_schema_name():
            request.urlconf = 'eventsaas.urls'
        else:
            request.urlconf = 'eventsaas.tenant_urls'

        return None
