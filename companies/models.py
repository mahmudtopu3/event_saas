# companies/models.py

from django.db import models
from django_tenants.models import TenantMixin, DomainMixin
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Abstract base model that provides self-updating created_at and updated_at fields."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        abstract = True


class Plan(models.Model):
    """
    Defines pre-set subscription plans. Each plan has a name, description,
    price, billing period (monthly/yearly), and an active flag.
    """
    BILLING_CHOICES = [
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    ]

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    billing_period = models.CharField(
        max_length=20,
        choices=BILLING_CHOICES,
        default='monthly',
        help_text="Billing cadence for this plan."
    )
    features = models.TextField(
        help_text="List features of this plan, one per line."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to retire a plan so that new orders cannot choose it."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['price']

    def __str__(self):
        return f"{self.name} - ${self.price}/{self.billing_period}"


class Company(TenantMixin, TimeStampedModel):
    """
    Each Company is a tenant. The public schema Admin can toggle
    is_active_subscription. We also track current_plan, subscription_start,
    and paid_until (which serves as subscription_end).
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    logo = models.ImageField(
        upload_to='company_logos/',
        null=True,
        blank=True,
        help_text="Company logo (optional)"
    )
    phone = models.CharField(max_length=20, blank=True)

    # When the subscription starts (auto‐set when an order is approved)
    subscription_start = models.DateTimeField(null=True, blank=True)

    # When the subscription ends (the tenant is inactive if now > paid_until)
    paid_until = models.DateField(
        null=True,
        blank=True,
        help_text="Date when the subscription expires."
    )

    on_trial = models.BooleanField(
        default=True,
        help_text="Whether this company is still on trial."
    )

    is_active_subscription = models.BooleanField(
        default=False,
        help_text="If False (or expired), this tenant’s site is inactive."
    )

    # Link to the Plan the company is currently on:
    current_plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The subscription plan this company is currently using."
    )

    # ── Canonical Skill.jobs linkage ──────────────────────────────────────
    # EventOS does not own employer identity. These are opaque references to the
    # canonical Employer / Tenant records (bigint, the Skill.jobs convention) and are
    # never used as a human identity. Name/description/contact fields above are local
    # EventOS display/config data; see docs/EVENTOS_COMPANY_INTEGRATION.md.
    canonical_employer_id = models.BigIntegerField(
        null=True, blank=True, unique=True,
        help_text="Canonical Skill.jobs EmployerID this EventOS tenant belongs to.",
    )
    canonical_tenant_id = models.BigIntegerField(
        null=True, blank=True, db_index=True,
        help_text="Canonical Skill.jobs TenantID/OrganisationID, when the platform issues one.",
    )

    # Auto-create/drop schema on Company create/delete
    auto_create_schema = True
    auto_drop_schema = True

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Companies"
        ordering = ['name']


class Domain(DomainMixin):
    """
    Domain model for tenant domains. Inherits `domain`, `tenant` FK, and
    `is_primary` from DomainMixin.

    Routing and SSO are gated separately: a domain routes while `is_active`, but it
    may only start/finish a Keycloak login once `oidc_callback_approved` is set (and
    its exact callback URL is registered on the Keycloak client).
    """
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive domains no longer route to the tenant (served as unknown host).",
    )
    ownership_verified_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When DNS/administrative ownership of this domain was confirmed.",
    )
    tls_ready = models.BooleanField(default=False, help_text="HTTPS certificate is provisioned for this host.")
    oidc_callback_approved = models.BooleanField(
        default=False,
        help_text="Host may run the Keycloak login flow. Its callback URL must also be registered in Keycloak.",
    )
    approved_by = models.CharField(max_length=150, blank=True)
    disabled_reason = models.CharField(max_length=255, blank=True)

    def oidc_redirect_uri(self, scheme='https'):
        return f'{scheme}://{self.domain}/auth/callback/'


class DomainAuditEvent(models.Model):
    """Append-only record of custom-domain lifecycle and SSO approval changes."""
    ACTIONS = [
        ('created', 'Created'),
        ('ownership_verified', 'Ownership verified'),
        ('tls_ready', 'TLS ready'),
        ('oidc_approved', 'OIDC callback approved'),
        ('oidc_revoked', 'OIDC callback revoked'),
        ('activated', 'Activated'),
        ('disabled', 'Disabled'),
    ]
    domain = models.ForeignKey(Domain, on_delete=models.SET_NULL, null=True, related_name='audit_events')
    domain_name = models.CharField(max_length=253)
    tenant_schema = models.CharField(max_length=63)
    action = models.CharField(max_length=32, choices=ACTIONS)
    actor = models.CharField(max_length=150)
    detail = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-occurred_at']

    def __str__(self):
        return f"{self.domain_name}: {self.action} by {self.actor}"


class Order(models.Model):
    """
    When a tenant wants to subscribe (or change plan), they place an Order.
    The public‐schema Admin can then approve/reject. Upon approval, the
    Company’s subscription is created/extended.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    BILLING_CHOICES = Plan.BILLING_CHOICES

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='orders'
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        help_text="Which plan the company wants to purchase."
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        help_text="Order status: pending → approved → rejected."
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Total cost of this order (e.g. plan.price)."
    )
    billing_period = models.CharField(
        max_length=20,
        choices=BILLING_CHOICES,
        help_text="Billing cadence for this order (copied from Plan)."
    )
    notes = models.TextField(
        blank=True,
        help_text="Optional notes or special requirements."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.id} — {self.company.name} — {self.plan.name} ({self.status})"
