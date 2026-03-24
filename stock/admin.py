from django.contrib import admin
from .models import Equipment, Discharge, DischargeItem, FieldReport, ReturnedItem, StockMovement


class DischargeItemInline(admin.TabularInline):
    model = DischargeItem
    extra = 0
    readonly_fields = ('equipment', 'quantity')


class ReturnedItemInline(admin.TabularInline):
    model = ReturnedItem
    extra = 0
    readonly_fields = ('equipment', 'quantity_returned', 'condition')


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'reference', 'quantity', 'defective_quantity', 'is_low_stock', 'updated_at')
    search_fields = ('name', 'reference')
    list_filter = ('updated_at',)
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(boolean=True, description='Stock faible')
    def is_low_stock(self, obj):
        return obj.is_low_stock


@admin.register(Discharge)
class DischargeAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'destination', 'date', 'status')
    list_filter = ('status', 'date')
    search_fields = ('destination', 'user__username')
    inlines = [DischargeItemInline]
    readonly_fields = ('created_at',)


@admin.register(FieldReport)
class FieldReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'discharge', 'date', 'created_at')
    search_fields = ('discharge__destination',)
    inlines = [ReturnedItemInline]
    readonly_fields = ('created_at',)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('equipment', 'movement_type', 'quantity', 'date', 'note')
    list_filter = ('movement_type', 'date')
    search_fields = ('equipment__name', 'note')
    readonly_fields = ('date',)
