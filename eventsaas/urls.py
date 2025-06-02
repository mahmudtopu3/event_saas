# eventsaas/urls.py (Public schema URLs)
from django.contrib import admin
from django.urls import path

from django.http import HttpResponse
from django_tenants.utils import get_tenant
from django.conf.urls.static import static
from django.conf import settings

from companies.views import company_detail, company_users, public_homepage


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
    path('admin/', admin.site.urls),  # Super admin for managing companies
    path('company/<str:schema_name>/', company_detail, name='company_detail'),  # Individual company pages
    path('', public_homepage, name='public_homepage'),  # Portfolio homepage
    path('company/<str:schema_name>/users/', company_users, name='company_users'),
    # path('', debug_request),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)