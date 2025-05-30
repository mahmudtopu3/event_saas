# events/context_processors.py
from django_tenants.utils import get_tenant

def tenant_context(request):
    """Add tenant information to all template contexts"""
    try:
        tenant = get_tenant(request)
        return {
            'tenant': tenant,
            'company_name': tenant.name if tenant.schema_name != 'public' else 'EventSaaS Admin',
            'is_public_schema': tenant.schema_name == 'public',
        }
    except:
        return {
            'tenant': None,
            'company_name': 'EventSaaS',
            'is_public_schema': True,
        }