from django.urls import path

from . import views

urlpatterns = [
    path('events/', views.PublicEventListView.as_view(), name='public-event-list'),
    path('events/<slug:slug>/', views.PublicEventDetailView.as_view(), name='public-event-detail'),
]

EVENT = 'orgs/<slug:org_slug>/events/<slug:event_slug>/'

organizer_urlpatterns = [
    path('orgs/<slug:org_slug>/events/', views.OrganizerEventListCreateView.as_view(), name='org-event-list'),
    path(EVENT, views.OrganizerEventDetailView.as_view(), name='org-event-detail'),
    path(EVENT + 'staff/', views.EventStaffListCreateView.as_view(), name='org-event-staff'),
    path(EVENT + 'staff/<int:pk>/', views.EventStaffDetailView.as_view(), name='org-event-staff-detail'),
]
