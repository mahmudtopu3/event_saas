# eventsaas/tenant_urls.py (this is what routes tenant requests)
from django.contrib import admin
from django.urls import path, include
from companies.views import order_thankyou, subscription_check, tenant_order, view_orders, view_plans
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
     # ─── Subscription / Order Routes ─────────────────────────────────────
    path('plans/', view_plans, name='view_plans'),
    path('order/', tenant_order, name='tenant_order'),
    path('order/thankyou/', order_thankyou, name='order_thankyou'),
    path('orders/', view_orders, name='view_orders'),
    path('subscription-check/', subscription_check, name='subscription_check'),
    # path('', debug_request),
    path('', include('events.urls')),  # Events at root for tenants
    path('api/', include('api.urls')),  # APIS tenants
    
]



if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)