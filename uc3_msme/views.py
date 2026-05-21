"""UC3 MSME Project Coordinator — Views"""
import json
import re
from datetime import date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone

from .models import (Tenant, Project, Phase, ActionItem, Risk, Budget,
                     Vendor, Decision, Document, Cashflow, TenantUser,
                     ExecutionLog, ChatMessage, VariationOrder, ClientPortalToken,
                     AccountingConnection, WeeklyReport, MeetingMinutes)
from core.claude_client import call_claude


# ─── Tenant selection helper ──────────────────────────────────────────────────

def get_active_tenant(request):
    """Get or default to the first tenant (demo: no real auth)."""
    tenant_id = request.session.get('active_tenant_id')
    if tenant_id:
        try:
            return Tenant.objects.get(id=tenant_id)
        except Tenant.DoesNotExist:
            pass
    tenant = Tenant.objects.filter(is_active=True).first()
    if tenant:
        request.session['active_tenant_id'] = tenant.id
    return tenant


def select_tenant(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id, is_active=True)
    request.session['active_tenant_id'] = tenant.id
    messages.success(request, f'Switched to tenant: {tenant.name}')
    return redirect('uc3:dashboard')


# ─── Dashboard ────────────────────────────────────────────────────────────────

def dashboard(request):
    tenant    = get_active_tenant(request)
    all_tenants = Tenant.objects.filter(is_active=True)

    if not tenant:
        return render(request, 'uc3_msme/no_tenant.html', {'all_tenants': all_tenants})

    projects  = Project.objects.filter(tenant=tenant).prefetch_related('phases', 'actions', 'risks')
    open_actions = ActionItem.objects.filter(tenant=tenant, status__in=['open','overdue']).count()
    overdue_actions = ActionItem.objects.filter(tenant=tenant, status='overdue').count()
    open_risks = Risk.objects.filter(tenant=tenant, status='open').count()
    high_risks = Risk.objects.filter(tenant=tenant, status='open', likelihood__gte=3, impact__gte=3).count()

    context = {
        'tenant': tenant, 'all_tenants': all_tenants,
        'projects': projects,
        'open_actions': open_actions, 'overdue_actions': overdue_actions,
        'open_risks': open_risks, 'high_risks': high_risks,
    }
    return render(request, 'uc3_msme/dashboard.html', context)


# ─── Projects ─────────────────────────────────────────────────────────────────

def project_list(request):
    tenant   = get_active_tenant(request)
    projects = Project.objects.filter(tenant=tenant)
    return render(request, 'uc3_msme/project_list.html', {
        'tenant': tenant, 'projects': projects
    })


def project_create(request):
    tenant = get_active_tenant(request)
    if request.method == 'POST':
        Project.objects.create(
            tenant      = tenant,
            name        = request.POST.get('name', ''),
            client      = request.POST.get('client', ''),
            status      = request.POST.get('status', 'planning'),
            start_date  = request.POST.get('start_date') or None,
            end_date    = request.POST.get('end_date') or None,
            description = request.POST.get('description', ''),
            health_score = 70,
        )
        messages.success(request, 'Project created.')
        return redirect('uc3:project_list')
    return render(request, 'uc3_msme/project_form.html', {'tenant': tenant})


def project_detail(request, pk):
    tenant  = get_active_tenant(request)
    project = get_object_or_404(Project, pk=pk, tenant=tenant)
    phases  = project.phases.all()
    actions = project.actions.all()[:10]
    risks   = project.risks.all()[:10]
    budgets = project.budgets.all()
    total_est = sum(float(b.estimated) for b in budgets)
    total_act = sum(float(b.actual) for b in budgets)

    # Risk heatmap data — 5×5 grid, count risks per cell
    heat_map = {}
    for r in project.risks.filter(status='open'):
        key = (r.likelihood, r.impact)
        heat_map[key] = heat_map.get(key, 0) + 1

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_status':
            project.status = request.POST.get('status', project.status)
            project.save()
            messages.success(request, 'Project status updated.')
        elif action == 'update_health':
            score = int(request.POST.get('health_score', project.health_score))
            project.health_score = max(0, min(100, score))
            project.save()
            ExecutionLog.objects.create(
                tenant=tenant, project=project,
                tool_name='update_health_score',
                payload=json.dumps({'score': score}),
                result=json.dumps({'status': 'success'}),
                ai_authority='full_write',
            )
            messages.success(request, 'Health score updated.')
        return redirect('uc3:project_detail', pk=pk)

    return render(request, 'uc3_msme/project_detail.html', {
        'tenant': tenant, 'project': project,
        'phases': phases, 'actions': actions, 'risks': risks,
        'budgets': budgets, 'total_est': total_est, 'total_act': total_act,
        'heat_map': heat_map,
    })


# ─── Action Items ─────────────────────────────────────────────────────────────

