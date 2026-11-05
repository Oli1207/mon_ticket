from django.urls import path

from . import views

urlpatterns = [
    path('orders/', views.OrderCreateView.as_view(), name='order-create'),
    path('orders/<str:public_id>/', views.OrderDetailView.as_view(), name='order-detail'),
    path('orders/<str:public_id>/pay/', views.OrderPayView.as_view(), name='order-pay'),
    path('orders/<str:public_id>/verify/', views.OrderVerifyView.as_view(), name='order-verify'),
    path('orders/<str:public_id>/cancel/', views.OrderCancelView.as_view(), name='order-cancel'),
    path('me/orders/', views.MyOrdersView.as_view(), name='my-orders'),
    path('payments/paystack/webhook/', views.PaystackWebhookView.as_view(), name='paystack-webhook'),
]

EVENT = 'orgs/<slug:org_slug>/events/<slug:event_slug>/'

organizer_urlpatterns = [
    path('events/<slug:event_slug>/scan/', views.ScanView.as_view(), name='event-scan'),
    path(EVENT + 'ticketing/', views.EventTicketingView.as_view(), name='org-event-ticketing'),
    path(EVENT + 'ticket-types/', views.TicketTypeListCreateView.as_view(), name='org-ticket-types'),
    path(EVENT + 'ticket-types/<int:pk>/', views.TicketTypeDetailView.as_view(), name='org-ticket-type-detail'),
    path(EVENT + 'promo-codes/', views.PromoCodeListCreateView.as_view(), name='org-promo-codes'),
    path(EVENT + 'promo-codes/<int:pk>/', views.PromoCodeDetailView.as_view(), name='org-promo-code-detail'),
    path(EVENT + 'orders/', views.OrganizerOrderListView.as_view(), name='org-event-orders'),
    path(EVENT + 'attendees/', views.AttendeeListView.as_view(), name='org-event-attendees'),
    path(EVENT + 'stats/', views.EventStatsView.as_view(), name='org-event-stats'),
    path(EVENT + 'scans/', views.ScanLogListView.as_view(), name='org-event-scans'),
]

admin_urlpatterns = [
    path('pricing/', views.AdminPricingSettingsView.as_view(), name='admin-pricing'),
    path('pricing/changes/', views.AdminPricingChangeListView.as_view(), name='admin-pricing-changes'),
    path('partner-codes/', views.AdminPartnerCodeListCreateView.as_view(), name='admin-partner-codes'),
    path('partner-codes/<int:pk>/', views.AdminPartnerCodeDetailView.as_view(), name='admin-partner-code-detail'),
    path('events/<slug:slug>/', views.AdminEventPricingView.as_view(), name='admin-event-pricing'),
    path('ticket-types/<int:pk>/commission/', views.AdminTicketTypeCommissionView.as_view(), name='admin-ticket-type-commission'),
]
