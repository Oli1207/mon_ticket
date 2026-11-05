from django.contrib import admin

from organizations.audit import diff_fields

from .models import (
    EventTicketing,
    Order,
    OrderItem,
    PartnerCode,
    PricingChange,
    PricingSettings,
    PromoCode,
    ScanLog,
    Ticket,
    TicketType,
)
from .audit import log_pricing_changes
from .services import invalidate_event_caches


class PricingAuditAdminMixin:
    """Toute modification d'un champ tarifaire depuis l'admin est tracee dans PricingChange."""

    pricing_fields = ()

    def pricing_target(self, obj):
        raise NotImplementedError

    def save_model(self, request, obj, form, change):
        tracked = [f for f in self.pricing_fields if f in form.changed_data]
        changes = {}
        if tracked:
            previous = type(obj).objects.filter(pk=obj.pk).first() if change else None
            changes = diff_fields(previous, {f: getattr(obj, f) for f in tracked}) if previous else {
                f: {'old': None, 'new': getattr(obj, f)} for f in tracked
            }
        super().save_model(request, obj, form, change)
        if changes:
            log_pricing_changes(request.user, self.pricing_target(obj), changes)


@admin.register(PricingSettings)
class PricingSettingsAdmin(PricingAuditAdminMixin, admin.ModelAdmin):
    pricing_fields = (
        'commission_percent', 'mobile_money_fee_percent', 'card_fee_percent', 'reservation_minutes',
        'max_tickets_per_order', 'max_pending_per_email', 'max_pending_per_ip',
    )
    list_display = ('__str__', 'commission_percent', 'reservation_minutes', 'max_pending_per_email', 'max_pending_per_ip', 'updated_at')

    def pricing_target(self, obj):
        return 'global'

    def has_add_permission(self, request):
        return not PricingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PartnerCode)
class PartnerCodeAdmin(PricingAuditAdminMixin, admin.ModelAdmin):
    pricing_fields = ('commission_percent', 'expires_at', 'max_uses', 'is_active')
    list_display = ('code', 'label', 'commission_percent', 'uses', 'max_uses', 'expires_at', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('code', 'label')
    readonly_fields = ('uses', 'created_by', 'created_at')

    def pricing_target(self, obj):
        return f'partner:{obj.code}'

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(EventTicketing)
class EventTicketingAdmin(PricingAuditAdminMixin, admin.ModelAdmin):
    pricing_fields = ('fees_mode', 'commission_override', 'partner_code')
    list_display = ('event', 'fees_mode', 'commission_override', 'partner_code', 'sales_open')
    list_filter = ('fees_mode', 'sales_open')
    search_fields = ('event__title', 'event__slug')
    raw_id_fields = ('event', 'partner_code')
    list_select_related = ('event', 'partner_code')

    def pricing_target(self, obj):
        return f'event:{obj.event.slug}'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        invalidate_event_caches(obj.event)


@admin.register(TicketType)
class TicketTypeAdmin(PricingAuditAdminMixin, admin.ModelAdmin):
    pricing_fields = ('commission_override',)
    list_display = ('name', 'event', 'price', 'quota', 'sold_count', 'reserved_count', 'commission_override', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'event__title')
    raw_id_fields = ('event',)
    readonly_fields = ('sold_count', 'reserved_count', 'created_at', 'updated_at')
    list_select_related = ('event',)

    def pricing_target(self, obj):
        return f'ticket_type:{obj.pk}'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        invalidate_event_caches(obj.event)


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'event', 'kind', 'value', 'uses', 'max_uses', 'expires_at', 'is_active')
    list_filter = ('kind', 'is_active')
    search_fields = ('code', 'event__title')
    raw_id_fields = ('event',)
    readonly_fields = ('uses', 'created_at')
    list_select_related = ('event',)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ('ticket_type', 'quantity', 'unit_price', 'commission_percent')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('public_id', 'event', 'email', 'status', 'total', 'organizer_net', 'commission_amount', 'created_at', 'paid_at')
    list_filter = ('status', 'fees_mode', 'channel')
    search_fields = ('public_id', 'email', 'full_name', 'paystack_reference')
    date_hierarchy = 'created_at'
    list_select_related = ('event',)
    raw_id_fields = ('event', 'user', 'promo_code')
    exclude = ('access_token',)
    readonly_fields = (
        'public_id', 'event', 'user', 'email', 'phone', 'full_name', 'client_ip', 'channel', 'promo_code',
        'subtotal', 'discount', 'service_fee', 'total', 'organizer_net', 'commission_amount',
        'commission_percent', 'fees_mode', 'paystack_reference', 'expires_at', 'paid_at',
        'confirmation_sent_at', 'created_at', 'updated_at',
    )
    inlines = (OrderItemInline,)
    show_full_result_count = False

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('public_id', 'event', 'ticket_type', 'status', 'holder_name', 'used_at', 'gate')
    list_filter = ('status',)
    search_fields = ('public_id', 'holder_name', 'holder_email', 'order__public_id')
    raw_id_fields = ('order', 'event', 'ticket_type', 'checked_in_by', 'transferred_from')
    readonly_fields = ('public_id', 'created_at')
    list_select_related = ('event', 'ticket_type')
    show_full_result_count = False

    def has_add_permission(self, request):
        return False


@admin.register(ScanLog)
class ScanLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'event', 'result', 'ticket', 'gate', 'scanned_by')
    list_filter = ('result',)
    search_fields = ('ticket__public_id', 'event__title', 'scanned_by__email')
    list_select_related = ('event', 'ticket', 'scanned_by')
    show_full_result_count = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PricingChange)
class PricingChangeAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'changed_by', 'target', 'field', 'old_value', 'new_value')
    list_filter = ('field',)
    search_fields = ('target', 'changed_by__email')
    list_select_related = ('changed_by',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
