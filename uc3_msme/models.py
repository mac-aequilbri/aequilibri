"""UC3 MSME Project Coordinator — Database Models (Multi-tenant)"""
import secrets as _secrets
from django.db import models


ROLE_CHOICES = [
    ('admin',      'Admin / Owner'),
    ('pm',         'Project Manager'),
    ('supervisor', 'Site Supervisor'),
    ('finance',    'Finance Officer'),
    ('readonly',   'Read-Only Viewer'),
]
PROJECT_STATUS = [
    ('planning',   'Planning'),
    ('active',     'Active'),
    ('on_hold',    'On Hold'),
    ('complete',   'Complete'),
]
PHASE_STATUS = [
    ('not_started', 'Not Started'),
    ('in_progress', 'In Progress'),
    ('complete',    'Complete'),
    ('on_hold',     'On Hold'),
]
ACTION_STATUS = [
    ('open',        'Open'),
    ('in_progress', 'In Progress'),
    ('complete',    'Complete'),
    ('overdue',     'Overdue'),
    ('cancelled',   'Cancelled'),
]
PRIORITY = [
    ('low',      'Low'),
    ('medium',   'Medium'),
    ('high',     'High'),
    ('critical', 'Critical'),
]
RISK_STATUS = [
    ('open',      'Open'),
    ('mitigated', 'Mitigated'),
    ('closed',    'Closed'),
    ('accepted',  'Accepted'),
]
LIKELIHOOD = [(1,'1-Rare'),(2,'2-Unlikely'),(3,'3-Possible'),(4,'4-Likely'),(5,'5-Almost Certain')]
IMPACT     = [(1,'1-Negligible'),(2,'2-Minor'),(3,'3-Moderate'),(4,'4-Major'),(5,'5-Catastrophic')]
DECISION_STATUS = [('draft','Draft'),('confirmed','Confirmed'),('superseded','Superseded')]
VENDOR_RATING = [(i, str(i)) for i in range(1, 11)]


# ─── Multi-Tenant ─────────────────────────────────────────────────────────────

class Tenant(models.Model):
    """One row per construction SME using the platform."""
    name        = models.CharField(max_length=200, unique=True)
    org_id      = models.CharField(max_length=100, unique=True,
                                    help_text='Clerk organisation ID')
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self): return self.name
    class Meta: verbose_name = 'Tenant'; ordering = ['name']


# ─── Project & Phases ─────────────────────────────────────────────────────────

class Project(models.Model):
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='projects')
    name            = models.CharField(max_length=300)
    client          = models.CharField(max_length=200, blank=True)
    status          = models.CharField(max_length=30, choices=PROJECT_STATUS, default='planning')
    start_date      = models.DateField(null=True, blank=True)
    end_date        = models.DateField(null=True, blank=True)
    description     = models.TextField(blank=True)
    # Health score = composite 0-100 (AI writes this)
    health_score    = models.IntegerField(default=50,
                                           help_text='0-100 composite project health score')
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    @property
    def health_label(self):
        if self.health_score >= 80: return 'Healthy'
        if self.health_score >= 60: return 'At Risk'
        return 'Critical'

    @property
    def health_color(self):
        if self.health_score >= 80: return '#27ae60'
        if self.health_score >= 60: return '#f39c12'
        return '#e74c3c'

    def __str__(self): return f"{self.name} ({self.tenant.name})"
    class Meta: verbose_name = 'Project'; ordering = ['-created_at']


class Phase(models.Model):
    project         = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='phases')
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    name            = models.CharField(max_length=200)
    status          = models.CharField(max_length=30, choices=PHASE_STATUS, default='not_started')
    completion_pct  = models.IntegerField(default=0)
    start_date      = models.DateField(null=True, blank=True)
    end_date        = models.DateField(null=True, blank=True)
    order           = models.PositiveSmallIntegerField(default=0)
    # Drafts require human approval before confirmed
    is_ai_draft     = models.BooleanField(default=False)
    approved_by     = models.CharField(max_length=200, blank=True)

    def __str__(self): return f"{self.project.name} / Phase {self.order}: {self.name}"
    class Meta: verbose_name = 'Phase'; ordering = ['order']


