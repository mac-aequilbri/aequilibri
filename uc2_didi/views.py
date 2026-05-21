"""UC2 Didi — Dulong Downs AI Assistant Views"""
import json
import uuid
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

from .models import (
    ActionHub, Budget, Cashflow, ChangeLog, Decision, Document,
    Procurement, ProjectPhase, ProjectPlan, RoomMatrix, Vendor,
    ExecutionLog, Hypothesis, LearningRule, RefBudget, RefCategory, RefZone,
    Metadata, ChatSession, ChatMessage
)
from core.claude_client import call_claude


# ─── Dashboard ────────────────────────────────────────────────────────────────

def dashboard(request):
    open_actions   = ActionHub.objects.filter(status__in=['open','overdue']).count()
    overdue        = ActionHub.objects.filter(status='overdue').count()
    phases         = ProjectPhase.objects.all().order_by('order')
    overall_pct    = 0
    if phases.exists():
        overall_pct = int(sum(p.completion_pct for p in phases) / phases.count())
    total_estimated = sum(float(b.estimated) for b in Budget.objects.all())
    total_actual    = sum(float(b.actual)    for b in Budget.objects.all())
    active_rules    = LearningRule.objects.filter(is_active=True).count()
    pending_hyp     = Hypothesis.objects.filter(status='pending').count()
    vendors         = Vendor.objects.filter(is_active=True).count()
    recent_changes  = ChangeLog.objects.all()[:5]

    context = {
        'open_actions': open_actions, 'overdue': overdue,
        'phases': phases, 'overall_pct': overall_pct,
        'total_estimated': total_estimated, 'total_actual': total_actual,
        'active_rules': active_rules, 'pending_hyp': pending_hyp,
        'vendors': vendors, 'recent_changes': recent_changes,
    }
    return render(request, 'uc2_didi/dashboard.html', context)


# ─── Chat / Didi Interface ────────────────────────────────────────────────────

def chat(request):
    """Main Didi chat interface."""
    session_key = request.session.get('didi_session_id')
    if not session_key:
        session_key = str(uuid.uuid4())[:8].upper()
        request.session['didi_session_id'] = session_key

    session, created = ChatSession.objects.get_or_create(session_id=session_key)
    messages_qs = session.messages.all()
    pending_proposals = session.messages.filter(has_proposal=True, proposal_confirmed=False)

    # Sidebar data
    active_rules   = LearningRule.objects.filter(is_active=True)[:8]
    overdue_items  = ActionHub.objects.filter(status='overdue')[:5]

    return render(request, 'uc2_didi/chat.html', {
        'session': session,
        'messages': messages_qs,
        'pending_proposals': pending_proposals,
        'active_rules': active_rules,
        'overdue_items': overdue_items,
        'session_key': session_key,
    })


def chat_send(request):
    """Handle a new chat message."""
    if request.method != 'POST':
        return redirect('uc2:chat')

    user_input   = request.POST.get('message', '').strip()
    session_key  = request.session.get('didi_session_id', 'DEMO')
    session, _   = ChatSession.objects.get_or_create(session_id=session_key)

    if not user_input:
        return redirect('uc2:chat')

    # Save user message
    ChatMessage.objects.create(session=session, role='user', content=user_input)

    # Build system prompt
    active_rules_text = "\n".join(
        f"- {r.rule_code}: {r.description}" for r in LearningRule.objects.filter(is_active=True)[:10]
    )
    system_prompt = f"""You are Didi, the AI assistant for the Dulong Downs residential construction project.
Client: Jack Henderson / Aboda Investments (199 Dulong Road, QLD)
Authorised contacts: Claudia Salem, Paul Sakrzewski.

CRITICAL RULES:
- NEVER write to the database without explicit YES confirmation from the user.
- Always cross-check CASHFLOWS before any invoice write involving Lighthouse Noosa (LRN-0030).
- Cannot-override rules: LRN-0006, LRN-0015, LRN-0018, LRN-0024, LRN-0032.
- Read-only tables: LEARNING_RULES, VENDORS, REF_*, METADATA, PROJECT_PHASES, ROOM_MATRIX.

ACTIVE LEARNING RULES (top 10):
{active_rules_text}

When you need to propose a write, present it as a DIFF showing old vs new value and ask for explicit YES confirmation.
Answer queries factually based on the project data. Be concise but thorough."""

    # Get Claude response
    result = call_claude(system_prompt, user_input)
    ai_content  = result['content']
    tool_uses   = result['tool_uses']
    demo_mode   = result['demo_mode']

    # Detect if this is a write proposal
    is_proposal = any(w in user_input.lower() for w in ['update', 'change', 'set', 'write', 'modify', 'delete', 'create', 'add'])
    is_proposal = is_proposal and any(w in user_input.lower() for w in ['budget', 'action', 'cashflow', 'decision', 'procurement'])

    # Log execution
    ExecutionLog.objects.create(
        tool_name='chat_query',
        payload=json.dumps({'message': user_input[:200]}),
        result=json.dumps({'response_len': len(ai_content), 'demo': demo_mode}),
        session_id=session_key,
    )

    # Save AI message
    ChatMessage.objects.create(
        session=session,
        role='assistant',
        content=ai_content,
        tool_calls=json.dumps(tool_uses),
        has_proposal=is_proposal,
    )

    return redirect('uc2:chat')


