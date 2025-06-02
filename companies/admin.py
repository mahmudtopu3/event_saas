from django.contrib import admin
from django.http import Http404
from django.urls import reverse
from django.utils.html import format_html
from django_tenants.utils import get_tenant
from .models import Company, Domain

class SecureCompanyAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'schema_name',
        'subscription_status',  # Custom method to show status with colors
        'on_trial',
        'paid_until',
        'created_at',
        'view_users_link'
    )
    list_filter = (
        'on_trial',
        'created_at',
        'paid_until',
        'is_active_subscription',  # Allow filtering by active/inactive
    )
    search_fields = ('name', 'schema_name', 'contact_email')
    readonly_fields = ('schema_name', 'created_at', 'view_users_link', 'view_company_link')
    
    fieldsets = (
        ('Company Information', {
            'fields': ('name', 'schema_name', 'contact_email')
        }),
        ('Subscription Management', {
            'fields': ('is_active_subscription', 'on_trial', 'paid_until'),
            'description': 'Control access to this tenant\'s site',
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
        ('Quick Actions', {
            'fields': ('view_users_link', 'view_company_link'),
            'description': 'Quick links to view company details and manage users',
        }),
    )
    
    def subscription_status(self, obj):
        """Display subscription status with color coding"""
        if obj.is_active_subscription:
            return format_html(
                '<span style="color: green; font-weight: bold;">✓ Active</span>'
            )
        else:
            return format_html(
                '<span style="color: red; font-weight: bold;">✗ Inactive</span>'
            )
    subscription_status.short_description = "Subscription"
    subscription_status.admin_order_field = 'is_active_subscription'
    
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
    
    def view_users_link(self, obj):
        """Add a clickable link to view users for this company"""
        if obj.schema_name:
            url = reverse('company_users', kwargs={'schema_name': obj.schema_name})
            return format_html(
                '<a href="{}" class="button" target="_blank" style="'
                'background: #417690; color: white; padding: 5px 10px; '
                'text-decoration: none; border-radius: 4px; font-size: 11px;">'
                '👥 View Users</a>',
                url
            )
        return "-"
    view_users_link.short_description = "Tenant Users"
    view_users_link.admin_order_field = 'schema_name'

    def view_company_link(self, obj):
        """Add a clickable link to view company detail page"""
        if obj.schema_name:
            url = reverse('company_detail', kwargs={'schema_name': obj.schema_name})
            return format_html(
                '<a href="{}" class="button" target="_blank" style="'
                'background: #28a745; color: white; padding: 5px 10px; '
                'text-decoration: none; border-radius: 4px; font-size: 11px;">'
                '🏢 View Details</a>',
                url
            )
        return "-"
    view_company_link.short_description = "Company Page"

    # Add custom admin actions for subscription management
    actions = ['activate_subscription', 'deactivate_subscription', 'mark_as_trial', 'mark_as_active']

    def activate_subscription(self, request, queryset):
        """Activate subscription for selected companies"""
        updated = queryset.update(is_active_subscription=True)
        self.message_user(request, f'{updated} companies activated. Their sites are now accessible.')
    activate_subscription.short_description = "✓ Activate subscription for selected companies"

    def deactivate_subscription(self, request, queryset):
        """Deactivate subscription for selected companies"""
        updated = queryset.update(is_active_subscription=False)
        self.message_user(request, f'{updated} companies deactivated. Their sites are now blocked.')
    deactivate_subscription.short_description = "✗ Deactivate subscription for selected companies"

    def mark_as_trial(self, request, queryset):
        """Mark selected companies as trial"""
        updated = queryset.update(on_trial=True)
        self.message_user(request, f'{updated} companies marked as trial.')
    mark_as_trial.short_description = "Mark selected companies as trial"

    def mark_as_active(self, request, queryset):
        """Mark selected companies as active (not trial)"""
        updated = queryset.update(on_trial=False)
        self.message_user(request, f'{updated} companies marked as active.')
    mark_as_active.short_description = "Mark selected companies as active"


class SecureDomainAdmin(admin.ModelAdmin):
    list_display = ('domain', 'tenant', 'is_primary', 'tenant_status', 'tenant_users_link')
    list_filter = ('is_primary',)
    search_fields = ('domain', 'tenant__name')
    
    def tenant_status(self, obj):
        """Show the subscription status of the tenant this domain belongs to"""
        if obj.tenant:
            if obj.tenant.is_active_subscription:
                return format_html('<span style="color: green;">✓ Active</span>')
            else:
                return format_html('<span style="color: red;">✗ Inactive</span>')
        return "-"
    tenant_status.short_description = "Tenant Status"
    
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
    
    def tenant_users_link(self, obj):
        """Add link to view users for the tenant this domain belongs to"""
        if obj.tenant and obj.tenant.schema_name:
            url = reverse('company_users', kwargs={'schema_name': obj.tenant.schema_name})
            return format_html(
                '<a href="{}" target="_blank" style="color: #417690; text-decoration: none;">'
                '👥 Users</a>',
                url
            )
        return "-"
    tenant_users_link.short_description = "Users"


# Register only in public schema
admin.site.register(Company, SecureCompanyAdmin)
admin.site.register(Domain, SecureDomainAdmin)