# ─── Action Items — AI has full write authority ────────────────────────────────

class ActionItem(models.Model):
    project         = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='actions')
    phase           = models.ForeignKey(Phase, on_delete=models.SET_NULL, null=True, blank=True)
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    title           = models.CharField(max_length=300)
    description     = models.TextField(blank=True)
    owner           = models.CharField(max_length=200, blank=True)
    due_date        = models.DateField(null=True, blank=True)
    priority        = models.CharField(max_length=20, choices=PRIORITY, default='medium')
    status          = models.CharField(max_length=30, choices=ACTION_STATUS, default='open')
    created_by_ai   = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    def __str__(self): return f"[{self.get_priority_display()}] {self.title}"
    class Meta: verbose_name = 'Action Item'; ordering = ['-priority', 'due_date']


# ─── Risks — AI has full write authority ─────────────────────────────────────

class Risk(models.Model):
    project         = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='risks')
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    description     = models.TextField()
    likelihood      = models.IntegerField(choices=LIKELIHOOD, default=2)
    impact          = models.IntegerField(choices=IMPACT, default=2)
    mitigation      = models.TextField(blank=True)
    status          = models.CharField(max_length=30, choices=RISK_STATUS, default='open')
    owner           = models.CharField(max_length=200, blank=True)
    created_by_ai   = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True)
    escalated_at    = models.DateTimeField(null=True, blank=True)
    escalation_note = models.TextField(blank=True)

    @property
    def risk_score(self):
        return self.likelihood * self.impact

    @property
    def risk_level(self):
        s = self.risk_score
        if s >= 15: return 'HIGH'
        if s >= 8:  return 'MEDIUM'
        return 'LOW'

    def __str__(self): return f"[{self.risk_level}] {self.description[:80]}"
    class Meta: verbose_name = 'Risk'; ordering = ['-likelihood', '-impact']


# ─── Budget — AI reads; Finance Officer writes actuals ─────────────────────────

class Budget(models.Model):
    project         = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='budgets')
    phase           = models.ForeignKey(Phase, on_delete=models.SET_NULL, null=True, blank=True)
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    description     = models.CharField(max_length=300)
    estimated       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    actual          = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def variance(self):
        return round(float(self.actual) - float(self.estimated), 2)

    @property
    def variance_pct(self):
        if float(self.estimated) == 0: return 0
        return round((self.variance / float(self.estimated)) * 100, 1)

    def __str__(self): return f"{self.project.name} — {self.description}"
    class Meta: verbose_name = 'Budget Line'; ordering = ['description']


# ─── Vendors — AI updates rating only ─────────────────────────────────────────

class Vendor(models.Model):
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='vendors')
    name            = models.CharField(max_length=200)
    category        = models.CharField(max_length=100, blank=True)
    contact_name    = models.CharField(max_length=200, blank=True)
    contact_email   = models.EmailField(blank=True)
    contact_phone   = models.CharField(max_length=30, blank=True)
    rating          = models.IntegerField(choices=VENDOR_RATING, default=8)
    notes           = models.TextField(blank=True)
    is_active       = models.BooleanField(default=True)

    def __str__(self): return f"{self.name} ({self.tenant.name})"
    class Meta: verbose_name = 'Vendor'; ordering = ['name']


# ─── Decisions — AI drafts; human confirms ────────────────────────────────────