def chat_confirm(request):
    """Confirm a pending write proposal."""
    if request.method != 'POST':
        return redirect('uc2:chat')

    msg_id  = request.POST.get('msg_id')
    action  = request.POST.get('action', 'confirm')

    try:
        msg = ChatMessage.objects.get(id=msg_id, has_proposal=True)
        msg.proposal_confirmed = True
        msg.save()
        if action == 'confirm':
            # Log the confirmation
            ChangeLog.objects.create(
                table_name='ChatMessage',
                record_id=str(msg_id),
                field='proposal_confirmed',
                old_value='False',
                new_value='True',
                changed_by='User',
                confirmed_by='User',
            )
            messages.success(request, 'Write proposal confirmed and logged to CHANGE_LOG.')
        else:
            messages.info(request, 'Write proposal rejected.')
    except ChatMessage.DoesNotExist:
        messages.error(request, 'Message not found.')

    return redirect('uc2:chat')


def chat_reset(request):
    """Reset / close the current session."""
    session_key = request.session.get('didi_session_id')
    if session_key:
        try:
            s = ChatSession.objects.get(session_id=session_key)
            s.closed_at = timezone.now()
            s.save()
        except ChatSession.DoesNotExist:
            pass
        del request.session['didi_session_id']
    return redirect('uc2:chat')


# ─── Data Table Views ─────────────────────────────────────────────────────────

def action_hub(request):
    """View / manage ACTION_HUB."""
    if request.method == 'POST':
        action  = request.POST.get('action')
        item_id = request.POST.get('item_id')

        if action == 'create':
            ActionHub.objects.create(
                action   = request.POST.get('action_text', ''),
                owner    = request.POST.get('owner', ''),
                due_date = request.POST.get('due_date') or None,
                priority = request.POST.get('priority', 'medium'),
                status   = 'open',
            )
            messages.success(request, 'Action item created.')
        elif action == 'update_status' and item_id:
            item = get_object_or_404(ActionHub, pk=item_id)
            old_status = item.status
            item.status = request.POST.get('status', item.status)
            item.save()
            ChangeLog.objects.create(
                table_name='ActionHub', record_id=item_id,
                field='status', old_value=old_status, new_value=item.status,
                changed_by='User',
            )
            messages.success(request, f'Action item updated to {item.get_status_display()}.')
        return redirect('uc2:action_hub')

    items = ActionHub.objects.all()
    status_f = request.GET.get('status', '')
    if status_f:
        items = items.filter(status=status_f)
    return render(request, 'uc2_didi/action_hub.html', {
        'items': items, 'status_filter': status_f,
    })


def budget_view(request):
    budgets = Budget.objects.select_related('category').all()
    total_est = sum(float(b.estimated) for b in budgets)
    total_act = sum(float(b.actual) for b in budgets)
    return render(request, 'uc2_didi/budget.html', {
        'budgets': budgets,
        'total_estimated': total_est,
        'total_actual': total_act,
    })


def phases_view(request):
    phases = ProjectPhase.objects.all().order_by('order')
    return render(request, 'uc2_didi/phases.html', {'phases': phases})


def decisions_view(request):
    if request.method == 'POST':
        Decision.objects.create(
            description = request.POST.get('description', ''),
            made_by     = request.POST.get('made_by', ''),
            date        = request.POST.get('date') or None,
            status      = request.POST.get('status', 'confirmed'),
            rationale   = request.POST.get('rationale', ''),
        )
        messages.success(request, 'Decision recorded.')
        return redirect('uc2:decisions')
    decisions = Decision.objects.all()
    return render(request, 'uc2_didi/decisions.html', {'decisions': decisions})


def vendors_view(request):
    vendors = Vendor.objects.all()
    return render(request, 'uc2_didi/vendors.html', {'vendors': vendors})


def learning_rules_view(request):
    rules = LearningRule.objects.all()
    pending_hyp = Hypothesis.objects.filter(status='pending')
    return render(request, 'uc2_didi/learning_rules.html', {
        'rules': rules, 'pending_hyp': pending_hyp,
    })


def hypothesis_promote(request, pk):
    """Promote a hypothesis to a learning rule."""
    hyp = get_object_or_404(Hypothesis, pk=pk)
    if request.method == 'POST':
        rule_code = f"LRN-{LearningRule.objects.count() + 1:04d}"
        LearningRule.objects.create(
            rule_code=rule_code,
            description=hyp.description,
            category='Promoted',
            source=hyp,
            is_active=True,
        )
        hyp.status = 'promoted'
        hyp.reviewed_at = timezone.now()
        hyp.reviewed_by = 'User'
        hyp.save()
        messages.success(request, f'Hypothesis promoted to {rule_code}.')
    return redirect('uc2:learning_rules')


def change_log_view(request):
    logs = ChangeLog.objects.all()[:100]
    return render(request, 'uc2_didi/change_log.html', {'logs': logs})


def procurement_view(request):
    if request.method == 'POST':
        qty   = float(request.POST.get('quantity', 1))
        price = float(request.POST.get('unit_price', 0))
        Procurement.objects.create(
            item        = request.POST.get('item', ''),
            vendor_name = request.POST.get('vendor_name', ''),
            quantity    = qty,
            unit_price  = price,
            total       = qty * price,
            status      = request.POST.get('status', 'pending'),
            due_date    = request.POST.get('due_date') or None,
        )
        messages.success(request, 'Procurement record added.')
        return redirect('uc2:procurement')
    items = Procurement.objects.all()
    return render(request, 'uc2_didi/procurement.html', {'items': items})
