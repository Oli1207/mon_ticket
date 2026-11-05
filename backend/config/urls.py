from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/auth/', include('users.urls')),
    path('api/v1/', include('events.urls')),
    path('api/v1/', include('ticketing.urls')),
    path('api/v1/organizer/', include('organizations.urls')),
]
