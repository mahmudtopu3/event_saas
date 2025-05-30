# companies/views.py
from django.shortcuts import render
from django.http import Http404
from django_tenants.utils import get_tenant
from .models import Company, Domain

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