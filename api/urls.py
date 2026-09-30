from django.urls import path

from api.views import events_api, my_registrations_api, tenant_info_api



urlpatterns = [
   path('tenant-info/', tenant_info_api, name='tenant-info'),
   path('events/', events_api, name='events-api'),
   path('my-registrations/', my_registrations_api, name='my-registrations-api'),
    
]



