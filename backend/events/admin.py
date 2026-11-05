from django.contrib import admin

from .models import Event, EventStaff


class EventStaffInline(admin.TabularInline):
    model = EventStaff
    fk_name = 'event'
    extra = 0
    raw_id_fields = ('user', 'added_by')
    fields = ('user', 'email', 'role', 'gate', 'is_active')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'organization', 'city', 'event_type', 'start_at', 'status', 'is_featured')
    list_filter = ('status', 'is_featured', 'event_type', 'city')
    search_fields = ('title', 'slug', 'venue_name', 'organization__name')
    raw_id_fields = ('organization',)
    readonly_fields = ('slug', 'published_at', 'created_at', 'updated_at')
    date_hierarchy = 'start_at'
    list_select_related = ('organization',)
    inlines = (EventStaffInline,)


@admin.register(EventStaff)
class EventStaffAdmin(admin.ModelAdmin):
    list_display = ('email', 'event', 'role', 'gate', 'is_active', 'created_at')
    list_filter = ('role', 'is_active')
    search_fields = ('email', 'event__title')
    raw_id_fields = ('event', 'user', 'added_by')
    filter_horizontal = ('ticket_types',)
    list_select_related = ('event',)
