# events/urls.py
from django.urls import path
from . import views

app_name = 'events'

urlpatterns = [
    path('', views.event_list, name='list'),
    path('event/<int:pk>/', views.event_detail, name='detail'),
    path('event/<int:pk>/register/', views.register_for_event, name='register'),
    path('event/<int:pk>/cancel/', views.cancel_registration, name='cancel'),
    path('my-registrations/', views.my_registrations, name='my_registrations'),
]