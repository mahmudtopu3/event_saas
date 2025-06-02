# companies/middleware.py

import re
from django.shortcuts import render, redirect
from django_tenants.utils import get_tenant, schema_context
from .models import Company
from django.utils import timezone
from django.urls import reverse


class SubscriptionMiddleware:
    """
    Simplified middleware that only:
    1) Blocks /admin/ for all tenants
    2) Redirects inactive subscriptions to subscription_check view
    """

    def __init__(self, get_response):
        self.get_response = get_response

        # URIs that are NEVER allowed for any tenant
        self.tenant_blocked_patterns = [
            r'^/admin/',             # Block Django admin for tenants
        ]

        # URIs that should bypass subscription check
        self.bypass_patterns = [
            r'^/accounts/',          # Authentication URLs
            r'^/login/',             # Login
            r'^/logout/',            # Logout
            r'^/subscription-check/', # Our subscription check view
            r'^/plans/',             # Plans view
            r'^/order/',             # Order URLs
            r'^/orders/',             # Order URLs
            r'^/static/',            # Static files
            r'^/media/',             # Media files
            r'^/favicon\.ico$',      # Favicon
        ]

        # Compile regex patterns
        self.blocked_regex = [re.compile(p) for p in self.tenant_blocked_patterns]
        self.bypass_regex = [re.compile(p) for p in self.bypass_patterns]

    def is_tenant_path_blocked(self, path: str) -> bool:
        """Return True if path should be blocked for tenants."""
        return any(rx.match(path) for rx in self.blocked_regex)

    def should_bypass_check(self, path: str) -> bool:
        """Return True if path should bypass subscription check."""
        return any(rx.match(path) for rx in self.bypass_regex)

    def __call__(self, request):
        tenant = get_tenant(request)
        path = request.path

        # Only process tenant requests (not public schema)
        if tenant.schema_name != 'public':
            
            # # 1) Block /admin/ for ALL tenants
            # if self.is_tenant_path_blocked(path):
            #     return render(request, 'subscriptions/admin_blocked.html', {
            #         'tenant': tenant,
            #     }, status=403)

            # 2) Check subscription status (unless bypassing)
            if not self.should_bypass_check(path):
                try:
                    with schema_context('public'):
                        company = Company.objects.get(schema_name=tenant.schema_name)
                except Company.DoesNotExist:
                    return render(request, 'subscriptions/company_not_found.html', status=404)

                # Check if subscription is active
                now_date = timezone.now().date()
                expired = (
                    not company.is_active_subscription or
                    (company.paid_until and company.paid_until < now_date)
                )

                if expired:
                    # Redirect to subscription check view with next parameter
                    return redirect(f'/subscription-check/?next={path}')

        # Normal flow
        return self.get_response(request)