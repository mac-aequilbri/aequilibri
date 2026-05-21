from django.contrib import admin
from .models import (Quote, QuoteItem, Contact, RateCard, RoofPolygon, ExecutionLog,
                     Vendor, VendorMaterialPrice, PurchaseOrder, PurchaseOrderItem,
                     PriceCheckLog)


class QuoteItemInline(admin.TabularInline):
    model = QuoteItem
    extra = 0


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ['ref_number', 'property_address', 'contact', 'material',
                    'pitch_type', 'adjusted_area_sqm', 'total_inc_gst', 'status', 'created_at']
    list_filter  = ['status', 'material', 'pitch_type']
    search_fields = ['ref_number', 'property_address']
    inlines      = [QuoteItemInline]


@admin.register(RateCard)
class RateCardAdmin(admin.ModelAdmin):
    list_display = ['material', 'pitch_type', 'rate_ex_gst', 'rate_inc_gst', 'unit', 'is_active']
    list_filter  = ['material', 'is_active']
    list_editable = ['rate_ex_gst', 'is_active']


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ['name', 'company', 'email', 'phone']
    search_fields = ['name', 'company', 'email']


@admin.register(ExecutionLog)
class ExecutionLogAdmin(admin.ModelAdmin):
    list_display  = ['created_at', 'tool_name', 'status', 'duration_ms', 'quote']
    list_filter   = ['status', 'tool_name']
    readonly_fields = ['created_at']

admin.site.register(RoofPolygon)


class VendorMaterialPriceInline(admin.TabularInline):
    model = VendorMaterialPrice
    extra = 0
    fields = ['material', 'item_code', 'description', 'unit', 'unit_price_ex_gst', 'lead_days', 'is_available']


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display  = ['name', 'suburb', 'state', 'contact_email', 'contact_phone', 'is_preferred', 'is_active']
    list_filter   = ['is_preferred', 'is_active', 'state']
    list_editable = ['is_preferred', 'is_active']
    search_fields = ['name', 'suburb']
    inlines       = [VendorMaterialPriceInline]


@admin.register(VendorMaterialPrice)
class VendorMaterialPriceAdmin(admin.ModelAdmin):
    list_display  = ['vendor', 'material', 'item_code', 'unit_price_ex_gst', 'unit', 'lead_days', 'is_available']
    list_filter   = ['material', 'is_available', 'vendor']
    list_editable = ['unit_price_ex_gst', 'is_available']
    search_fields = ['vendor__name', 'item_code', 'description']


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display  = ['po_number', 'vendor', 'quote', 'status', 'total_inc_gst', 'created_at']
    list_filter   = ['status', 'vendor']
    search_fields = ['po_number', 'vendor__name']
    inlines       = [PurchaseOrderItemInline]
    readonly_fields = ['po_number', 'created_at', 'updated_at']


@admin.register(PriceCheckLog)
class PriceCheckLogAdmin(admin.ModelAdmin):
    list_display  = ['run_at', 'status', 'vendors_checked', 'prices_updated',
                     'prices_unchanged', 'errors']
    list_filter   = ['status']
    readonly_fields = ['run_at', 'status', 'vendors_checked', 'prices_updated',
                       'prices_unchanged', 'errors', 'summary', 'raw_log']

    def has_add_permission(self, request):
        return False  # logs are created by the management command only
