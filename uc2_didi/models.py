"""
UC2 Didi — Dulong Downs AI Assistant
18-Table Database (SQL equivalent of the Airtable base)
"""
from django.db import models
from django.utils import timezone


# ─── REFERENCE TABLES (4) ─────────────────────────────────────────────────────

class RefBudget(models.Model):
    """REF_BUDGET — Budget category reference."""
    category        = models.CharField(max_length=200, unique=True)
    budget_allocated = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes           = models.TextField(blank=True)

    def __str__(self): return self.category
    class Meta: verbose_name = 'Ref — Budget Category'; ordering = ['category']


class RefCategory(models.Model):
    """REF_CATEGORIES — Work categories."""
    name        = models.CharField(max_length=200, unique=True)
    type        = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)

    def __str__(self): return self.name
    class Meta: verbose_name = 'Ref — Category'; ordering = ['name']


class RefZone(models.Model):
    """REF_ZONES — Building zones / areas."""
    name        = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    area_sqm    = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    def __str__(self): return self.name
    class Meta: verbose_name = 'Ref — Zone'; ordering = ['name']


class Metadata(models.Model):
    """METADATA — Key-value project metadata store."""
    key         = models.CharField(max_length=200, unique=True)
    value       = models.TextField()
    updated_at  = models.DateTimeField(auto_now=True)

    def __str__(self): return f"{self.key}: {self.value[:60]}"
    class Meta: verbose_name = 'Metadata'; ordering = ['key']


# ─── OPERATIONAL TABLES (11) ──────────────────────────────────────────────────

ACTION_STATUS   = [('open','Open'),('in_progress','In Progress'),('complete','Complete'),('overdue','Overdue')]
PRIORITY_LEVELS = [('low','Low'),('medium','Medium'),('high','High'),('critical','Critical')]


class ActionHub(models.Model):
    """ACTION_HUB — Primary action item list."""
    action      = models.CharField(max_length=500)
    owner       = models.CharField(max_length=200)
    due_date    = models.DateField(null=True, blank=True)
    status      = models.CharField(max_length=30, choices=ACTION_STATUS, default='open')
    priority    = models.CharField(max_length=20, choices=PRIORITY_LEVELS, default='medium')
    category    = models.ForeignKey(RefCategory, on_delete=models.SET_NULL, null=True, blank=True)
    zone        = models.ForeignKey(RefZone, on_delete=models.SET_NULL, null=True, blank=True)
    notes       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    def __str__(self): return f"[{self.get_priority_display()}] {self.action[:80]}"
    class Meta: verbose_name = 'Action Hub'; ordering = ['-priority', 'due_date']


class Budget(models.Model):
    """BUDGET — Project budget tracking."""
    category    = models.ForeignKey(RefBudget, on_delete=models.SET_NULL, null=True)
    phase       = models.CharField(max_length=200, blank=True)
    description = models.CharField(max_length=300, blank=True)
    estimated   = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    actual      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    committed   = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def variance(self):
        return round(float(self.actual) - float(self.estimated), 2)

    @property
    def variance_pct(self):
        if float(self.estimated) == 0: return 0
        return round((self.variance / float(self.estimated)) * 100, 1)

    def __str__(self): return f"{self.phase} — {self.description}"
    class Meta: verbose_name = 'Budget'; ordering = ['phase']


class Cashflow(models.Model):
    """CASHFLOWS — Monthly cashflow projections vs actuals."""
    period      = models.CharField(max_length=20, help_text='e.g. 2026-05')
    projected   = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    actual      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes       = models.TextField(blank=True)

    def __str__(self): return f"Cashflow {self.period}"
    class Meta: verbose_name = 'Cashflow'; ordering = ['period']


class ChangeLog(models.Model):
    """CHANGE_LOG — Immutable write audit trail."""
    table_name  = models.CharField(max_length=100)
    record_id   = models.CharField(max_length=50)
    field       = models.CharField(max_length=100)
    old_value   = models.TextField(blank=True)
    new_value   = models.TextField(blank=True)
    changed_by  = models.CharField(max_length=200, default='Didi AI')
    confirmed_by = models.CharField(max_length=200, blank=True)
    timestamp   = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.table_name}.{self.field}"
    class Meta: verbose_name = 'Change Log'; ordering = ['-timestamp']


