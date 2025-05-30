# eventsaas/tenant_urls.py (this is what routes tenant requests)
from django.contrib import admin
from django.urls import path, include
from events.views import signup, company_login
from django.http import HttpResponse
from django_tenants.utils import get_tenant
from django.conf.urls.static import static
from django.conf import settings


def debug_request(request):
    tenant = get_tenant(request)
    return HttpResponse(f"""
    <h1>Request Debug</h1>
    <p><strong>request.get_host():</strong> {request.get_host()}</p>
    <p><strong>HTTP_HOST header:</strong> {request.META.get('HTTP_HOST')}</p>
    <p><strong>Matched tenant:</strong> {tenant.name}</p>
    <p><strong>Tenant schema:</strong> {tenant.schema_name}</p>
    """)
    
    
urlpatterns = [
    path('admin/', admin.site.urls),  # Company admin
    path('accounts/login/', company_login, name='login'),
    path('accounts/', include('django.contrib.auth.urls')),
    
    path('accounts/signup/', signup, name='signup'),
    # path('', debug_request),
    path('', include('events.urls')),  # Events at root for tenants
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)