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
    `is_primary` from DomainMixin. No extra fields needed.
    """
    pass


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
