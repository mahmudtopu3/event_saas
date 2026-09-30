# companies/admin.py

from django.contrib import admin
from django.utils import timezone
from django.http import Http404
from django.urls import reverse
from django.utils.html import format_html
from django_tenants.utils import get_tenant
from .models import Company, Domain, DomainAuditEvent, Plan, Order
from . import provisioning


class PublicSchemaOnlyMixin:
    """
    These models live in the public schema. The admin is also mounted on tenant hosts, where
    tenant superusers would otherwise pass the default has_*_permission checks and could
    create/edit platform rows (e.g. attach a Domain to any tenant). Deny all of it there.
    """

    def _on_public(self, request):
        return get_tenant(request).schema_name == 'public'

    def has_view_permission(self, request, obj=None):
        return self._on_public(request) and super().has_view_permission(request, obj)

    def has_add_permission(self, request, *args, **kwargs):
        return self._on_public(request) and super().has_add_permission(request, *args, **kwargs)

    def has_change_permission(self, request, obj=None):
        return self._on_public(request) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._on_public(request) and super().has_delete_permission(request, obj)



class SecureCompanyAdmin(PublicSchemaOnlyMixin, admin.ModelAdmin):
    """
    Only visible/runnable from the PUBLIC schema. Allows:
    - toggling is_active_subscription, on_trial, paid_until
    - quick links to view users & view public company page
    - custom actions: approve/deactivate subscriptions (for multiple companies at once)
    """
    list_display = (
        'name',
        'schema_name',
        'subscription_status',
        'on_trial',
        'paid_until',
        'current_plan',
        'created_at',
        'view_users_link',
        'view_company_link'
    )
    list_filter = (
        'on_trial',
        'created_at',
        'paid_until',
        'is_active_subscription',
        'current_plan',
    )
    search_fields = ('name', 'schema_name', 'contact_email', 'canonical_employer_id')
    readonly_fields = (
        'schema_name',
        'created_at',
        'view_users_link',
        'view_company_link'
    )

    fieldsets = (
        ('Company Information', {
            'fields': ('name', 'schema_name', 'contact_email', 'current_plan')
        }),
        ('Skill.jobs linkage', {
            'fields': ('canonical_employer_id', 'canonical_tenant_id'),
            'description': "Opaque references to the canonical Skill.jobs Employer/Tenant. "
                           "EventOS doesn't own employer identity.",
        }),
        ('Subscription Management', {
            'fields': ('is_active_subscription', 'on_trial', 'paid_until', 'subscription_start'),
            'description': "Toggle subscription status or adjust expiration date.",
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
        ('Quick Actions', {
            'fields': ('view_users_link', 'view_company_link'),
            'description': 'Quick links to see company details or tenant users.',
        }),
    )

    def subscription_status(self, obj):
        """Color‐coded display of whether the subscription is active."""
        now_date = timezone.now().date()
        expired = (obj.paid_until and obj.paid_until < now_date) or not obj.is_active_subscription
        if not expired:
            return format_html('<span style="color: green; font-weight: bold;">✓ Active</span>')
        else:
            return format_html('<span style="color: red; font-weight: bold;">✗ Inactive/Expired</span>')

    subscription_status.short_description = "Subscription"
    subscription_status.admin_order_field = 'is_active_subscription'

    def get_queryset(self, request):
        """Only show companies when in the PUBLIC schema."""
        tenant = get_tenant(request)
        if tenant.schema_name != 'public':
            return Company.objects.none()
        return super().get_queryset(request)

    def has_module_permission(self, request):
        """Show this admin only if in PUBLIC schema."""
        tenant = get_tenant(request)
        return tenant.schema_name == 'public'

    def view_users_link(self, obj):
        """Clickable link to view staff users for a given company (opens in new tab)."""
        if obj.schema_name:
            url = reverse('company_users', kwargs={'schema_name': obj.schema_name})
            return format_html(
                '<a href="{}" style="background: #417690; color: white; '
                'padding: 4px 8px; text-decoration: none; border-radius: 4px; font-size: 11px;" '
                'target="_blank">👥 Users</a>',
                url
            )
        return "-"

    view_users_link.short_description = "Tenant Users"

    def view_company_link(self, obj):
        """Clickable link to view the public‐facing company detail page."""
        if obj.schema_name:
            url = reverse('company_detail', kwargs={'schema_name': obj.schema_name})
            return format_html(
                '<a href="{}" style="background: #28a745; color: white; '
                'padding: 4px 8px; text-decoration: none; border-radius: 4px; font-size: 11px;" '
                'target="_blank">🏢 View Site</a>',
                url
            )
        return "-"

    view_company_link.short_description = "Company Page"

    # Custom actions to bulk‐activate/deactivate or switch trial statuses:
    actions = [
        'activate_subscription',
        'deactivate_subscription',
        'mark_as_trial',
        'mark_as_active'
    ]

    def activate_subscription(self, request, queryset):
        """Mark selected companies’ subscriptions as active (no change to paid_until)."""
        updated = queryset.update(is_active_subscription=True)
        self.message_user(request, f"{updated} company(ies) marked active.")

    activate_subscription.short_description = "✓ Activate subscription for selected companies"

    def deactivate_subscription(self, request, queryset):
        """Mark selected companies’ subscriptions as inactive (no change to paid_until)."""
        updated = queryset.update(is_active_subscription=False)
        self.message_user(request, f"{updated} company(ies) deactivated.")

    deactivate_subscription.short_description = "✗ Deactivate subscription for selected companies"

    def mark_as_trial(self, request, queryset):
        """Put selected companies back on trial (no change to paid_until)."""
        updated = queryset.update(on_trial=True)
        self.message_user(request, f"{updated} company(ies) set to trial.")

    mark_as_trial.short_description = "Mark selected companies as trial"

    def mark_as_active(self, request, queryset):
        """Remove trial status for selected companies (no change to paid_until)."""
        updated = queryset.update(on_trial=False)
        self.message_user(request, f"{updated} company(ies) removed from trial.")

    mark_as_active.short_description = "Remove trial status for selected companies"


class SecureDomainAdmin(PublicSchemaOnlyMixin, admin.ModelAdmin):
    """
    Only visible/runnable in PUBLIC schema. Show domains and the status
    of their associated Company.
    """
    list_display = ('domain', 'tenant', 'is_primary', 'is_active', 'oidc_callback_approved', 'tls_ready',
                    'tenant_status', 'tenant_users_link')
    list_filter = ('is_primary', 'is_active', 'oidc_callback_approved', 'tls_ready')
    search_fields = ('domain', 'tenant__name')
    # SSO approval and verification change only through the audited actions below.
    readonly_fields = ('ownership_verified_at', 'oidc_callback_approved', 'approved_by', 'disabled_reason')
    actions = ['approve_oidc', 'revoke_oidc', 'disable_domains']

    def save_model(self, request, obj, form, change):
        actor = request.user.get_username()
        repointed = change and ({'domain', 'tenant'} & set(form.changed_data))
        if repointed and obj.oidc_callback_approved:
            # SSO approval belongs to a specific host + tenant; it never carries over.
            obj.oidc_callback_approved = False
        super().save_model(request, obj, form, change)

        def record(action, **detail):
            DomainAuditEvent.objects.create(domain=obj, domain_name=obj.domain, tenant_schema=obj.tenant.schema_name,
                                            action=action, actor=actor, detail={'via': 'admin', **detail})

        if not change:
            record('created')
            return
        if repointed:
            record('oidc_revoked', reason='domain or tenant changed', changed=sorted(repointed))
        if 'is_active' in form.changed_data:
            record('activated' if obj.is_active else 'disabled')

    def _run(self, request, queryset, fn, label, *args):
        done, failed = 0, []
        for domain in queryset.select_related('tenant'):
            try:
                fn(domain, request.user.get_username(), *args)
                done += 1
            except provisioning.ProvisioningError as exc:
                failed.append(f'{domain.domain}: {exc}')
        self.message_user(request, f'{done} domain(s) {label}.' + (f' Skipped: {"; ".join(failed)}' if failed else ''))

    def approve_oidc(self, request, queryset):
        self._run(request, queryset, provisioning.approve_oidc, 'approved for SSO')
    approve_oidc.short_description = 'Approve for Keycloak SSO (register callback in Keycloak too)'

    def revoke_oidc(self, request, queryset):
        self._run(request, queryset, provisioning.revoke_oidc, 'SSO revoked', 'admin action')
    revoke_oidc.short_description = 'Revoke Keycloak SSO'

    def disable_domains(self, request, queryset):
        self._run(request, queryset, provisioning.disable, 'disabled', 'disabled via admin')
    disable_domains.short_description = 'Disable domain (stops routing and SSO)'

    def tenant_status(self, obj):
        """Shows whether the tenant’s subscription is active or expired."""
        if obj.tenant:
            now_date = timezone.now().date()
            expired = (
                (obj.tenant.paid_until and obj.tenant.paid_until < now_date)
                or not obj.tenant.is_active_subscription
            )
            if not expired:
                return format_html('<span style="color: green;">✓ Active</span>')
            else:
                return format_html('<span style="color: red;">✗ Inactive</span>')
        return "-"

    tenant_status.short_description = "Tenant Status"

    def get_queryset(self, request):
        """Only show domains in PUBLIC schema."""
        tenant = get_tenant(request)
        if tenant.schema_name != 'public':
            return Domain.objects.none()
        return super().get_queryset(request)

    def has_module_permission(self, request):
        """Show only if in PUBLIC schema."""
        tenant = get_tenant(request)
        return tenant.schema_name == 'public'

    def tenant_users_link(self, obj):
        """Quick link to view staff users for the tenant of this domain."""
        if obj.tenant and obj.tenant.schema_name:
            url = reverse('company_users', kwargs={'schema_name': obj.tenant.schema_name})
            return format_html(
                '<a href="{}" style="color: #417690; text-decoration: none;" target="_blank">👥 Users</a>',
                url
            )
        return "-"

    tenant_users_link.short_description = "Users"


@admin.register(Plan)
class SecurePlanAdmin(PublicSchemaOnlyMixin, admin.ModelAdmin):
    """
    Only visible/runnable from the PUBLIC schema.
    Tenant users should not see or modify plans via Admin.
    """
    list_display = ('name', 'price', 'billing_period', 'is_active', 'created_at')
    list_filter = ('billing_period', 'is_active')
    search_fields = ('name', 'description')
    readonly_fields = ('created_at',)

    def get_queryset(self, request):
        """Only show Plan entries in public schema."""
        tenant = get_tenant(request)
        if tenant.schema_name != 'public':
            return Plan.objects.none()
        return super().get_queryset(request)

    def has_module_permission(self, request):
        """Only allow this admin in public schema."""
        tenant = get_tenant(request)
        return tenant.schema_name == 'public'


@admin.register(Order)
class SecureOrderAdmin(PublicSchemaOnlyMixin, admin.ModelAdmin):
    """
    Only visible/runnable from the PUBLIC schema.
    Tenant users cannot approve/reject via Admin; only public admin can.
    """
    list_display = ('company', 'plan', 'status', 'total_amount', 'billing_period',
                    'created_at', 'approved_at', 'paid_at')
    list_filter = ('status', 'billing_period', 'created_at', 'approved_at')
    search_fields = ('company__name', 'plan__name')
    readonly_fields = ('created_at', 'approved_at', 'paid_at')

    actions = ['approve_orders', 'reject_orders']

    def get_queryset(self, request):
        """Only show Order entries in public schema."""
        tenant = get_tenant(request)
        if tenant.schema_name != 'public':
            return Order.objects.none()
        return super().get_queryset(request)

    def has_module_permission(self, request):
        """Only allow this admin in public schema."""
        tenant = get_tenant(request)
        return tenant.schema_name == 'public'

    def approve_orders(self, request, queryset):
        """
        Approve selected orders, set paid_at, and update the Company's subscription fields.
        """
        updated = 0
        for order in queryset:
            if order.status != 'pending':
                continue

            order.status = 'approved'
            order.approved_at = timezone.now()
            order.paid_at = timezone.now()
            order.save()

            # Update the associated Company in PUBLIC schema
            company = order.company
            plan = order.plan
            company.current_plan = plan
            company.subscription_start = timezone.now()
            # Compute paid_until based on plan.billing_period
            if plan.billing_period == 'monthly':
                company.paid_until = timezone.now().date() + timezone.timedelta(days=30)
            else:  # 'yearly'
                company.paid_until = timezone.now().date() + timezone.timedelta(days=365)

            company.is_active_subscription = True
            company.on_trial = False
            company.save()
            updated += 1

        self.message_user(request, f"{updated} order(s) approved and subscription updated.")

    approve_orders.short_description = "✓ Approve selected orders"

    def reject_orders(self, request, queryset):
        """Reject selected orders. Rejected orders cannot be re-approved."""
        updated = queryset.filter(status='pending').update(status='rejected')
        self.message_user(request, f"{updated} order(s) rejected.")

    reject_orders.short_description = "✗ Reject selected orders"



@admin.register(DomainAuditEvent)
class DomainAuditEventAdmin(admin.ModelAdmin):
    list_display = ('occurred_at', 'domain_name', 'tenant_schema', 'action', 'actor')
    list_filter = ('action',)
    search_fields = ('domain_name', 'tenant_schema', 'actor')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_module_permission(self, request):
        return get_tenant(request).schema_name == 'public'


# Finally, register Company & Domain only in PUBLIC schema:
admin.site.register(Company, SecureCompanyAdmin)
admin.site.register(Domain, SecureDomainAdmin)
