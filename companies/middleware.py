# companies/middleware.py

from django.shortcuts import render
from django_tenants.utils import get_tenant, schema_context
from .models import Company

class SubscriptionMiddleware:
    """
    Block any request to a tenant whose subscription is inactive.
    If is_active_subscription == False, we immediately show an "inactive" page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1) See which tenant/schema is being used
        tenant = get_tenant(request)

        # 2) If it's not the public schema, enforce subscription check:
        if tenant.schema_name != 'public':
            try:
                # We need to check the Company in the public schema
                with schema_context('public'):
                    company = Company.objects.get(schema_name=tenant.schema_name)
            except Company.DoesNotExist:
                # If somehow the schema/company doesn't exist, 404:
                return render(request, 'subscriptions/company_not_found.html', status=404)

            # If subscription is inactive, render a simple "inactive" page
            if not company.is_active_subscription:
                return render(request, 'subscriptions/inactive.html', {
                    'company': company,
                    'tenant': tenant
                })

        # 3) Otherwise, either we're in "public" or the tenant is active → normal flow
        response = self.get_response(request)
        return response