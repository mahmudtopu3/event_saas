# events/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.utils import timezone
from django.db import IntegrityError
from .models import Event, Registration

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
        try:
            user_registration = Registration.objects.get(event=event, user=request.user)
            user_registered = True
        except Registration.DoesNotExist:
            pass
    
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
    
    try:
        registration = Registration.objects.create(
            event=event,
            user=request.user,
            status='confirmed'
        )
        messages.success(request, f'Successfully registered for "{event.title}"!')
    except IntegrityError:
        messages.warning(request, 'You are already registered for this event.')
    
    return redirect('events:detail', pk=pk)

@login_required
def cancel_registration(request, pk):
    """Cancel user's registration for an event"""
    event = get_object_or_404(Event, pk=pk)
    
    try:
        registration = Registration.objects.get(event=event, user=request.user)
        registration.delete()
        messages.success(request, f'Registration cancelled for "{event.title}".')
    except Registration.DoesNotExist:
        messages.error(request, 'No registration found for this event.')
    
    return redirect('events:detail', pk=pk)

@login_required
def my_registrations(request):
    """Display user's registrations"""
    registrations = Registration.objects.filter(user=request.user).select_related('event')
    
    context = {
        'registrations': registrations,
    }
    return render(request, 'events/my_registrations.html', context)

def signup(request):
    """User registration view"""
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, 'Account created successfully! You can now log in.')
            return redirect('login')
    else:
        form = UserCreationForm()
    
    return render(request, 'registration/signup.html', {'form': form})

from django.contrib.auth import authenticate, login
from django.contrib.auth.forms import AuthenticationForm

def company_login(request):
    """Company-specific login page with actual login logic"""
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f'Welcome back, {username}!')
                
                # Redirect to next URL or home
                next_url = request.POST.get('next') or request.GET.get('next') or '/'
                return redirect(next_url)
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = AuthenticationForm()
    
    return render(request, 'registration/company_login.html', {'form': form})