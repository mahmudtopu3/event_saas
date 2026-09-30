# identity/employer_client.py
"""
Canonical Employer and employer-membership adapter.

The canonical backend doesn't expose a person→employer membership API yet (see
docs/EVENTOS_COMPANY_INTEGRATION.md, OPEN_DECISIONS). Until one is configured the
default client fails closed, so no one gains organizer access through Keycloak.
"""
from dataclasses import dataclass, field
from typing import Optional

from .exceptions import UpstreamUnavailable
from .service_http import load_client

ACTIVE = 'active'


@dataclass(frozen=True)
class Employer:
    employer_id: int
    status: str = ACTIVE              # active | suspended | closed
    verification_state: str = ''


@dataclass(frozen=True)
class EmployerMembership:
    person_id: int
    employer_id: int
    status: str = ACTIVE              # active | revoked | suspended
    roles: frozenset = field(default_factory=frozenset)
    recruiter_id: Optional[int] = None

    @property
    def is_active(self):
        return self.status == ACTIVE


class EmployerClient:
    def get_employer(self, employer_id, ctx):
        raise NotImplementedError

    def get_membership(self, person_id, employer_id, ctx):
        """The person's membership in that employer, or None."""
        raise NotImplementedError


class UnavailableEmployerClient(EmployerClient):
    def get_employer(self, employer_id, ctx):
        raise UpstreamUnavailable('no canonical employer API configured', code='employer_not_configured')

    def get_membership(self, person_id, employer_id, ctx):
        raise UpstreamUnavailable('no canonical membership API configured', code='membership_not_configured')


class InMemoryEmployerClient(EmployerClient):
    def __init__(self):
        self.employers = {}
        self.memberships = {}         # (person_id, employer_id) -> EmployerMembership
        self.fail = False

    def add_member(self, person_id, employer_id, roles=('recruiter',), status=ACTIVE, recruiter_id=None):
        self.memberships[(person_id, employer_id)] = EmployerMembership(
            person_id=person_id, employer_id=employer_id, status=status,
            roles=frozenset(roles), recruiter_id=recruiter_id,
        )

    def _check(self):
        if self.fail:
            raise UpstreamUnavailable('simulated outage', code='upstream_unreachable')

    def get_employer(self, employer_id, ctx):
        self._check()
        return self.employers.get(employer_id)

    def get_membership(self, person_id, employer_id, ctx):
        self._check()
        return self.memberships.get((person_id, employer_id))


def get_employer_client():
    return load_client('EVENTOS_EMPLOYER_CLIENT', 'identity.employer_client.UnavailableEmployerClient')
