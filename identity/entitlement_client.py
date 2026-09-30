# identity/entitlement_client.py
"""
Commercial capability ("is EVENTOS enabled for this employer?").

Separate from authentication (Keycloak) and from membership (who belongs to the
employer). No Keycloak role is ever read as an entitlement.
"""
from dataclasses import dataclass

from django.utils import timezone
from django_tenants.utils import schema_context

from .exceptions import UpstreamUnavailable
from .service_http import load_client

EVENTOS = 'EVENTOS'
ACTIVE = 'active'


@dataclass(frozen=True)
class Entitlement:
    employer_id: int
    capability: str
    status: str                       # active | disabled | expired | requested

    @property
    def is_active(self):
        return self.status == ACTIVE


class EntitlementClient:
    def get_entitlement(self, employer_id, capability, ctx):
        """Entitlement or None when the employer has never had the capability."""
        raise NotImplementedError


class UnavailableEntitlementClient(EntitlementClient):
    def get_entitlement(self, employer_id, capability, ctx):
        raise UpstreamUnavailable('no entitlement authority configured', code='entitlement_not_configured')


class InMemoryEntitlementClient(EntitlementClient):
    def __init__(self):
        self.entitlements = {}        # (employer_id, capability) -> status
        self.fail = False

    def set(self, employer_id, status=ACTIVE, capability=EVENTOS):
        self.entitlements[(employer_id, capability)] = status

    def get_entitlement(self, employer_id, capability, ctx):
        if self.fail:
            raise UpstreamUnavailable('simulated outage', code='upstream_unreachable')
        status = self.entitlements.get((employer_id, capability))
        return Entitlement(employer_id, capability, status) if status else None


class LocalTenantSubscriptionEntitlementClient(EntitlementClient):
    """
    Interim authority (OPEN DECISION): treat the existing EventOS tenant subscription
    of the tenant linked to `employer_id` as its EVENTOS entitlement. This reuses the
    commercial state EventOS already owns, until a canonical entitlement API exists.
    """

    def get_entitlement(self, employer_id, capability, ctx):
        if capability != EVENTOS:
            return None
        from companies.models import Company

        with schema_context('public'):
            company = Company.objects.filter(canonical_employer_id=employer_id).first()
        if company is None:
            return None
        expired = company.paid_until and company.paid_until < timezone.now().date()
        status = ACTIVE if company.is_active_subscription and not expired else 'disabled'
        return Entitlement(employer_id, capability, status)


def get_entitlement_client():
    return load_client('EVENTOS_ENTITLEMENT_CLIENT', 'identity.entitlement_client.UnavailableEntitlementClient')
