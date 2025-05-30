from django.contrib import admin
from django.http import Http404
from django_tenants.utils import get_tenant
from .models import Company, Domain

class SecureCompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'schema_name', 'on_trial', 'paid_until', 'created_at')
    list_filter = ('on_trial', 'created_at', 'paid_until')
    search_fields = ('name', 'schema_name', 'contact_email')
    readonly_fields = ('schema_name', 'created_at')
    
    def get_queryset(self, request):
        """Only show companies in public schema (super admin only)"""
        tenant = get_tenant(request)
        
        # Only allow access in public schema
        if tenant.schema_name != 'public':
            return Company.objects.none()  # Return empty queryset
        
        return super().get_queryset(request)
    
    def has_module_permission(self, request):
        """Only show this admin in public schema"""
        tenant = get_tenant(request)
        return tenant.schema_name == 'public'

class SecureDomainAdmin(admin.ModelAdmin):
    list_display = ('domain', 'tenant', 'is_primary')
    list_filter = ('is_primary',)
    search_fields = ('domain', 'tenant__name')
    
    def get_queryset(self, request):
        """Only show domains in public schema"""
        tenant = get_tenant(request)
        
        if tenant.schema_name != 'public':
            return Domain.objects.none()
        
        return super().get_queryset(request)
    
    def has_module_permission(self, request):
        """Only show this admin in public schema"""
        tenant = get_tenant(request)
        return tenant.schema_name == 'public'

# Register only in public schema
admin.site.register(Company, SecureCompanyAdmin)
admin.site.register(Domain, SecureDomainAdmin)