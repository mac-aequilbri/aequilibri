from django.contrib import admin
from .models import (Tenant, Project, Phase, ActionItem, Risk, Budget,
                     Vendor, Decision, Document, Cashflow, TenantUser, ExecutionLog, ChatMessage)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ['name', 'org_id', 'is_active', 'created_at']
    list_editable = ['is_active']

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'tenant', 'client', 'status', 'health_score', 'start_date', 'end_date']
    list_filter  = ['tenant', 'status']
    list_editable = ['status', 'health_score']

@admin.register(Phase)
class PhaseAdmin(admin.ModelAdmin):
    list_display = ['name', 'project', 'status', 'completion_pct', 'is_ai_draft']
    list_editable = ['completion_pct', 'status']

@admin.register(ActionItem)
class ActionItemAdmin(admin.ModelAdmin):
    list_display = ['title', 'project', 'owner', 'due_date', 'priority', 'status', 'created_by_ai']
    list_filter  = ['status', 'priority', 'created_by_ai']
    list_editable = ['status']

@admin.register(Risk)
class RiskAdmin(admin.ModelAdmin):
    list_display = ['description', 'project', 'likelihood', 'impact', 'risk_score', 'risk_level', 'status']
    list_filter  = ['status']

@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ['description', 'project', 'estimated', 'actual', 'variance', 'variance_pct']

@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ['name', 'tenant', 'category', 'rating', 'is_active']
    list_editable = ['rating']

@admin.register(Decision)
class DecisionAdmin(admin.ModelAdmin):
    list_display = ['description', 'project', 'status', 'is_ai_draft', 'drafted_by']
    list_editable = ['status']

admin.site.register(Document)
admin.site.register(Cashflow)
admin.site.register(TenantUser)
admin.site.register(ExecutionLog)
admin.site.register(ChatMessage)
