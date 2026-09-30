# companies/provisioning.py
"""
Idempotent EventOS tenant provisioning for a canonical Skill.jobs employer,
plus auditable custom-domain lifecycle operations.
"""
import re
from dataclasses import dataclass, field

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

from identity.observability import audit

from .models import Company, Domain, DomainAuditEvent

_LABEL = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
_HOST = re.compile(r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')


class ProvisioningError(Exception):
    pass


@dataclass
class ProvisionResult:
    company: Company
    created: bool
    domains: list = field(default_factory=list)


def schema_name_for_employer(canonical_employer_id):
    return f'emp_{int(canonical_employer_id)}'


def _record(domain, action, actor, **detail):
    DomainAuditEvent.objects.create(domain=domain, domain_name=domain.domain, tenant_schema=domain.tenant.schema_name,
                                    action=action, actor=actor, detail=detail)
    audit('tenant.domain.' + action, domain=domain.domain, tenant=domain.tenant.schema_name, actor=actor)


def _attach_domain(company, hostname, *, is_primary, custom, actor):
    existing = Domain.objects.filter(domain=hostname).select_related('tenant').first()
    if existing:
        if existing.tenant_id != company.pk:
            raise ProvisioningError(f'domain {hostname} already belongs to another tenant')
        return existing
    try:
        with transaction.atomic():
            domain = Domain.objects.create(
                domain=hostname, tenant=company, is_primary=is_primary,
                # A custom domain does not route until ownership is verified and it is activated.
                is_active=not custom,
            )
    except IntegrityError:
        # Concurrent retry created it first; re-run the ownership check on the winner.
        return _attach_domain(company, hostname, is_primary=is_primary, custom=custom, actor=actor)
    _record(domain, 'created', actor, custom=custom)
    return domain


def provision_eventos_tenant(canonical_employer_id, requested_domain=None, requested_subdomain=None,
                             *, display_name=None, actor='system'):
    """
    Return the EventOS tenant for `canonical_employer_id`, creating it if needed.

    Safe to retry: the tenant is keyed by the canonical EmployerID (unique) and its schema
    name is derived from it, so concurrent or repeated calls converge on one tenant.
    Only tenant-infrastructure and EventOS-config data is stored locally; the
    employer's canonical profile stays in Skill.jobs.
    """
    employer_id = int(canonical_employer_id)
    if employer_id <= 0:
        raise ProvisioningError('canonical_employer_id must be positive')
    subdomain = (requested_subdomain or '').strip().lower() or None
    custom = (requested_domain or '').strip().lower().rstrip('.') or None
    if subdomain and not _LABEL.match(subdomain):
        raise ProvisioningError('invalid subdomain label')
    if custom and not _HOST.match(custom):
        raise ProvisioningError('invalid custom domain')

    with schema_context(get_public_schema_name()):
        company = Company.objects.filter(canonical_employer_id=employer_id).first()
        created = False
        if company is None:
            schema = schema_name_for_employer(employer_id)
            if Company.objects.filter(schema_name=schema).exists():
                raise ProvisioningError(f'schema {schema} exists without canonical link; resolve manually')
            try:
                with transaction.atomic():
                    company = Company(
                        schema_name=schema,
                        canonical_employer_id=employer_id,
                        # Local label only (Company.name is unique); canonical name lives in Skill.jobs.
                        name=(display_name or f'Employer {employer_id}')[:100],
                        on_trial=True,
                        is_active_subscription=False,
                    )
                    company.save(verbosity=0)
                created = True
            except IntegrityError:
                company = Company.objects.filter(canonical_employer_id=employer_id).first()   # lost a race?
                if company is None:
                    raise ProvisioningError('tenant name or schema already in use; pass a different display_name')

        domains = []
        if subdomain:
            base = settings.EVENTOS_PLATFORM_BASE_DOMAIN
            domains.append(_attach_domain(company, f'{subdomain}.{base}', is_primary=not company.domains.exists(),
                                          custom=False, actor=actor))
        if custom:
            domains.append(_attach_domain(company, custom, is_primary=False, custom=True, actor=actor))

    audit('tenant.provisioned', employer_id=employer_id, tenant=company.schema_name, created=created)
    return ProvisionResult(company=company, created=created, domains=domains)


# ── custom-domain lifecycle ───────────────────────────────────────────────
def verify_ownership(domain, actor, method):
    domain.ownership_verified_at = timezone.now()
    domain.save(update_fields=['ownership_verified_at'])
    _record(domain, 'ownership_verified', actor, method=method)


def mark_tls_ready(domain, actor):
    domain.tls_ready = True
    domain.save(update_fields=['tls_ready'])
    _record(domain, 'tls_ready', actor)


def activate(domain, actor):
    if domain.ownership_verified_at is None and not _is_platform_domain(domain.domain):
        raise ProvisioningError('custom domain ownership must be verified before activation')
    domain.is_active, domain.disabled_reason = True, ''
    domain.save(update_fields=['is_active', 'disabled_reason'])
    _record(domain, 'activated', actor)


def approve_oidc(domain, actor):
    """Allow this host to run Keycloak login. Its exact callback URL must also be registered in Keycloak."""
    if not domain.is_active:
        raise ProvisioningError('domain must be active')
    if domain.ownership_verified_at is None and not _is_platform_domain(domain.domain):
        raise ProvisioningError('custom domain ownership must be verified first')
    domain.oidc_callback_approved, domain.approved_by = True, actor
    domain.save(update_fields=['oidc_callback_approved', 'approved_by'])
    _record(domain, 'oidc_approved', actor, redirect_uri=domain.oidc_redirect_uri())


def revoke_oidc(domain, actor, reason=''):
    domain.oidc_callback_approved = False
    domain.save(update_fields=['oidc_callback_approved'])
    _record(domain, 'oidc_revoked', actor, reason=reason)


def disable(domain, actor, reason):
    domain.is_active, domain.oidc_callback_approved, domain.disabled_reason = False, False, reason[:255]
    domain.save(update_fields=['is_active', 'oidc_callback_approved', 'disabled_reason'])
    _record(domain, 'disabled', actor, reason=reason)


def _is_platform_domain(hostname):
    return hostname.endswith('.' + settings.EVENTOS_PLATFORM_BASE_DOMAIN)
