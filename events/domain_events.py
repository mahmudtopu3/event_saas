# events/domain_events.py
"""
EventOS domain-event envelope, the seam for a future transactional outbox (ADR-SJ-003).

Today `emit` only writes a structured audit line. When the outbox lands, only this
module changes: callers already pass canonical references. EventOS never writes
CareerOS tables and never invents IDs it doesn't have; unknown references are omitted.
"""
from django.utils import timezone

from identity.observability import audit, get_correlation_id

EVENT_CREATED = 'event.created'
EVENT_PUBLISHED = 'event.published'
RSVP_CREATED = 'event.rsvp.created'
REGISTRATION_CONFIRMED = 'event.registration.confirmed'
REGISTRATION_CANCELLED = 'event.registration.cancelled'
CHECKED_IN = 'event.checked_in'
INTERVIEW_BOOKED = 'event.interview.booked'
INTERVIEW_COMPLETED = 'event.interview.completed'
PARTICIPATION_COMPLETED = 'event.participation.completed'
DISPOSITION_RECORDED = 'event.disposition.recorded'


def envelope(event_type, *, tenant=None, event_id=None, person_id=None, actor_person_id=None,
             purpose=None, **extra_refs):
    refs = {
        'event_id': event_id,
        'person_id': person_id,
        'actor_person_id': actor_person_id,
        'tenant_schema': getattr(tenant, 'schema_name', None),
        'tenant_id': getattr(tenant, 'canonical_tenant_id', None),
        'employer_id': getattr(tenant, 'canonical_employer_id', None),
        **extra_refs,
    }
    return {
        'type': event_type,
        'occurred_at': timezone.now().isoformat(),
        'correlation_id': get_correlation_id(),
        'purpose': purpose,
        'refs': {k: v for k, v in refs.items() if v is not None},
    }


def emit(event_type, **kwargs):
    env = envelope(event_type, **kwargs)
    audit('domain_event', type=env['type'], occurred_at=env['occurred_at'], **env['refs'])
    return env
