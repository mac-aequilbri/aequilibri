from django.urls import path
from . import views

app_name = 'uc3'

urlpatterns = [
    # ── Core ──────────────────────────────────────────────────────────────────
    path('',                              views.dashboard,      name='dashboard'),
    path('tenant/<int:tenant_id>/',       views.select_tenant,  name='select_tenant'),
    path('projects/',                     views.project_list,   name='project_list'),
    path('projects/new/',                 views.project_create, name='project_create'),
    path('projects/<int:pk>/',            views.project_detail, name='project_detail'),
    path('actions/',                      views.action_list,    name='action_list'),
    path('risks/',                        views.risk_list,      name='risk_list'),
    path('budget/',                       views.budget_view,    name='budget'),
    path('ai-chat/',                      views.ai_chat,        name='ai_chat'),
    path('exec-log/',                     views.exec_log,       name='exec_log'),

    # ── Feature 4: Variation Orders ───────────────────────────────────────────
    path('variations/',                   views.variation_list,   name='variation_list'),
    path('variations/new/',               views.variation_create, name='variation_create'),
    path('variations/<int:pk>/',          views.variation_detail, name='variation_detail'),

    # ── Feature 2: Client Portal ──────────────────────────────────────────────
    path('portal/',                       views.portal_list,    name='portal_list'),
    path('projects/<int:pk>/portal/',     views.portal_manage,  name='portal_manage'),
    path('portal/<str:token>/',           views.portal_public,  name='portal_public'),

    # ── Feature 1: Accounting Sync ────────────────────────────────────────────
    path('accounting/',                   views.accounting_sync, name='accounting_sync'),

    # ── Feature 5: Weekly Reports ─────────────────────────────────────────────
    path('reports/',                      views.report_list,     name='report_list'),
    path('reports/generate/',             views.report_generate, name='report_generate'),
    path('reports/<int:pk>/',             views.report_detail,   name='report_detail'),

    # ── Feature 3: Cashflow Planner ───────────────────────────────────────────
    path('cashflow-planner/',             views.cashflow_planner, name='cashflow_planner'),

    # ── Feature 6: Budget Analytics ───────────────────────────────────────────
    path('budget-analytics/',             views.budget_analytics, name='budget_analytics'),

    # ── Feature 7: Document Intelligence ─────────────────────────────────────
    path('documents/',                    views.document_list,    name='document_list'),
    path('documents/<int:pk>/analyze/',   views.document_analyze, name='document_analyze'),

    # ── Feature 8: Delay Cascade ──────────────────────────────────────────────
    path('delay-cascade/',                views.delay_cascade,    name='delay_cascade'),

    # ── Feature 9: Meeting Minutes ────────────────────────────────────────────
    path('meeting-minutes/',              views.meeting_minutes_list,   name='meeting_minutes_list'),
    path('meeting-minutes/new/',          views.meeting_minutes_create, name='meeting_minutes_create'),
    path('meeting-minutes/<int:pk>/',     views.meeting_minutes_detail, name='meeting_minutes_detail'),

    # ── Feature 10: Risk Escalation ───────────────────────────────────────────
    path('risk-escalation/',              views.risk_escalation, name='risk_escalation'),
]