class Decision(models.Model):
    """DECISIONS — Decision log."""
    description = models.TextField()
    made_by     = models.CharField(max_length=200)
    date        = models.DateField(null=True, blank=True)
    status      = models.CharField(max_length=30,
                    choices=[('draft','Draft'),('confirmed','Confirmed'),('superseded','Superseded')],
                    default='confirmed')
    rationale   = models.TextField(blank=True)
    category    = models.ForeignKey(RefCategory, on_delete=models.SET_NULL, null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"[{self.date}] {self.description[:80]}"
    class Meta: verbose_name = 'Decision'; ordering = ['-date']


class Document(models.Model):
    """DOCUMENTS — Document library."""
    name        = models.CharField(max_length=300)
    doc_type    = models.CharField(max_length=100, blank=True)
    version     = models.CharField(max_length=20, default='1.0')
    zone        = models.ForeignKey(RefZone, on_delete=models.SET_NULL, null=True, blank=True)
    url         = models.URLField(blank=True)
    upload_date = models.DateField(null=True, blank=True)
    notes       = models.TextField(blank=True)

    def __str__(self): return f"{self.name} v{self.version}"
    class Meta: verbose_name = 'Document'; ordering = ['name']


class Procurement(models.Model):
    """PROCUREMENT — Purchase orders / supplier orders."""
    item        = models.CharField(max_length=300)
    vendor_name = models.CharField(max_length=200)
    quantity    = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status      = models.CharField(max_length=50,
                    choices=[('pending','Pending'),('ordered','Ordered'),
                             ('delivered','Delivered'),('invoiced','Invoiced'),('paid','Paid')],
                    default='pending')
    due_date    = models.DateField(null=True, blank=True)
    category    = models.ForeignKey(RefCategory, on_delete=models.SET_NULL, null=True, blank=True)
    notes       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"{self.item} — {self.vendor_name}"
    class Meta: verbose_name = 'Procurement'; ordering = ['-created_at']


class ProjectPhase(models.Model):
    """PROJECT_PHASES — Project construction phases (read-only for Didi)."""
    name            = models.CharField(max_length=200)
    status          = models.CharField(max_length=30,
                        choices=[('not_started','Not Started'),('in_progress','In Progress'),
                                 ('complete','Complete'),('on_hold','On Hold')], default='not_started')
    start_date      = models.DateField(null=True, blank=True)
    end_date        = models.DateField(null=True, blank=True)
    completion_pct  = models.IntegerField(default=0)
    budget_estimate = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    order           = models.PositiveSmallIntegerField(default=0)
    notes           = models.TextField(blank=True)

    def __str__(self): return f"Phase {self.order}: {self.name}"
    class Meta: verbose_name = 'Project Phase'; ordering = ['order']


class ProjectPlan(models.Model):
    """PROJECT_PLAN — Detailed task plan per phase."""
    task            = models.CharField(max_length=300)
    phase           = models.ForeignKey(ProjectPhase, on_delete=models.SET_NULL, null=True, blank=True)
    owner           = models.CharField(max_length=200, blank=True)
    start_date      = models.DateField(null=True, blank=True)
    end_date        = models.DateField(null=True, blank=True)
    status          = models.CharField(max_length=30,
                        choices=[('not_started','Not Started'),('in_progress','In Progress'),('complete','Complete')],
                        default='not_started')
    pct_complete    = models.IntegerField(default=0)
    notes           = models.TextField(blank=True)

    def __str__(self): return self.task
    class Meta: verbose_name = 'Project Plan Task'; ordering = ['phase__order', 'start_date']


class RoomMatrix(models.Model):
    """ROOM_MATRIX — Room-level specification matrix."""
    zone            = models.ForeignKey(RefZone, on_delete=models.SET_NULL, null=True, blank=True)
    room_name       = models.CharField(max_length=200)
    area_sqm        = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    floor_finish    = models.CharField(max_length=100, blank=True)
    wall_finish     = models.CharField(max_length=100, blank=True)
    ceiling_height  = models.DecimalField(max_digits=5, decimal_places=2, default=2.7)
    notes           = models.TextField(blank=True)

    def __str__(self): return f"{self.room_name} ({self.zone})"
    class Meta: verbose_name = 'Room Matrix'; ordering = ['zone__name', 'room_name']


class Vendor(models.Model):
    """VENDORS — Supplier / subcontractor directory (read-only for Didi)."""
    name        = models.CharField(max_length=200, unique=True)
    category    = models.CharField(max_length=100, blank=True)
    contact     = models.CharField(max_length=200, blank=True)
    email       = models.EmailField(blank=True)
    phone       = models.CharField(max_length=30, blank=True)
    rating      = models.DecimalField(max_digits=3, decimal_places=1, default=5.0)
    notes       = models.TextField(blank=True)
    is_active   = models.BooleanField(default=True)

    def __str__(self): return self.name
    class Meta: verbose_name = 'Vendor'; ordering = ['name']


# ─── INTELLIGENCE & AUDIT TABLES (3) ─────────────────────────────────────────

class ExecutionLog(models.Model):
    """EXECUTION_LOG — Append-only AI tool call audit (UC2)."""
    tool_name   = models.CharField(max_length=100)
    payload     = models.TextField(default='{}')
    result      = models.TextField(default='{}')
    status      = models.CharField(max_length=20, default='success')
    duration_ms = models.IntegerField(default=0)
    session_id  = models.CharField(max_length=50, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.tool_name}"
    class Meta: verbose_name = 'Execution Log (UC2)'; ordering = ['-created_at']


class Hypothesis(models.Model):
    """HYPOTHESES — Didi-generated learnings awaiting promotion."""
    description     = models.TextField()
    source_session  = models.CharField(max_length=50, blank=True)
    evidence        = models.TextField(blank=True)
    status          = models.CharField(max_length=30,
                        choices=[('pending','Pending Review'),('approved','Approved'),
                                 ('rejected','Rejected'),('promoted','Promoted to Rule')],
                        default='pending')
    created_at      = models.DateTimeField(auto_now_add=True)
    reviewed_at     = models.DateTimeField(null=True, blank=True)
    reviewed_by     = models.CharField(max_length=200, blank=True)

    def __str__(self): return f"[{self.get_status_display()}] {self.description[:80]}"
    class Meta: verbose_name = 'Hypothesis'; ordering = ['-created_at']


class LearningRule(models.Model):
    """LEARNING_RULES — Validated rules loaded at every session (read-only for Didi)."""
    rule_code       = models.CharField(max_length=20, unique=True)
    description     = models.TextField()
    category        = models.CharField(max_length=100, blank=True)
    is_active       = models.BooleanField(default=True)
    cannot_override = models.BooleanField(default=False,
                        help_text='LRN rules flagged cannot-override are never bypassed')
    source          = models.ForeignKey(Hypothesis, on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='rules')
    created_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"{self.rule_code}: {self.description[:60]}"
    class Meta: verbose_name = 'Learning Rule'; ordering = ['rule_code']


# ─── CHAT SESSION ─────────────────────────────────────────────────────────────

class ChatSession(models.Model):
    """Transient chat sessions — not part of Airtable schema but needed for the Django UI."""
    session_id  = models.CharField(max_length=50, unique=True)
    started_at  = models.DateTimeField(auto_now_add=True)
    closed_at   = models.DateTimeField(null=True, blank=True)
    summary     = models.TextField(blank=True)

    def __str__(self): return f"Session {self.session_id}"
    class Meta: ordering = ['-started_at']


class ChatMessage(models.Model):
    """Individual chat messages within a session."""
    ROLES = [('user','User'),('assistant','Didi'),('system','System')]
    session     = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    role        = models.CharField(max_length=20, choices=ROLES)
    content     = models.TextField()
    tool_calls  = models.TextField(default='[]', help_text='JSON list of tool calls made')
    has_proposal = models.BooleanField(default=False)
    proposal_confirmed = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"[{self.role}] {self.content[:60]}"
    class Meta: ordering = ['created_at']
