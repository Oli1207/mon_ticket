from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    ordering = ('-date_joined',)
    list_display = ('email', 'first_name', 'last_name', 'city', 'is_organizer', 'is_staff', 'is_active', 'date_joined')
    list_filter = ('is_organizer', 'is_staff', 'is_superuser', 'is_active', 'city')
    search_fields = ('email', 'first_name', 'last_name', 'phone')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Informations', {'fields': ('first_name', 'last_name', 'phone', 'city', 'avatar', 'is_organizer')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('email', 'password1', 'password2')}),
    )
