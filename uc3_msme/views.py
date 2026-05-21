"""UC3 MSME Project Coordinator — Views"""
import json
from datetime import date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone

from .models import (Tenant, Project, Phase, ActionItem, Risk, Budget,
                     Vendor, Decision, Document, Cashflow, TenantUser,
                     ExecutionLog, ChatMessage)
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
