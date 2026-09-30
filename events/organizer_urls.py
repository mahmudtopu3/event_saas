from django.urls import path

from . import organizer

app_name = 'organizer'

urlpatterns = [
    path('', organizer.dashboard, name='dashboard'),
    path('events/new/', organizer.event_create, name='event_create'),
    path('events/<int:pk>/edit/', organizer.event_edit, name='event_edit'),
    path('events/<int:pk>/publish/', organizer.event_publish, name='event_publish'),
    path('events/<int:pk>/attendees/', organizer.event_attendees, name='event_attendees'),
]
