from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.views import TokenBlacklistView

from . import views

urlpatterns = [
    path('register/', views.RegisterView.as_view(), name='auth-register'),
    path('login/', views.LoginView.as_view(), name='auth-login'),
    path('refresh/', views.RefreshView.as_view(), name='auth-refresh'),
    path('logout/', TokenBlacklistView.as_view(permission_classes=(AllowAny,)), name='auth-logout'),
    path('me/', views.ProfileView.as_view(), name='auth-me'),
    path('me/password/', views.ChangePasswordView.as_view(), name='auth-change-password'),
]
