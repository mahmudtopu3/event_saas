# companies/views.py
from django.shortcuts import render
from django.http import Http404
from .models import Company, Domain
from django_tenants.utils import get_tenant, schema_context
from django.contrib.auth import get_user_model


def public_homepage(request):
    """Portfolio homepage showing all companies using the platform"""
    tenant = get_tenant(request)
    
    # Only show this page in public schema
    if tenant.schema_name != 'public':
        raise Http404("Page not found")
    
    # Get all companies except public schema
    companies = Company.objects.exclude(schema_name='public').order_by('name')
    
    # Get company stats
    total_companies = companies.count()
    active_companies = companies.filter(on_trial=False).count()
    trial_companies = companies.filter(on_trial=True).count()
    
    context = {
        'companies': companies,
        'stats': {
            'total_companies': total_companies,
            'active_companies': active_companies,
            'trial_companies': trial_companies,
        }
    }
    
    return render(request, 'companies/public_homepage.html', context)

def company_detail(request, schema_name):
    """Show individual company details"""
    tenant = get_tenant(request)
    
    if tenant.schema_name != 'public':
        raise Http404("Page not found")
    
    try:
        company = Company.objects.get(schema_name=schema_name)
        primary_domain = Domain.objects.filter(tenant=company, is_primary=True).first()
        
        context = {
            'company': company,
            'primary_domain': primary_domain,
        }
        
        return render(request, 'companies/company_detail.html', context)
        
    except Company.DoesNotExist:
        raise Http404("Company not found")
    

def company_users(request, schema_name):
    """
    List all users in the specified tenant's schema.
    
    Access Control:
    - Public admin (public schema): Can view users from any tenant
    - Tenant admin: Can only view users from their own tenant
    """
    # 1) Determine which tenant the request is currently using:
    request_tenant = get_tenant(request)

    # 2) Load the Company object from the PUBLIC schema (where Company objects are stored):
    company = None
    with schema_context('public'):
        try:
            company = Company.objects.get(schema_name=schema_name)
        except Company.DoesNotExist:
            raise Http404("Company not found")

    # 3) Check permissions:
    if request_tenant.schema_name == "public":
        # Public admin can access any tenant's users
        pass
    elif request_tenant.schema_name == company.schema_name:
        # Tenant admin can access their own users
        pass
    else:
        # Block access if trying to access another tenant's users
        raise Http404("Page not found")

    User = get_user_model()

    # 4) Switch into the target tenant's schema and fetch its users:
    with schema_context(company.schema_name):
        # Fetch all users from the tenant's schema
        users = User.objects.all().order_by("username")
        
        # Add some additional user info for better display
        users_list = []
        for user in users:
            user_data = {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_staff': user.is_staff,
                'is_superuser': user.is_superuser,
                'is_active': user.is_active,
                'last_login': user.last_login,
                'date_joined': user.date_joined,
            }
            users_list.append(user_data)

    # 5) Render with additional context for public admin:
    context = {
        'company': company,
        'users': users_list,
        'is_public_admin': request_tenant.schema_name == "public",
        'total_users': len(users_list),
        'active_users': len([u for u in users_list if u['is_active']]),
        'staff_users': len([u for u in users_list if u['is_staff']]),
        'superusers': len([u for u in users_list if u['is_superuser']]),
    }
    
    return render(request, "companies/company_users.html", context)