def action_list(request):
    tenant  = get_active_tenant(request)
    project_id = request.GET.get('project')
    qs = ActionItem.objects.filter(tenant=tenant).select_related('project', 'phase')
    if project_id:
        qs = qs.filter(project_id=project_id)
    status_f = request.GET.get('status', '')
    if status_f:
        qs = qs.filter(status=status_f)
    projects = Project.objects.filter(tenant=tenant)

    if request.method == 'POST':
        action = request.POST.get('form_action')
        if action == 'create':
            project = get_object_or_404(Project, pk=request.POST.get('project_id'), tenant=tenant)
            ActionItem.objects.create(
                tenant      = tenant,
                project     = project,
                title       = request.POST.get('title', ''),
                description = request.POST.get('description', ''),
                owner       = request.POST.get('owner', ''),
                due_date    = request.POST.get('due_date') or None,
                priority    = request.POST.get('priority', 'medium'),
                status      = 'open',
                created_by_ai = False,
            )
            ExecutionLog.objects.create(
                tenant=tenant, project=project,
                tool_name='create_action_item',
                payload=json.dumps({'title': request.POST.get('title', '')[:100]}),
                result='{"status":"success"}',
                ai_authority='full_write',
            )
            messages.success(request, 'Action item created.')
        elif action == 'update':
            item_id = request.POST.get('item_id')
            item = get_object_or_404(ActionItem, pk=item_id, tenant=tenant)
            item.status = request.POST.get('status', item.status)
            item.save()
            messages.success(request, 'Action item updated.')
        return redirect(request.path)

    return render(request, 'uc3_msme/action_list.html', {
        'tenant': tenant, 'actions': qs,
        'projects': projects, 'project_filter': project_id,
        'status_filter': status_f,
    })


# ─── Risks ────────────────────────────────────────────────────────────────────

