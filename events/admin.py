from django.contrib import admin
from django.utils.html import format_html
from django_tenants.utils import get_tenant
from .models import Event, Registration

class TenantEventAdmin(admin.ModelAdmin):
    list_display = ('title', 'start_date', 'status', 'registration_count', 'max_attendees', 'created_by')
    list_filter = ('status', 'start_date', 'created_at')
    search_fields = ('title', 'description', 'location')
    readonly_fields = ('created_at', 'updated_at', 'registration_count')
    
    fieldsets = (
        ('Event Details', {
            'fields': ('title', 'description', 'location', 'status')
        }),
        ('Schedule', {
            'fields': ('start_date', 'end_date', 'registration_deadline')
        }),
        ('Registration', {
            'fields': ('max_attendees', 'registration_count')
        }),
        ('Meta', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def save_model(self, request, obj, form, change):
        if not change:  # If creating new event
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def registration_count(self, obj):
        count = obj.registrations.filter(status='confirmed').count()
        total = obj.max_attendees or "∞"
        return format_html(f'<strong>{count}/{total}</strong>')
    registration_count.short_description = 'Registrations'
    
    def has_module_permission(self, request):
        """Only show events admin in tenant schemas (not public)"""
        tenant = get_tenant(request)
        return tenant.schema_name != 'public'

class TenantRegistrationAdmin(admin.ModelAdmin):
    list_display = ('user', 'event', 'status', 'registered_at')
    list_filter = ('status', 'registered_at', 'event__start_date')
    search_fields = ('user__username', 'user__email', 'event__title')
    readonly_fields = ('registered_at', 'updated_at')
    
    def has_module_permission(self, request):
        """Only show registrations admin in tenant schemas"""
        tenant = get_tenant(request)
        return tenant.schema_name != 'public'

# Register only in tenant schemas
admin.site.register(Event, TenantEventAdmin)
admin.site.register(Registration, TenantRegistrationAdmin)