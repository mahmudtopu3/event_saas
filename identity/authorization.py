# identity/authorization.py
"""
EventOS organizer authorization.

Organizer access requires ALL of:
  authenticated PersonID
  + tenant linked to the requested canonical EmployerID (the tenant decides the employer; a query string never does)
  + canonical employer exists and is active
  + EVENTOS entitlement active for that employer
  + active canonical membership of the person in that employer
  + an EventOS permission (an organizer-grade membership role, or an active TenantRoleAssignment)

It's evaluated on every request, never cached in the session, so a central
revocation (membership, entitlement, employer status) takes effect on the next request.
Any upstream failure fails closed.
"""
from dataclasses import dataclass, field
from typing import Optional

from django.conf import settings
from django_tenants.utils import get_public_schema_name, schema_context

from .employer_client import get_employer_client
from .entitlement_client import EVENTOS, get_entitlement_client
from .exceptions import UpstreamUnavailable
from .observability import audit
from .service_http import CallContext


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    employer_id: Optional[int] = None
    roles: frozenset = field(default_factory=frozenset)

    def __bool__(self):
        return self.allowed


def _organizer_membership_roles():
    return frozenset(getattr(settings, 'EVENTOS_ORGANIZER_MEMBERSHIP_ROLES', ('owner', 'admin')))


class EventOSAccessService:
    def can_use_eventos(self, employer_id, person_id, tenant):
        decision = self._evaluate(employer_id, person_id, tenant)
        if not decision.allowed:
            audit('tenant.access.denied', person_id=person_id, employer_id=employer_id,
                  tenant=getattr(tenant, 'schema_name', None), reason=decision.reason)
        return decision

    def organizer_access(self, request):
        """Organizer decision for the current request's person and tenant."""
        memo = getattr(request, '_eventos_organizer_decision', None)
        if memo is not None:
            return memo
        principal = getattr(request, 'principal', None)
        tenant = getattr(request, 'tenant', None)
        employer_id = getattr(tenant, 'canonical_employer_id', None)
        decision = self.can_use_eventos(employer_id, principal.person_id if principal else None, tenant)
        request._eventos_organizer_decision = decision
        return decision

    # ─────────────────────────────────────────────────────────────────────
    def _evaluate(self, employer_id, person_id, tenant):
        if person_id is None:
            return AccessDecision(False, 'unauthenticated')
        if tenant is None or tenant.schema_name == get_public_schema_name():
            return AccessDecision(False, 'no_tenant')
        if tenant.canonical_employer_id is None:
            return AccessDecision(False, 'tenant_not_linked')
        if employer_id is None or int(employer_id) != int(tenant.canonical_employer_id):
            return AccessDecision(False, 'employer_tenant_mismatch')

        ctx = CallContext(purpose='eventos.organizer_access', tenant_schema=tenant.schema_name,
                          canonical_tenant_id=tenant.canonical_tenant_id)
        try:
            employer = get_employer_client().get_employer(employer_id, ctx)
            if employer is None:
                return AccessDecision(False, 'employer_not_found', employer_id)
            if employer.status != 'active':
                return AccessDecision(False, 'employer_inactive', employer_id)

            entitlement = get_entitlement_client().get_entitlement(employer_id, EVENTOS, ctx)
            audit('eventos.entitlement.checked', employer_id=employer_id,
                  status=entitlement.status if entitlement else 'none')
            if entitlement is None or not entitlement.is_active:
                return AccessDecision(False, 'entitlement_inactive', employer_id)

            membership = get_employer_client().get_membership(person_id, employer_id, ctx)
            audit('employer.membership.checked', person_id=person_id, employer_id=employer_id,
                  status=membership.status if membership else 'none')
            if membership is None or not membership.is_active:
                return AccessDecision(False, 'not_a_member', employer_id)
        except UpstreamUnavailable as exc:
            return AccessDecision(False, f'upstream_unavailable:{exc.code}', employer_id)

        roles = set(membership.roles & _organizer_membership_roles())
        roles |= self._tenant_roles(person_id, tenant)
        if not roles:
            return AccessDecision(False, 'no_eventos_permission', employer_id)
        return AccessDecision(True, 'ok', employer_id, frozenset(roles))

    @staticmethod
    def _tenant_roles(person_id, tenant):
        from .models import TenantRoleAssignment

        with schema_context(tenant.schema_name):
            return set(TenantRoleAssignment.objects.filter(person_id=person_id, revoked_at__isnull=True)
                       .values_list('role', flat=True))


access_service = EventOSAccessService()


def can_use_eventos(employer_id, person_id, tenant):
    return access_service.can_use_eventos(employer_id, person_id, tenant)