class Decision(models.Model):
    project         = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='decisions')
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    description     = models.TextField()
    rationale       = models.TextField(blank=True)
    status          = models.CharField(max_length=30, choices=DECISION_STATUS, default='draft')
    is_ai_draft     = models.BooleanField(default=False)
    drafted_by      = models.CharField(max_length=200, default='AI')
    confirmed_by    = models.CharField(max_length=200, blank=True)
    confirmed_at    = models.DateTimeField(null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"[{self.get_status_display()}] {self.description[:80]}"
    class Meta: verbose_name = 'Decision'; ordering = ['-created_at']


# ─── Documents — human uploads; AI reads ──────────────────────────────────────

class Document(models.Model):
    project         = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='documents')
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    name            = models.CharField(max_length=300)
    doc_type        = models.CharField(max_length=100, blank=True)
    version         = models.CharField(max_length=20, default='1.0')
    upload_date     = models.DateField(null=True, blank=True)
    uploaded_by     = models.CharField(max_length=200, blank=True)
    notes           = models.TextField(blank=True)
    file_content    = models.TextField(blank=True, help_text='Paste document content for AI analysis')
    ai_analysis     = models.TextField(blank=True)
    analyzed_at     = models.DateTimeField(null=True, blank=True)

    def __str__(self): return f"{self.name} v{self.version}"
    class Meta: verbose_name = 'Document'; ordering = ['name']


# ─── Cashflows — AI reads; updates projected only ─────────────────────────────

class Cashflow(models.Model):
    project         = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='cashflows')
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    period          = models.CharField(max_length=20, help_text='e.g. 2026-05')
    projected       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    actual          = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self): return f"{self.project.name} / {self.period}"
    class Meta: verbose_name = 'Cashflow'; ordering = ['period']


# ─── Users (per tenant) ───────────────────────────────────────────────────────

class TenantUser(models.Model):
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='users')
    email           = models.EmailField()
    name            = models.CharField(max_length=200)
    role            = models.CharField(max_length=30, choices=ROLE_CHOICES, default='readonly')
    mfa_enabled     = models.BooleanField(default=False)
    is_active       = models.BooleanField(default=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"{self.name} ({self.tenant.name}) — {self.get_role_display()}"
    class Meta: verbose_name = 'Tenant User'; unique_together = [('tenant', 'email')]


# ─── Execution Log — append only ──────────────────────────────────────────────

class ExecutionLog(models.Model):
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE,
                                         null=True, blank=True, related_name='exec_logs')
    project         = models.ForeignKey(Project, on_delete=models.SET_NULL,
                                         null=True, blank=True)
    tool_name       = models.CharField(max_length=100)
    payload         = models.TextField(default='{}')
    result          = models.TextField(default='{}')
    status          = models.CharField(max_length=20, default='success')
    ai_authority    = models.CharField(max_length=50, blank=True,
                                        help_text='full_write | approval_required | blocked')
    created_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.tool_name}"
    class Meta: verbose_name = 'Execution Log (UC3)'; ordering = ['-created_at']


# ─── AI Chat (UC3) ────────────────────────────────────────────────────────────

class ChatMessage(models.Model):
    ROLES = [('user','User'),('assistant','AI'),('system','System')]
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE,
                                         null=True, blank=True)
    project         = models.ForeignKey(Project, on_delete=models.SET_NULL,
                                         null=True, blank=True)
    role            = models.CharField(max_length=20, choices=ROLES)
    content         = models.TextField()
    requires_approval = models.BooleanField(default=False)
    approved        = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"[{self.role}] {self.content[:60]}"
    class Meta: ordering = ['created_at']


# ─── Feature 4: Variation Orders ──────────────────────────────────────────────

VO_STATUS = [
    ('draft', 'Draft'),
    ('pending_approval', 'Pending Approval'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('withdrawn', 'Withdrawn'),
]


class VariationOrder(models.Model):
    project          = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='variations')
    tenant           = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    ref_number       = models.CharField(max_length=20, blank=True)
    title            = models.CharField(max_length=300)
    description      = models.TextField()
    scope_change     = models.TextField(blank=True)
    cost_impact      = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                            help_text='Positive = cost increase')
    time_impact_days = models.IntegerField(default=0,
                                            help_text='Positive = schedule delay in days')
    status           = models.CharField(max_length=30, choices=VO_STATUS, default='draft')
    is_ai_drafted    = models.BooleanField(default=False)
    submitted_by     = models.CharField(max_length=200, blank=True)
    approved_by      = models.CharField(max_length=200, blank=True)
    approved_at      = models.DateTimeField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.ref_number and self.project_id:
            count = VariationOrder.objects.filter(project_id=self.project_id).count()
            self.ref_number = f"VO-{self.project_id:03d}-{count + 1:03d}"
        super().save(*args, **kwargs)

    def __str__(self): return f"{self.ref_number}: {self.title}"
    class Meta: verbose_name = 'Variation Order'; ordering = ['-created_at']


