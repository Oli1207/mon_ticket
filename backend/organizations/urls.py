from django.urls import path

from . import views

ORG = 'orgs/<slug:org_slug>/'

urlpatterns = [
    path('capabilities/', views.CapabilityCatalogView.as_view(), name='org-capabilities'),
    path('orgs/', views.MyOrganizationsView.as_view(), name='org-list'),
    path(ORG, views.OrganizationDetailView.as_view(), name='org-detail'),
    path(ORG + 'members/', views.MemberListView.as_view(), name='org-members'),
    path(ORG + 'members/<int:pk>/', views.MemberDetailView.as_view(), name='org-member-detail'),
    path(ORG + 'invitations/', views.InvitationListCreateView.as_view(), name='org-invitations'),
    path(ORG + 'invitations/<int:pk>/revoke/', views.InvitationRevokeView.as_view(), name='org-invitation-revoke'),
    path(ORG + 'audit-logs/', views.AuditLogListView.as_view(), name='org-audit-logs'),
    path('invitations/accept/', views.AcceptInvitationView.as_view(), name='invitation-accept'),
]

public_urlpatterns = [
    path('organizations/<slug:slug>/', views.PublicOrganizationView.as_view(), name='public-org-detail'),
]

admin_urlpatterns = [
    path('organizations/', views.AdminOrganizationListView.as_view(), name='admin-org-list'),
    path('organizations/<slug:slug>/', views.AdminOrganizationModerationView.as_view(), name='admin-org-moderate'),
]
