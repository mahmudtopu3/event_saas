# companies/views.py
from django.shortcuts import render, redirect
from django.http import Http404
from .models import Company, Domain, Order, Plan
from django_tenants.utils import get_tenant, schema_context
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.utils import timezone


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


def view_plans(request):
    """
    Tenant‐schema: List all active Plans so that a tenant may choose one.
    Also shows the tenant’s current subscription status.
    """
    tenant = get_tenant(request)
    if tenant.schema_name == 'public':
        # Public schema should not see this page
        raise Http404("Page not found")

    # Fetch Company record from PUBLIC schema
    with schema_context('public'):
        try:
            company = Company.objects.get(schema_name=tenant.schema_name)
        except Company.DoesNotExist:
            raise Http404("Company not found in public schema")

        plans = Plan.objects.filter(is_active=True)

    return render(request, 'companies/plans.html', {
        'company': company,
        'plans': plans,
    })


@login_required
def tenant_order(request):
    """
    Tenant‐schema: POST to place a new Order for the current Plan.
    If method=GET, redirect to view_plans.
    """
    tenant = get_tenant(request)
    if tenant.schema_name == 'public':
        raise Http404("Page not found")

    if request.method != 'POST':
        return redirect('view_plans')

    plan_id = request.POST.get('plan_id')
    if not plan_id:
        return redirect('view_plans')

    # In PUBLIC schema, create a new Order tied to this Company
    with schema_context('public'):
        try:
            company = Company.objects.get(schema_name=tenant.schema_name)
            plan = Plan.objects.get(pk=plan_id, is_active=True)
        except (Company.DoesNotExist, Plan.DoesNotExist):
            raise Http404("Invalid company or plan")

        total = plan.price
        billing = plan.billing_period

        # Create the Order (status='pending' by default)
        Order.objects.create(
            company=company,
            plan=plan,
            total_amount=total,
            billing_period=billing,
        )

    return redirect('order_thankyou')


def order_thankyou(request):
    """Simple thank‐you page after placing an order."""
    tenant = get_tenant(request)
    if tenant.schema_name == 'public':
        raise Http404("Page not found")

    return render(request, 'companies/order_thankyou.html')


@login_required
def view_orders(request):
    """
    Tenant‐schema: Show all Orders for this tenant (Company) and their statuses.
    """
    tenant = get_tenant(request)
    if tenant.schema_name == 'public':
        raise Http404("Page not found")

    with schema_context('public'):
        try:
            company = Company.objects.get(schema_name=tenant.schema_name)
        except Company.DoesNotExist:
            raise Http404("Company not found")

        orders = Order.objects.filter(company=company).order_by('-created_at')

    return render(request, 'companies/tenant_orders.html', {
        'company': company,
        'orders': orders,
    })


def subscription_check(request):
    """
    This view handles inactive subscription display.
    Shows appropriate content based on user authentication status.
    """
    tenant = get_tenant(request)
    next_url = request.GET.get('next', '/')
    
    # Get company info
    try:
        with schema_context('public'):
            company = Company.objects.get(schema_name=tenant.schema_name)
    except Company.DoesNotExist:
        return render(request, 'subscriptions/company_not_found.html', status=404)

    # Check if subscription is actually active now
    now_date = timezone.now().date()
    is_active = (
        company.is_active_subscription and
        (not company.paid_until or company.paid_until >= now_date)
    )

    # If subscription became active, redirect to original destination
    if is_active:
        return redirect(next_url)

    # Get user's orders if authenticated
    orders = None
    if request.user.is_authenticated:
        with schema_context('public'):
            orders = Order.objects.filter(company=company).order_by('-created_at')[:5]

    context = {
        'company': company,
        'tenant': tenant,
        'next_url': next_url,
        'orders': orders,
        'login_url': '/accounts/login/',
        'plans_url': '/plans/',
        'order_url': '/orders/',
        'is_authenticated': request.user.is_authenticated,
    }

    return render(request, 'subscriptions/inactive.html', context)