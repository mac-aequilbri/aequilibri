from django.contrib import admin
from .models import (ActionHub, Budget, Cashflow, ChangeLog, Decision, Document,
                     Procurement, ProjectPhase, ProjectPlan, RoomMatrix, Vendor,
                     ExecutionLog, Hypothesis, LearningRule,
                     RefBudget, RefCategory, RefZone, Metadata, ChatSession, ChatMessage)


@admin.register(LearningRule)
class LearningRuleAdmin(admin.ModelAdmin):
    list_display = ['rule_code', 'description', 'category', 'is_active', 'cannot_override']
    list_filter  = ['is_active', 'cannot_override', 'category']
    list_editable = ['is_active']

@admin.register(ActionHub)
class ActionHubAdmin(admin.ModelAdmin):
    list_display = ['action', 'owner', 'due_date', 'status', 'priority']
    list_filter  = ['status', 'priority']
    list_editable = ['status']

@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ['phase', 'description', 'estimated', 'actual', 'variance', 'variance_pct']
    list_filter  = ['phase']

@admin.register(ProjectPhase)
class ProjectPhaseAdmin(admin.ModelAdmin):
    list_display = ['order', 'name', 'status', 'completion_pct', 'start_date', 'end_date']
    list_editable = ['completion_pct', 'status']

@admin.register(Hypothesis)
class HypothesisAdmin(admin.ModelAdmin):
    list_display = ['description', 'status', 'created_at']
    list_filter  = ['status']

@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'contact', 'rating', 'is_active']
    list_editable = ['rating', 'is_active']

admin.site.register(RefBudget)
admin.site.register(RefCategory)
admin.site.register(RefZone)
admin.site.register(Metadata)
admin.site.register(Cashflow)
admin.site.register(ChangeLog)
admin.site.register(Decision)
admin.site.register(Document)
admin.site.register(Procurement)
admin.site.register(ProjectPlan)
admin.site.register(RoomMatrix)
admin.site.register(ExecutionLog)
admin.site.register(ChatSession)
admin.site.register(ChatMessage)