# ─── Feature 2: Client Portal ─────────────────────────────────────────────────

class ClientPortalToken(models.Model):
    project     = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='portal_tokens')
    tenant      = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    token       = models.CharField(max_length=64, unique=True, blank=True)
    label       = models.CharField(max_length=200, blank=True,
                                    help_text='e.g. client company name')
    is_active   = models.BooleanField(default=True)
    views_count = models.IntegerField(default=0)
    expires_at  = models.DateField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = _secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self): return f"Portal: {self.project.name} / {self.label or self.token[:12]}"
    class Meta: verbose_name = 'Client Portal Token'; ordering = ['-created_at']


# ─── Feature 1: Accounting Sync ───────────────────────────────────────────────

class AccountingConnection(models.Model):
    PROVIDER    = [('xero', 'Xero'), ('myob', 'MYOB AccountRight'), ('qbo', 'QuickBooks Online')]
    SYNC_STATUS = [('disconnected', 'Disconnected'), ('connected', 'Connected'), ('error', 'Sync Error')]
    tenant         = models.ForeignKey(Tenant, on_delete=models.CASCADE,
                                        related_name='accounting_connections')
    provider       = models.CharField(max_length=20, choices=PROVIDER, default='xero')
    status         = models.CharField(max_length=20, choices=SYNC_STATUS, default='disconnected')
    org_name       = models.CharField(max_length=200, blank=True)
    access_token   = models.TextField(blank=True)
    last_sync      = models.DateTimeField(null=True, blank=True)
    sync_log       = models.TextField(blank=True)
    records_synced = models.IntegerField(default=0)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.tenant.name} — {self.get_provider_display()} ({self.get_status_display()})"
    class Meta: verbose_name = 'Accounting Connection'; ordering = ['-created_at']


# ─── Feature 5: Weekly Reports ────────────────────────────────────────────────

class WeeklyReport(models.Model):
    REPORT_STATUS = [('draft', 'Draft'), ('approved', 'Approved'), ('sent', 'Sent')]
    project         = models.ForeignKey(Project, on_delete=models.CASCADE,
                                         related_name='weekly_reports')
    tenant          = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    week_ending     = models.DateField()
    title           = models.CharField(max_length=300, blank=True)
    content         = models.TextField()
    is_ai_generated = models.BooleanField(default=True)
    status          = models.CharField(max_length=20, choices=REPORT_STATUS, default='draft')
    generated_at    = models.DateTimeField(auto_now_add=True)
    approved_by     = models.CharField(max_length=200, blank=True)
    approved_at     = models.DateTimeField(null=True, blank=True)

    def __str__(self): return f"{self.project.name} — Week ending {self.week_ending}"
    class Meta: verbose_name = 'Weekly Report'; ordering = ['-week_ending']


# ─── Feature 9: Meeting Minutes ───────────────────────────────────────────────

class MeetingMinutes(models.Model):
    MM_STATUS = [
        ('raw', 'Raw'),
        ('processed', 'Actions Extracted'),
        ('confirmed', 'Actions Confirmed'),
    ]
    project           = models.ForeignKey(Project, on_delete=models.CASCADE,
                                           related_name='meeting_minutes')
    tenant            = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    meeting_date      = models.DateField()
    title             = models.CharField(max_length=300, blank=True)
    attendees         = models.CharField(max_length=500, blank=True)
    raw_minutes       = models.TextField()
    extracted_actions = models.TextField(default='[]',
                                          help_text='JSON list of extracted action items')
    actions_count     = models.IntegerField(default=0)
    status            = models.CharField(max_length=30, choices=MM_STATUS, default='raw')
    created_at        = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"{self.project.name} / {self.title or str(self.meeting_date)}"
    class Meta: verbose_name = 'Meeting Minutes'; ordering = ['-meeting_date']
