# Add to your eventsaas/tenant_urls.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django_tenants.utils import get_tenant
from events.models import Event

@api_view(['GET'])
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

