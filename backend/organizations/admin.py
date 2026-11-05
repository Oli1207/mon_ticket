from django.contrib import admin

from .models import AuditLog, Invitation, Membership, Organization


class MembershipInline(admin.TabularInline):
    model = Membership
    fk_name = 'organization'
    extra = 0
    raw_id_fields = ('user', 'invited_by')
    fields = ('user', 'role', 'custom_caps', 'is_active', 'invited_by', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'kind', 'status', 'owner', 'paystack_subaccount_code', 'verified_at', 'created_at')
    list_filter = ('status', 'kind', 'created_at')
    search_fields = ('name', 'slug', 'owner__email', 'contact_email')
    raw_id_fields = ('owner',)
    readonly_fields = ('created_at', 'updated_at')
    inlines = (MembershipInline,)
    list_select_related = ('owner',)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization', 'role', 'is_active', 'created_at')
    list_filter = ('role', 'is_active')
    search_fields = ('user__email', 'organization__name')
    raw_id_fields = ('user', 'organization', 'invited_by')
    list_select_related = ('user', 'organization')


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ('email', 'organization', 'role', 'status', 'invited_by', 'expires_at', 'created_at')
    list_filter = ('status', 'role')
    search_fields = ('email', 'organization__name')
    raw_id_fields = ('organization', 'invited_by')
    exclude = ('token_hash',)
    list_select_related = ('organization', 'invited_by')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'organization', 'actor', 'target_type', 'target_label', 'ip_address')
    list_filter = ('action', 'target_type', 'created_at')
    search_fields = ('action', 'actor__email', 'organization__name', 'target_label')
    list_select_related = ('organization', 'actor')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
