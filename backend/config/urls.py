from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from events import urls as events_urls
from organizations import urls as organizations_urls
from ticketing import urls as ticketing_urls

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/auth/', include('users.urls')),
    path('api/v1/', include(events_urls.urlpatterns)),
    path('api/v1/', include(organizations_urls.public_urlpatterns)),
    path('api/v1/', include(ticketing_urls.urlpatterns)),
    path('api/v1/organizer/', include(organizations_urls.urlpatterns)),
    path('api/v1/organizer/', include(events_urls.organizer_urlpatterns)),
    path('api/v1/organizer/', include(ticketing_urls.organizer_urlpatterns)),
    path('api/v1/admin/', include(organizations_urls.admin_urlpatterns)),
    path('api/v1/admin/', include(ticketing_urls.admin_urlpatterns)),
]

if settings.DEBUG:
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
