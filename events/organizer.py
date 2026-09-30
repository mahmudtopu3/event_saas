# events/organizer.py
"""
Organizer workspace for Keycloak-authenticated employer staff.

Access = identity.decorators.organizer_required (PersonID + canonical membership +
EVENTOS entitlement + tenant↔employer link + EventOS permission), evaluated per request.
Legacy local staff keep using the tenant Django admin during the migration.
"""
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from identity.decorators import organizer_required, strong_auth_required
from identity.observability import audit
from identity.profile_client import display_names

from . import domain_events
from .forms import BootstrapFormMixin
from .models import Event
from .registrants import creator_kwargs


class EventForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Event
        fields = ['title', 'description', 'location', 'start_date', 'end_date',
                  'max_attendees', 'registration_deadline']
        widgets = {
            'start_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'end_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'registration_deadline': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }

    def clean(self):
        data = super().clean()
        if data.get('start_date') and data.get('end_date') and data['end_date'] <= data['start_date']:
            self.add_error('end_date', 'End must be after start.')
        return data


@organizer_required
def dashboard(request):
    events = Event.objects.all().order_by('-start_date')
    return render(request, 'events/organizer/dashboard.html', {'events': events})


@organizer_required
def event_create(request):
    form = EventForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        event = form.save(commit=False)
        event.status = 'draft'
        for field, value in creator_kwargs(request).items():
            setattr(event, field, value)
        event.save()
        audit('event.created', event_id=event.pk, person_id=request.principal.person_id,
              tenant=request.tenant.schema_name)
        domain_events.emit(domain_events.EVENT_CREATED, event_id=event.pk, tenant=request.tenant,
                           actor_person_id=request.principal.person_id)
        messages.success(request, 'Draft event created.')
        return redirect('organizer:dashboard')
    return render(request, 'events/organizer/event_form.html', {'form': form, 'event': None})


@organizer_required
def event_edit(request, pk):
    event = get_object_or_404(Event, pk=pk)
    form = EventForm(request.POST or None, instance=event)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Event updated.')
        return redirect('organizer:dashboard')
    return render(request, 'events/organizer/event_form.html', {'form': form, 'event': event})


@require_POST
@organizer_required
@strong_auth_required
def event_publish(request, pk):
    event = get_object_or_404(Event, pk=pk)
    event.status = 'published'
    event.save(update_fields=['status', 'updated_at'])
    domain_events.emit(domain_events.EVENT_PUBLISHED, event_id=event.pk, tenant=request.tenant,
                       actor_person_id=request.principal.person_id)
    messages.success(request, f'"{event.title}" is now published.')
    return redirect('organizer:dashboard')


@organizer_required
def event_attendees(request, pk):
    event = get_object_or_404(Event, pk=pk)
    registrations = list(event.registrations.select_related('user').order_by('registered_at'))
    # One bulk call to the canonical Person API for the whole page (no N+1).
    names = display_names([r.person_id for r in registrations], tenant_schema=request.tenant.schema_name)
    rows = [{
        'registration': r,
        'name': names.get(r.person_id) if r.person_id is not None else (r.user.username if r.user_id else '-'),
        'identity': 'Skill.jobs' if r.person_id is not None else 'Legacy account',
    } for r in registrations]
    return render(request, 'events/organizer/attendees.html', {'event': event, 'rows': rows})
