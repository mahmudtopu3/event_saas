from django.utils.deprecation import MiddlewareMixin
from django_tenants.utils import get_tenant

class TenantURLConfMiddleware(MiddlewareMixin):
    def process_request(self, request):
        tenant = get_tenant(request)
        
        # Force URLconf based on tenant schema
        if tenant.schema_name == 'public':
            request.urlconf = 'eventsaas.urls'
        else:
            request.urlconf = 'eventsaas.tenant_urls'
        
        return None