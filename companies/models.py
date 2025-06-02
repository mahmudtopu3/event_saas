# companies/models.py
from django.db import models
from django_tenants.models import TenantMixin, DomainMixin

class TimeStampedModel(models.Model):
    """Abstract base model that provides self-updating created_at and updated_at fields"""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        abstract = True
        
class Company(TenantMixin,TimeStampedModel):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to='company_logos/', null=True, blank=True, help_text="Company logo")
    phone = models.CharField(max_length=20, blank=True)
    paid_until = models.DateField(null=True, blank=True)
    on_trial = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    is_active_subscription = models.BooleanField(
        default=False,
        help_text="If False, this tenant's site is inactive (no logins)."
    )
    
 
    
    
    
    # Auto-create schema when company is created
    auto_create_schema = True
    auto_drop_schema = True

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Companies"
        ordering = ['name']

class Domain(DomainMixin):
    """
    Domain model for tenant domains.
    Inherits domain, tenant FK, and is_primary from DomainMixin
    """
    pass