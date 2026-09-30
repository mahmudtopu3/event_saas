# Add to your eventsaas/tenant_urls.py
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django_tenants.utils import get_tenant
from events.models import Event, Registration
from events.registrants import owner_q

@api_view(['GET'])
@permission_classes([AllowAny])  # intentionally public
def tenant_info_api(request):
    tenant = get_tenant(request)
    if tenant.schema_name == 'public':
        return Response({'error': 'Public schema'}, status=400)
    
    return Response({
        'id': tenant.id,
        'name': tenant.name,
        'description': tenant.description,
        'logo': tenant.logo.url if tenant.logo else None,
 
        'schema_name': tenant.schema_name,
    })

@api_view(['GET'])
@permission_classes([AllowAny])  # intentionally public
def events_api(request):
    events = Event.objects.filter(status='published').order_by('start_date')
    data = []
    for event in events:
        data.append({
            'id': event.id,
            'title': event.title,
            'description': event.description,
            'start_date': event.start_date.isoformat(),
            'end_date': event.end_date.isoformat(),
            'location': event.location,
            'status': event.status,
            'max_attendees': event.max_attendees,
            'registrations_count': event.registrations.count(),
        })
    return Response(data)



@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_registrations_api(request):
    """
    The caller's registrations in *this* tenant. Accepts a Keycloak bearer token
    (Principal → PersonID) or an existing session. The tenant comes from the Host,
    and rows are filtered by the caller's identity, so there's nothing to tamper with.
    """
    registrations = Registration.objects.filter(owner_q(request)).select_related('event')
    return Response([{
        'event_id': r.event_id,
        'event_title': r.event.title,
        'status': r.status,
        'registered_at': r.registered_at.isoformat(),
    } for r in registrations])
