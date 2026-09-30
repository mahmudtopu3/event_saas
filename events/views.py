# events/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.db import IntegrityError, transaction
from identity import conf as auth_conf
from identity.observability import audit

from . import domain_events
from .forms import LoginForm, SignupForm
from .models import Event, Registration
from .registrants import create_kwargs, owner_q


def _safe_next_url(request, default='/'):
    """Return the `next` URL from POST/GET if it is safe to redirect to, else `default`."""
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return default

def _auth_login_url(request, signup=False):
    from urllib.parse import urlencode

    params = {'next': _safe_next_url(request)}
    if signup:
        params['signup'] = '1'
    return f"/auth/login/?{urlencode(params)}"


def event_list(request):
    """Display list of published events"""
    events = Event.objects.filter(status='published').order_by('start_date')
    upcoming_events = events.filter(start_date__gte=timezone.now())
    past_events = events.filter(start_date__lt=timezone.now())
    
    context = {
        'upcoming_events': upcoming_events,
        'past_events': past_events,
    }
    return render(request, 'events/event_list.html', context)

def event_detail(request, pk):
    """Display event details"""
    event = get_object_or_404(Event, pk=pk, status='published')
    user_registered = False
    user_registration = None
    
    if request.user.is_authenticated:
        user_registration = Registration.objects.filter(owner_q(request), event=event).first()
        user_registered = user_registration is not None
    
    context = {
        'event': event,
        'user_registered': user_registered,
        'user_registration': user_registration,
    }
    return render(request, 'events/event_detail.html', context)

@login_required
def register_for_event(request, pk):
    """Register user for an event"""
    event = get_object_or_404(Event, pk=pk, status='published')
    
    if not event.is_registration_open:
        messages.error(request, 'Registration is closed for this event.')
        return redirect('events:detail', pk=pk)
    
    if Registration.objects.filter(owner_q(request), event=event).exists():
        # Covers the same human across a legacy row and a Keycloak login, which the
        # (event,user) / (event,person_id) constraints can't see individually.
        messages.warning(request, 'You are already registered for this event.')
        return redirect('events:detail', pk=pk)

    try:
        # Savepoint so a duplicate doesn't poison an enclosing transaction.
        with transaction.atomic():
            registration = Registration.objects.create(
                event=event,
                status='confirmed',
                **create_kwargs(request),
            )
        audit('registration.created', event_id=event.pk, person_id=registration.person_id,
              tenant=request.tenant.schema_name)
        domain_events.emit(domain_events.REGISTRATION_CONFIRMED, event_id=event.pk,
                           person_id=registration.person_id, tenant=request.tenant)
        messages.success(request, f'Successfully registered for "{event.title}"!')
    except IntegrityError:
        messages.warning(request, 'You are already registered for this event.')
    
    return redirect('events:detail', pk=pk)

@login_required
def cancel_registration(request, pk):
    """Cancel user's registration for an event"""
    event = get_object_or_404(Event, pk=pk)
    
    registration = Registration.objects.filter(owner_q(request), event=event).first()
    if registration is not None:
        person_id = registration.person_id
        registration.delete()
        audit('registration.cancelled', event_id=event.pk, person_id=person_id,
              tenant=request.tenant.schema_name)
        domain_events.emit(domain_events.REGISTRATION_CANCELLED, event_id=event.pk,
                           person_id=person_id, tenant=request.tenant)
        messages.success(request, f'Registration cancelled for "{event.title}".')
    else:
        messages.error(request, 'No registration found for this event.')
    
    return redirect('events:detail', pk=pk)

@login_required
def my_registrations(request):
    """Display user's registrations"""
    registrations = Registration.objects.filter(owner_q(request)).select_related('event')
    
    context = {
        'registrations': registrations,
    }
    return render(request, 'events/my_registrations.html', context)

def signup(request):
    """Tenant user registration (attendees / job seekers)."""
    if request.user.is_authenticated:
        return redirect(_safe_next_url(request))
    if not auth_conf.legacy_signup_enabled():
        # Keycloak mode: accounts are created centrally at Skill.jobs, never as an EventOS password.
        return redirect(_auth_login_url(request, signup=True))

    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Welcome, {user.first_name or user.username}! Your account has been created.')
            return redirect(_safe_next_url(request))
    else:
        form = SignupForm()

    return render(request, 'registration/signup.html', {
        'form': form,
        'next': request.GET.get('next', ''),
    })


def company_login(request):
    """Company-specific login page with actual login logic"""
    if request.user.is_authenticated:
        return redirect(_safe_next_url(request))
    if not auth_conf.legacy_login_enabled():
        return redirect(_auth_login_url(request))

    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f'Welcome back, {user.first_name or user.username}!')
            return redirect(_safe_next_url(request))
    else:
        form = LoginForm(request)

    return render(request, 'registration/company_login.html', {
        'form': form,
        'next': request.GET.get('next', ''),
    })