def risk_list(request):
    tenant   = get_active_tenant(request)
    project_id = request.GET.get('project')
    qs = Risk.objects.filter(tenant=tenant).select_related('project')
    if project_id:
        qs = qs.filter(project_id=project_id)
    projects = Project.objects.filter(tenant=tenant)

    # Build 3×3 heat map (consolidate to 3 bands: low=1-2, med=3, high=4-5)
    heat_map_3x3 = {(l, i): [] for l in range(1, 4) for i in range(1, 4)}
    for r in qs.filter(status='open'):
        l3 = min(3, max(1, (r.likelihood + 1) // 2))
        i3 = min(3, max(1, (r.impact + 1) // 2))
        heat_map_3x3[(l3, i3)].append(r)

    if request.method == 'POST':
        form_action = request.POST.get('form_action')
        if form_action == 'create':
            project = get_object_or_404(Project, pk=request.POST.get('project_id'), tenant=tenant)
            Risk.objects.create(
                tenant      = tenant,
                project     = project,
                description = request.POST.get('description', ''),
                likelihood  = int(request.POST.get('likelihood', 2)),
                impact      = int(request.POST.get('impact', 2)),
                mitigation  = request.POST.get('mitigation', ''),
                status      = 'open',
                owner       = request.POST.get('owner', ''),
                created_by_ai = False,
            )
            messages.success(request, 'Risk added to register.')
        elif form_action == 'update_status':
            risk = get_object_or_404(Risk, pk=request.POST.get('risk_id'), tenant=tenant)
            risk.status = request.POST.get('status', risk.status)
            risk.save()
            messages.success(request, 'Risk status updated.')
        return redirect(request.path + (f'?project={project_id}' if project_id else ''))

    return render(request, 'uc3_msme/risk_list.html', {
        'tenant': tenant, 'risks': qs, 'projects': projects,
        'project_filter': project_id, 'heat_map': heat_map_3x3,
    })


# ─── Budget ───────────────────────────────────────────────────────────────────

def budget_view(request):
    tenant   = get_active_tenant(request)
    project_id = request.GET.get('project')
    projects = Project.objects.filter(tenant=tenant)
    qs = Budget.objects.filter(tenant=tenant).select_related('project', 'phase')
    if project_id:
        qs = qs.filter(project_id=project_id)
    total_est = sum(float(b.estimated) for b in qs)
    total_act = sum(float(b.actual) for b in qs)
    flagged   = [b for b in qs if abs(b.variance_pct) > 10]
    return render(request, 'uc3_msme/budget.html', {
        'tenant': tenant, 'budgets': qs, 'projects': projects,
        'project_filter': project_id,
        'total_est': total_est, 'total_act': total_act, 'flagged': flagged,
    })


# ─── AI Chat ─────────────────────────────────────────────────────────────────

def ai_chat(request):
    tenant  = get_active_tenant(request)
    project_id = request.GET.get('project') or request.session.get('chat_project_id')
    project = None
    if project_id:
        try:
            project = Project.objects.get(pk=project_id, tenant=tenant)
            request.session['chat_project_id'] = project.id
        except Project.DoesNotExist:
            pass
    projects = Project.objects.filter(tenant=tenant)
    messages_qs = ChatMessage.objects.filter(tenant=tenant, project=project)[:50]

    if request.method == 'POST':
        user_msg = request.POST.get('message', '').strip()
        if user_msg and project:
            # Save user message
            ChatMessage.objects.create(tenant=tenant, project=project, role='user', content=user_msg)

            # Build context
            open_actions = ActionItem.objects.filter(project=project, tenant=tenant, status__in=['open','overdue']).count()
            high_risks   = Risk.objects.filter(project=project, tenant=tenant, status='open').filter(likelihood__gte=3, impact__gte=3).count()

            system = f"""You are an AI Project Coordinator for æquilibri.
Tenant: {tenant.name}
Project: {project.name} (Status: {project.get_status_display()}, Health: {project.health_score}/100)
Open actions: {open_actions} | High risks: {high_risks}

CRUD POLICY:
- Full write authority: ACTION_ITEMS, RISKS, VENDORS.rating, CASHFLOWS.projected
- Requires human approval: PHASES, DECISIONS
- BLOCKED: BUDGET.actual, PROJECTS.creation, DOCUMENTS

Answer concisely. When proposing writes that need approval, clearly label them as PROPOSAL."""

            result = call_claude(system, user_msg)
            ai_content = result['content']
            needs_approval = any(w in user_msg.lower() for w in ['phase', 'decision', 'approve'])

            ChatMessage.objects.create(
                tenant=tenant, project=project, role='assistant',
                content=ai_content,
                requires_approval=needs_approval,
            )

            ExecutionLog.objects.create(
                tenant=tenant, project=project,
                tool_name='ai_chat_query',
                payload=json.dumps({'message': user_msg[:200]}),
                result=json.dumps({'len': len(ai_content), 'demo': result.get('demo_mode')}),
                ai_authority='full_write' if not needs_approval else 'approval_required',
            )
        return redirect(f"{request.path}?project={project.id}" if project else request.path)

    return render(request, 'uc3_msme/ai_chat.html', {
        'tenant': tenant, 'project': project, 'projects': projects,
        'messages': messages_qs,
    })


# ─── Exec Log ─────────────────────────────────────────────────────────────────

def exec_log(request):
    tenant = get_active_tenant(request)
    logs   = ExecutionLog.objects.filter(tenant=tenant)[:100]
    return render(request, 'uc3_msme/exec_log.html', {'tenant': tenant, 'logs': logs})


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 4 — Variation Orders
# ═══════════════════════════════════════════════════════════════════════════════

def variation_list(request):
    tenant     = get_active_tenant(request)
    project_id = request.GET.get('project')
    qs         = VariationOrder.objects.filter(tenant=tenant).select_related('project')
    if project_id:
        qs = qs.filter(project_id=project_id)
    projects   = Project.objects.filter(tenant=tenant)
    total_cost = sum(float(v.cost_impact) for v in qs)
    return render(request, 'uc3_msme/variation_list.html', {
        'tenant': tenant, 'variations': qs, 'projects': projects,
        'project_filter': project_id, 'total_cost': total_cost,
    })


def variation_create(request):
    tenant   = get_active_tenant(request)
    projects = Project.objects.filter(tenant=tenant)
    ai_draft = None

    if request.method == 'POST':
        form_action = request.POST.get('form_action')
        project_id  = request.POST.get('project_id')

        if form_action == 'ai_draft' and project_id:
            project = get_object_or_404(Project, pk=project_id, tenant=tenant)
            trigger = request.POST.get('trigger_description', '')
            system  = """You are a construction contract specialist. Draft a formal Variation Order.
Respond ONLY with a valid JSON object containing these keys:
  title (string, max 80 chars),
  description (string, 2-3 formal sentences),
  scope_change (string, bullet list of scope changes),
  cost_impact (number, positive = cost increase),
  time_impact_days (integer, positive = delay)
Return ONLY the JSON object, no markdown fences."""
            result = call_claude(system,
                f"Project: {project.name}\nVariation trigger: {trigger}\nDraft the variation order.",
                max_tokens=1024)
            try:
                m = re.search(r'\{.*\}', result['content'], re.DOTALL)
                ai_draft = json.loads(m.group()) if m else {}
                ai_draft.update({'project_id': project.id, 'project_name': project.name,
                                 'trigger': trigger})
            except Exception:
                ai_draft = {'error': result['content'][:500]}

        elif form_action == 'save':
            project = get_object_or_404(Project, pk=project_id, tenant=tenant)
            vo = VariationOrder.objects.create(
                tenant=tenant, project=project,
                title=request.POST.get('title', ''),
                description=request.POST.get('description', ''),
                scope_change=request.POST.get('scope_change', ''),
                cost_impact=request.POST.get('cost_impact') or 0,
                time_impact_days=request.POST.get('time_impact_days') or 0,
                status='draft',
                is_ai_drafted=request.POST.get('is_ai_drafted') == '1',
                submitted_by=request.POST.get('submitted_by', ''),
            )
            ExecutionLog.objects.create(
                tenant=tenant, project=project,
                tool_name='variation_order_create',
                payload=json.dumps({'ref': vo.ref_number, 'cost': float(vo.cost_impact)}),
                result='{"status":"success"}',
                ai_authority='approval_required' if vo.is_ai_drafted else 'full_write',
            )
            messages.success(request, f'Variation Order {vo.ref_number} created.')
            return redirect('uc3:variation_list')

    return render(request, 'uc3_msme/variation_create.html', {
        'tenant': tenant, 'projects': projects, 'ai_draft': ai_draft,
    })


def variation_detail(request, pk):
    tenant = get_active_tenant(request)
    vo     = get_object_or_404(VariationOrder, pk=pk, tenant=tenant)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            vo.status     = 'approved'
            vo.approved_by = request.POST.get('approved_by', 'Project Manager')
            vo.approved_at = timezone.now()
            vo.save()
            messages.success(request, f'{vo.ref_number} approved.')
        elif action == 'reject':
            vo.status = 'rejected'
            vo.save()
            messages.warning(request, f'{vo.ref_number} rejected.')
        elif action == 'submit':
            vo.status = 'pending_approval'
            vo.save()
            messages.info(request, f'{vo.ref_number} submitted for approval.')
        return redirect('uc3:variation_detail', pk=pk)

    return render(request, 'uc3_msme/variation_detail.html', {'tenant': tenant, 'vo': vo})


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 2 — Client Portal
# ═══════════════════════════════════════════════════════════════════════════════

def portal_list(request):
    tenant = get_active_tenant(request)
    tokens = ClientPortalToken.objects.filter(tenant=tenant).select_related('project')
    return render(request, 'uc3_msme/portal_list.html', {'tenant': tenant, 'tokens': tokens})


def portal_manage(request, pk):
    tenant  = get_active_tenant(request)
    project = get_object_or_404(Project, pk=pk, tenant=tenant)
    tokens  = ClientPortalToken.objects.filter(project=project, tenant=tenant)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            t = ClientPortalToken.objects.create(
                project=project, tenant=tenant,
                label=request.POST.get('label', ''),
                expires_at=request.POST.get('expires_at') or None,
            )
            messages.success(request, f'Portal link created — share: /uc3/portal/{t.token}/')
        elif action == 'deactivate':
            t = get_object_or_404(ClientPortalToken, pk=request.POST.get('token_id'), tenant=tenant)
            t.is_active = False
            t.save()
            messages.success(request, 'Portal link deactivated.')
        return redirect('uc3:portal_manage', pk=pk)

    return render(request, 'uc3_msme/portal_manage.html', {
        'tenant': tenant, 'project': project, 'tokens': tokens,
    })


def portal_public(request, token):
    """Public project status portal — no authentication required."""
    token_obj = get_object_or_404(ClientPortalToken, token=token, is_active=True)
    if token_obj.expires_at and token_obj.expires_at < date.today():
        return render(request, 'uc3_msme/portal_expired.html', {'token': token_obj})

    # Track view
    token_obj.views_count += 1
    token_obj.save(update_fields=['views_count'])

    project = token_obj.project
    phases  = project.phases.all()
    actions = project.actions.filter(status__in=['open', 'overdue']).order_by('-priority')[:8]
    risks_high  = project.risks.filter(status='open', likelihood__gte=3, impact__gte=3).count()
    risks_total = project.risks.filter(status='open').count()
    budgets     = project.budgets.all()
    total_est   = sum(float(b.estimated) for b in budgets)
    total_act   = sum(float(b.actual) for b in budgets)
    budget_pct  = round((total_act / total_est * 100) if total_est > 0 else 0, 1)

    return render(request, 'uc3_msme/portal_public.html', {
        'project': project, 'token': token_obj,
        'phases': phases, 'actions': actions,
        'risks_high': risks_high, 'risks_total': risks_total,
        'total_est': total_est, 'total_act': total_act, 'budget_pct': budget_pct,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 1 — Accounting Sync
# ═══════════════════════════════════════════════════════════════════════════════

def accounting_sync(request):
    tenant      = get_active_tenant(request)
    connections = AccountingConnection.objects.filter(tenant=tenant)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'connect':
            provider = request.POST.get('provider', 'xero')
            conn, _ = AccountingConnection.objects.get_or_create(
                tenant=tenant, provider=provider,
                defaults={'status': 'disconnected'},
            )
            conn.status   = 'connected'
            conn.org_name = request.POST.get('org_name',
                f'{tenant.name} — {conn.get_provider_display()}')
            conn.last_sync = timezone.now()
            conn.sync_log  = f'[{timezone.now():%Y-%m-%d %H:%M}] Connection established (simulated OAuth).\n'
            conn.save()
            messages.success(request, f'Connected to {conn.get_provider_display()} — {conn.org_name}')

        elif action == 'sync_now':
            conn = get_object_or_404(AccountingConnection, pk=request.POST.get('conn_id'),
                                     tenant=tenant)
            budgets   = Budget.objects.filter(tenant=tenant)
            cashflows = Cashflow.objects.filter(tenant=tenant)
            count     = budgets.count() + cashflows.count()
            conn.last_sync      = timezone.now()
            conn.records_synced = count
            conn.sync_log += (f'[{timezone.now():%Y-%m-%d %H:%M}] Sync complete — '
                              f'{count} records ({budgets.count()} budget lines, '
                              f'{cashflows.count()} cashflow entries).\n')
            conn.save()
            ExecutionLog.objects.create(
                tenant=tenant,
                tool_name='accounting_sync',
                payload=json.dumps({'provider': conn.provider, 'records': count}),
                result='{"status":"success"}',
                ai_authority='full_write',
            )
            messages.success(request, f'Synced {count} records to {conn.get_provider_display()}.')

        elif action == 'disconnect':
            conn = get_object_or_404(AccountingConnection, pk=request.POST.get('conn_id'),
                                     tenant=tenant)
            conn.status       = 'disconnected'
            conn.access_token = ''
            conn.save()
            messages.warning(request, f'Disconnected from {conn.get_provider_display()}.')

        return redirect('uc3:accounting_sync')

    budgets       = Budget.objects.filter(tenant=tenant)
    total_est     = sum(float(b.estimated) for b in budgets)
    total_act     = sum(float(b.actual) for b in budgets)
    return render(request, 'uc3_msme/accounting_sync.html', {
        'tenant': tenant, 'connections': connections,
        'total_est': total_est, 'total_act': total_act,
        'budget_count': budgets.count(),
        'cashflow_count': Cashflow.objects.filter(tenant=tenant).count(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 5 — Weekly Client Reports
# ═══════════════════════════════════════════════════════════════════════════════

def report_list(request):
    tenant     = get_active_tenant(request)
    project_id = request.GET.get('project')
    qs         = WeeklyReport.objects.filter(tenant=tenant).select_related('project')
    if project_id:
        qs = qs.filter(project_id=project_id)
    projects = Project.objects.filter(tenant=tenant)
    return render(request, 'uc3_msme/report_list.html', {
        'tenant': tenant, 'reports': qs, 'projects': projects,
        'project_filter': project_id,
    })


def report_generate(request):
    tenant   = get_active_tenant(request)
    projects = Project.objects.filter(tenant=tenant)

    if request.method == 'POST':
        project     = get_object_or_404(Project, pk=request.POST.get('project_id'), tenant=tenant)
        week_ending = date.fromisoformat(request.POST.get('week_ending', str(date.today())))

        open_actions      = ActionItem.objects.filter(project=project, tenant=tenant, status='open').count()
        overdue_actions   = ActionItem.objects.filter(project=project, tenant=tenant, status='overdue').count()
        completed_week    = ActionItem.objects.filter(
            project=project, tenant=tenant, status='complete',
            updated_at__gte=timezone.now() - timedelta(days=7)).count()
        high_risks = Risk.objects.filter(
            project=project, tenant=tenant, status='open',
            likelihood__gte=3, impact__gte=3).count()
        phases       = project.phases.all()
        phases_text  = '\n'.join(
            f"  • {p.name}: {p.completion_pct}% ({p.get_status_display()})" for p in phases
        ) or '  No phases defined.'
        budgets      = project.budgets.all()
        total_est    = sum(float(b.estimated) for b in budgets)
        total_act    = sum(float(b.actual) for b in budgets)
        budget_pct   = round((total_act / total_est * 100) if total_est > 0 else 0, 1)

        system = """You are a professional construction project manager writing a weekly client status report.
Use Australian English. Professional but readable tone. Format with markdown headers (##).
Include: Executive Summary, Progress This Week, Looking Ahead, Budget Status, Risks & Issues, Actions Required from Client.
Be specific with numbers. Keep each section concise (3-5 bullet points max)."""

        prompt = f"""Generate a weekly client report:

WEEK ENDING: {week_ending.strftime('%d %B %Y')}
PROJECT: {project.name}
CLIENT: {project.client or 'Client'}
STATUS: {project.get_status_display()} | Health Score: {project.health_score}/100

PHASE PROGRESS:
{phases_text}

ACTIONS:
  Completed this week: {completed_week}
  Open: {open_actions} | Overdue: {overdue_actions}

BUDGET: ${total_act:,.0f} of ${total_est:,.0f} ({budget_pct}% utilised)
HIGH RISKS: {high_risks} open

Write the weekly client report now."""

        result  = call_claude(system, prompt, max_tokens=2048)
        content = result['content']

        report = WeeklyReport.objects.create(
            tenant=tenant, project=project,
            week_ending=week_ending,
            title=f"Week Ending {week_ending.strftime('%d %B %Y')} — {project.name}",
            content=content,
            is_ai_generated=True, status='draft',
        )
        ExecutionLog.objects.create(
            tenant=tenant, project=project,
            tool_name='generate_weekly_report',
            payload=json.dumps({'week_ending': str(week_ending)}),
            result=json.dumps({'report_id': report.id, 'chars': len(content)}),
            ai_authority='full_write',
        )
        messages.success(request, f'Report generated for {project.name}.')
        return redirect('uc3:report_detail', pk=report.pk)

    return render(request, 'uc3_msme/report_generate.html', {
        'tenant': tenant, 'projects': projects, 'today': date.today().isoformat(),
    })


def report_detail(request, pk):
    tenant = get_active_tenant(request)
    report = get_object_or_404(WeeklyReport, pk=pk, tenant=tenant)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            report.status      = 'approved'
            report.approved_by = request.POST.get('approved_by', 'Project Manager')
            report.approved_at = timezone.now()
            report.save()
            messages.success(request, 'Report approved.')
        elif action == 'mark_sent':
            report.status = 'sent'
            report.save()
            messages.success(request, 'Report marked as sent.')
        return redirect('uc3:report_detail', pk=pk)

    return render(request, 'uc3_msme/report_detail.html', {'tenant': tenant, 'report': report})


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 3 — Cashflow Scenario Planner
# ═══════════════════════════════════════════════════════════════════════════════

def cashflow_planner(request):
    tenant     = get_active_tenant(request)
    project_id = request.GET.get('project')
    projects   = Project.objects.filter(tenant=tenant)
    project    = None

    if project_id:
        try: project = Project.objects.get(pk=project_id, tenant=tenant)
        except Project.DoesNotExist: pass
    if not project and projects.exists():
        project = projects.first()

    cf_data    = []
    scenarios  = {}
    total_budget = total_spent = avg_burn = 0

    if project:
        cf_qs    = Cashflow.objects.filter(project=project, tenant=tenant).order_by('period')
        budgets  = Budget.objects.filter(project=project, tenant=tenant)
        total_budget = sum(float(b.estimated) for b in budgets)
        total_spent  = sum(float(b.actual) for b in budgets)

        running = 0
        for cf in cf_qs:
            month_spend = float(cf.actual) if float(cf.actual) != 0 else float(cf.projected)
            running += month_spend
            cf_data.append({'period': cf.period, 'projected': float(cf.projected),
                             'actual': float(cf.actual), 'running': running})

        actual_vals = [float(cf.actual) for cf in cf_qs if float(cf.actual) != 0]
        avg_burn    = sum(actual_vals) / len(actual_vals) if actual_vals else 0
        remaining   = total_budget - total_spent

        def months(burn): return round(remaining / burn, 1) if burn > 0 else None
        scenarios = {
            'optimistic':  {'label': 'Optimistic (−15%)',  'burn': avg_burn * 0.85,  'months': months(avg_burn * 0.85)},
            'base':        {'label': 'Base (current)',      'burn': avg_burn,          'months': months(avg_burn)},
            'pessimistic': {'label': 'Pessimistic (+20%)', 'burn': avg_burn * 1.20,  'months': months(avg_burn * 1.20)},
        }

    return render(request, 'uc3_msme/cashflow_planner.html', {
        'tenant': tenant, 'project': project, 'projects': projects,
        'project_filter': project_id,
        'cf_json': json.dumps(cf_data),
        'total_budget': total_budget, 'total_spent': total_spent,
        'avg_burn': avg_burn, 'remaining': total_budget - total_spent,
        'scenarios': scenarios,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 6 — Budget Burn Rate Analytics
# ═══════════════════════════════════════════════════════════════════════════════

def budget_analytics(request):
    tenant     = get_active_tenant(request)
    project_id = request.GET.get('project')
    projects   = Project.objects.filter(tenant=tenant)
    project    = None

    if project_id:
        try: project = Project.objects.get(pk=project_id, tenant=tenant)
        except Project.DoesNotExist: pass
    if not project and projects.exists():
        project = projects.first()

    if not project:
        return render(request, 'uc3_msme/budget_analytics.html',
                      {'tenant': tenant, 'projects': projects, 'project': None})

    budgets      = Budget.objects.filter(project=project, tenant=tenant).select_related('phase')
    total_est    = sum(float(b.estimated) for b in budgets)
    total_act    = sum(float(b.actual) for b in budgets)
    burn_pct     = round((total_act / total_est * 100) if total_est > 0 else 0, 1)
    over_lines   = [b for b in budgets if b.variance > 0]
    under_lines  = [b for b in budgets if b.variance < 0]

    phases           = project.phases.all()
    completed_phases = phases.filter(status='complete').count()
    total_phases     = phases.count()
    schedule_pct     = round((completed_phases / total_phases * 100) if total_phases > 0 else 0)
    cost_variance    = round(burn_pct - schedule_pct, 1)

    phase_budgets = []
    for ph in phases:
        pb = budgets.filter(phase=ph)
        if pb.exists():
            pe = sum(float(b.estimated) for b in pb)
            pa = sum(float(b.actual) for b in pb)
            phase_budgets.append({
                'phase': ph, 'estimated': pe, 'actual': pa,
                'variance': pa - pe,
                'variance_pct': round(((pa - pe) / pe * 100) if pe > 0 else 0, 1),
            })

    cf_qs          = Cashflow.objects.filter(project=project, tenant=tenant)
    actual_months  = [float(c.actual) for c in cf_qs if float(c.actual) != 0]
    avg_monthly    = sum(actual_months) / len(actual_months) if actual_months else 0
    remaining      = total_est - total_act
    months_left    = round(remaining / avg_monthly, 1) if avg_monthly > 0 else None

    return render(request, 'uc3_msme/budget_analytics.html', {
        'tenant': tenant, 'project': project, 'projects': projects,
        'project_filter': str(project.pk),
        'budgets': budgets, 'total_est': total_est, 'total_act': total_act,
        'burn_pct': burn_pct, 'over_lines': over_lines, 'under_lines': under_lines,
        'schedule_pct': schedule_pct, 'cost_variance': cost_variance,
        'phase_budgets': phase_budgets,
        'avg_monthly': avg_monthly, 'months_left': months_left,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 7 — Document Intelligence
# ═══════════════════════════════════════════════════════════════════════════════

def document_list(request):
    tenant     = get_active_tenant(request)
    project_id = request.GET.get('project')
    projects   = Project.objects.filter(tenant=tenant)
    qs         = Document.objects.filter(tenant=tenant).select_related('project')
    if project_id:
        qs = qs.filter(project_id=project_id)

    if request.method == 'POST':
        form_action = request.POST.get('form_action')
        if form_action == 'create':
            proj = get_object_or_404(Project, pk=request.POST.get('project_id'), tenant=tenant)
            Document.objects.create(
                tenant=tenant, project=proj,
                name=request.POST.get('name', ''),
                doc_type=request.POST.get('doc_type', ''),
                version=request.POST.get('version', '1.0'),
                upload_date=request.POST.get('upload_date') or None,
                uploaded_by=request.POST.get('uploaded_by', ''),
                notes=request.POST.get('notes', ''),
                file_content=request.POST.get('file_content', ''),
            )
            messages.success(request, 'Document added.')
        return redirect(request.path + (f'?project={project_id}' if project_id else ''))

    return render(request, 'uc3_msme/document_list.html', {
        'tenant': tenant, 'documents': qs, 'projects': projects,
        'project_filter': project_id,
    })


def document_analyze(request, pk):
    tenant = get_active_tenant(request)
    doc    = get_object_or_404(Document, pk=pk, tenant=tenant)

    if request.method == 'POST':
        new_content = request.POST.get('file_content', '').strip()
        if new_content:
            doc.file_content = new_content
        if doc.file_content:
            system = """You are a construction contract and document intelligence specialist.
Analyse the provided document and return a structured report with these sections (use ## headers):
## Document Summary
## Key Clauses & Obligations
## Critical Dates & Deadlines
## Financial Terms
## Risk Flags
## Recommended Actions

Be specific and practical. Use Australian construction industry context."""
            result = call_claude(system,
                f"Analyse this document:\n\n{doc.file_content[:8000]}", max_tokens=2048)
            doc.ai_analysis  = result['content']
            doc.analyzed_at  = timezone.now()
            doc.save()
            ExecutionLog.objects.create(
                tenant=tenant, project=doc.project,
                tool_name='document_intelligence',
                payload=json.dumps({'doc': doc.name, 'chars': len(doc.file_content)}),
                result=json.dumps({'analysis_chars': len(doc.ai_analysis)}),
                ai_authority='full_write',
            )
            messages.success(request, f'"{doc.name}" analysed successfully.')
        else:
            messages.error(request, 'Please paste document content before analysing.')
        return redirect('uc3:document_analyze', pk=pk)

    return render(request, 'uc3_msme/document_analyze.html', {'tenant': tenant, 'doc': doc})


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 8 — Delay Cascade Analyser
# ═══════════════════════════════════════════════════════════════════════════════

def delay_cascade(request):
    tenant     = get_active_tenant(request)
    project_id = request.GET.get('project')
    projects   = Project.objects.filter(tenant=tenant)
    project    = None

    if project_id:
        try: project = Project.objects.get(pk=project_id, tenant=tenant)
        except Project.DoesNotExist: pass
    if not project and projects.exists():
        project = projects.first()

    cascade_result = None
    phases = list(project.phases.all().order_by('order')) if project else []

    if request.method == 'POST' and project:
        delayed_phase_id = int(request.POST.get('phase_id', 0))
        delay_days       = int(request.POST.get('delay_days', 0))

        cascade_phases   = []
        delayed_reached  = False
        delay_accum      = 0
        delayed_name     = ''

        for ph in phases:
            if ph.id == delayed_phase_id:
                delayed_reached = True
                delay_accum     = delay_days
                delayed_name    = ph.name

            new_start = (ph.start_date + timedelta(days=delay_accum)
                         if ph.start_date and delayed_reached else ph.start_date)
            new_end   = (ph.end_date + timedelta(days=delay_accum)
                         if ph.end_date and delayed_reached else ph.end_date)
            cascade_phases.append({
                'phase': ph,
                'original_start': ph.start_date, 'original_end': ph.end_date,
                'new_start': new_start, 'new_end': new_end,
                'delay_days': delay_accum if delayed_reached else 0,
                'is_trigger': ph.id == delayed_phase_id,
            })

        orig_end = max((p.end_date for p in phases if p.end_date), default=None)
        new_end  = orig_end + timedelta(days=delay_accum) if orig_end else None

        # AI mitigation suggestions
        ai_text = ''
        if delayed_name:
            result  = call_claude(
                "You are a construction project manager. Give 4-5 specific mitigation actions. Be concise, practical.",
                f"Project '{project.name}', phase '{delayed_name}' delayed {delay_days} days. "
                f"List mitigation actions to minimise cascade impact on subsequent phases.",
                max_tokens=512)
            ai_text = result['content']

        cascade_result = {
            'cascade_phases': cascade_phases,
            'delay_days': delay_days,
            'delayed_phase_name': delayed_name,
            'orig_completion': orig_end, 'new_completion': new_end,
            'total_impact': delay_accum,
            'ai_mitigations': ai_text,
        }

    return render(request, 'uc3_msme/delay_cascade.html', {
        'tenant': tenant, 'project': project, 'projects': projects,
        'project_filter': project_id, 'phases': phases,
        'cascade_result': cascade_result,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 9 — Meeting Minutes → Action Items
# ═══════════════════════════════════════════════════════════════════════════════

def meeting_minutes_list(request):
    tenant     = get_active_tenant(request)
    project_id = request.GET.get('project')
    projects   = Project.objects.filter(tenant=tenant)
    qs         = MeetingMinutes.objects.filter(tenant=tenant).select_related('project')
    if project_id:
        qs = qs.filter(project_id=project_id)
    return render(request, 'uc3_msme/meeting_minutes_list.html', {
        'tenant': tenant, 'minutes_list': qs, 'projects': projects,
        'project_filter': project_id,
    })


def meeting_minutes_create(request):
    tenant   = get_active_tenant(request)
    projects = Project.objects.filter(tenant=tenant)

    if request.method == 'POST':
        project     = get_object_or_404(Project, pk=request.POST.get('project_id'), tenant=tenant)
        raw         = request.POST.get('raw_minutes', '')
        mtg_date    = request.POST.get('meeting_date', str(date.today()))
        title       = request.POST.get('title', '')
        attendees   = request.POST.get('attendees', '')
        form_action = request.POST.get('form_action', 'save_only')

        if form_action == 'extract' and raw:
            system = """You are a construction project meeting analyst.
Extract ALL action items from the meeting notes.
Return ONLY a valid JSON array. Each element must have:
  "action" (string, starts with a verb),
  "owner" (string, empty if unclear),
  "due_date" (ISO date YYYY-MM-DD or null),
  "priority" ("low"|"medium"|"high"|"critical"),
  "notes" (string, any context)
Return ONLY the JSON array, no other text."""
            result = call_claude(system,
                f"Extract action items from these meeting notes:\n\n{raw}", max_tokens=1024)
            try:
                m         = re.search(r'\[.*\]', result['content'], re.DOTALL)
                extracted = json.loads(m.group()) if m else []
            except Exception:
                extracted = []
            mm = MeetingMinutes.objects.create(
                tenant=tenant, project=project,
                meeting_date=mtg_date, title=title, attendees=attendees,
                raw_minutes=raw,
                extracted_actions=json.dumps(extracted),
                actions_count=len(extracted),
                status='processed',
            )
            messages.success(request, f'Extracted {len(extracted)} action items.')
            return redirect('uc3:meeting_minutes_detail', pk=mm.pk)

        mm = MeetingMinutes.objects.create(
            tenant=tenant, project=project,
            meeting_date=mtg_date, title=title, attendees=attendees,
            raw_minutes=raw, status='raw',
        )
        messages.success(request, 'Meeting minutes saved.')
        return redirect('uc3:meeting_minutes_detail', pk=mm.pk)

    return render(request, 'uc3_msme/meeting_minutes_create.html', {
        'tenant': tenant, 'projects': projects, 'today': date.today().isoformat(),
    })


def meeting_minutes_detail(request, pk):
    tenant = get_active_tenant(request)
    mm     = get_object_or_404(MeetingMinutes, pk=pk, tenant=tenant)
    try:
        extracted = json.loads(mm.extracted_actions)
    except Exception:
        extracted = []

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'confirm_actions':
            count = 0
            for item in extracted:
                due_date = None
                raw_due  = item.get('due_date')
                if raw_due:
                    try: due_date = date.fromisoformat(raw_due)
                    except Exception: pass
                ActionItem.objects.create(
                    tenant=tenant, project=mm.project,
                    title=item.get('action', 'Action from meeting')[:300],
                    description=item.get('notes', ''),
                    owner=item.get('owner', ''),
                    due_date=due_date,
                    priority=item.get('priority', 'medium'),
                    status='open', created_by_ai=True,
                )
                count += 1
            mm.status = 'confirmed'
            mm.save()
            ExecutionLog.objects.create(
                tenant=tenant, project=mm.project,
                tool_name='meeting_minutes_to_actions',
                payload=json.dumps({'meeting': str(mm.meeting_date), 'created': count}),
                result='{"status":"success"}',
                ai_authority='full_write',
            )
            messages.success(request, f'{count} action items created from meeting minutes.')
            return redirect('uc3:action_list')

        elif action == 're_extract':
            system = """You are a construction project meeting analyst.
Extract ALL action items from the meeting notes.
Return ONLY a valid JSON array with fields: action, owner, due_date, priority, notes."""
            result = call_claude(system,
                f"Extract action items:\n\n{mm.raw_minutes}", max_tokens=1024)
            try:
                m         = re.search(r'\[.*\]', result['content'], re.DOTALL)
                extracted = json.loads(m.group()) if m else []
            except Exception:
                extracted = []
            mm.extracted_actions = json.dumps(extracted)
            mm.actions_count     = len(extracted)
            mm.status            = 'processed'
            mm.save()
            messages.success(request, f'Re-extracted {len(extracted)} action items.')
            return redirect('uc3:meeting_minutes_detail', pk=pk)

    return render(request, 'uc3_msme/meeting_minutes_detail.html', {
        'tenant': tenant, 'mm': mm, 'extracted_actions': extracted,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 10 — Risk Escalation Engine
# ═══════════════════════════════════════════════════════════════════════════════

def risk_escalation(request):
    tenant     = get_active_tenant(request)
    project_id = request.GET.get('project')
    projects   = Project.objects.filter(tenant=tenant)
    qs         = Risk.objects.filter(tenant=tenant, status='open').select_related('project')
    if project_id:
        qs = qs.filter(project_id=project_id)

    today = date.today()
    escalate_now     = []
    review_required  = []
    monitoring       = []

    for r in qs:
        age       = (today - r.created_at.date()).days
        escalated = bool(r.escalated_at)
        if r.risk_score >= 15 and age >= 7 and not escalated:
            escalate_now.append((r, age))
        elif r.risk_score >= 8 and age >= 14 and not escalated:
            review_required.append((r, age))
        else:
            monitoring.append((r, age))

    if request.method == 'POST':
        action  = request.POST.get('action')
        risk_id = request.POST.get('risk_id')
        risk    = get_object_or_404(Risk, pk=risk_id, tenant=tenant)
        age     = (today - risk.created_at.date()).days

        if action == 'escalate':
            risk.escalated_at    = timezone.now()
            risk.escalation_note = (request.POST.get('note', '') or
                f'Escalated {today} — {risk.risk_level} risk, open {age} days.')
            risk.save()
            ExecutionLog.objects.create(
                tenant=tenant, project=risk.project,
                tool_name='risk_escalation',
                payload=json.dumps({'risk_id': risk.id, 'score': risk.risk_score, 'age': age}),
                result='{"status":"escalated"}',
                ai_authority='full_write',
            )
            messages.warning(request, f'Escalated: {risk.description[:80]}')
        elif action == 'accept':
            risk.status = 'accepted'
            risk.save()
            messages.info(request, 'Risk accepted — no further escalation.')

        return redirect(request.path + (f'?project={project_id}' if project_id else ''))

    return render(request, 'uc3_msme/risk_escalation.html', {
        'tenant': tenant, 'projects': projects, 'project_filter': project_id,
        'escalate_now': escalate_now,
        'review_required': review_required,
        'monitoring': monitoring,
        'total_open': qs.count(),
    })
