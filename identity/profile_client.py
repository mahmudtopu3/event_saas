# identity/profile_client.py
"""
Canonical Person profile adapter.

EventOS stores PersonID only. Anything shown about a person is read from here
at render time, so a name change in CareerOS shows up on the next EventOS render
with no EventOS table touched. There's no sync and no local profile copy.
"""
from dataclasses import dataclass

from .exceptions import UpstreamUnavailable
from .observability import audit
from .service_http import CallContext, backend_url, load_client, request_json


@dataclass(frozen=True)
class PersonSummary:
    """The minimum EventOS renders. Deliberately excludes email/phone/CV/etc."""
    person_id: int
    display_name: str


class ProfileClient:
    def get_me(self, access_token, ctx):
        raise NotImplementedError

    def get_person(self, person_id, ctx):
        raise NotImplementedError

    def resolve_people(self, person_ids, ctx):
        """Bulk lookup → {person_id: PersonSummary}. Missing IDs are simply absent."""
        raise NotImplementedError


class UnavailableProfileClient(ProfileClient):
    """Default until a canonical Person API is configured: every lookup degrades gracefully."""

    def _fail(self):
        raise UpstreamUnavailable('no canonical profile API configured', code='profile_not_configured')

    def get_me(self, access_token, ctx):
        self._fail()

    def get_person(self, person_id, ctx):
        self._fail()

    def resolve_people(self, person_ids, ctx):
        self._fail()


class InMemoryProfileClient(ProfileClient):
    """Test/dev double that behaves like the canonical service (it's the source of truth, not a cache)."""

    def __init__(self):
        self.people = {}
        self.me_by_token = {}
        self.calls = []
        self.fail = False

    def _check(self):
        if self.fail:
            raise UpstreamUnavailable('simulated outage', code='upstream_unreachable')

    def get_me(self, access_token, ctx):
        self._check()
        self.calls.append(('get_me',))
        pid = self.me_by_token.get(access_token)
        return self.people.get(pid)

    def get_person(self, person_id, ctx):
        self._check()
        self.calls.append(('get_person', person_id))
        return self.people.get(person_id)

    def resolve_people(self, person_ids, ctx):
        self._check()
        ids = list(dict.fromkeys(person_ids))
        self.calls.append(('resolve_people', tuple(ids)))
        return {pid: self.people[pid] for pid in ids if pid in self.people}


class BackendMainProfileClient(ProfileClient):
    """
    Adapter for the current Skill.jobs backend.

    Available today: ``GET /api/auth/sso/me/`` with the user's own Keycloak bearer token
    (backendmain accepts tokens carrying the ``skilljobs-api`` audience).
    Not yet available: server-to-server person lookup and bulk resolve. Those raise
    ``UpstreamUnavailable(code='contract_missing')`` so callers degrade gracefully.
    The desired contract is in docs/EVENTOS_COMPANY_INTEGRATION.md.
    """

    def get_me(self, access_token, ctx):
        status, body = request_json('GET', backend_url('/api/auth/sso/me/'), ctx, bearer_token=access_token)
        if status != 200 or not isinstance(body.get('user'), dict):
            raise UpstreamUnavailable('profile lookup rejected', code=f'profile_{status}')
        user = body['user']
        try:
            name = ' '.join(str(p) for p in (user.get('first_name'), user.get('last_name')) if p).strip()
            return PersonSummary(person_id=int(user['id']), display_name=name)
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamUnavailable('malformed profile response', code='profile_bad_body') from exc

    def get_person(self, person_id, ctx):
        raise UpstreamUnavailable('person lookup not provided by canonical API yet', code='contract_missing')

    def resolve_people(self, person_ids, ctx):
        raise UpstreamUnavailable('bulk resolve not provided by canonical API yet', code='contract_missing')


def get_profile_client():
    return load_client('EVENTOS_PROFILE_CLIENT', 'identity.profile_client.UnavailableProfileClient')


FALLBACK_LABEL = 'Skill.jobs member'


def display_name(person_id, *, access_token=None, tenant_schema=None):
    """Live display name for one person; never raises (falls back to a neutral label)."""
    client = get_profile_client()
    ctx = CallContext(purpose='eventos.render_profile', tenant_schema=tenant_schema)
    try:
        person = None
        try:
            person = client.get_person(person_id, ctx)
        except UpstreamUnavailable as exc:
            if exc.code != 'contract_missing' or not access_token:
                raise
            person = client.get_me(access_token, ctx)
            if person and person.person_id != person_id:
                person = None
        audit('identity.person.resolved', person_id=person_id, found=bool(person))
        return (person.display_name if person and person.display_name else FALLBACK_LABEL)
    except UpstreamUnavailable as exc:
        audit('identity.person.resolve_failed', person_id=person_id, reason=exc.code)
        return FALLBACK_LABEL


def display_names(person_ids, *, tenant_schema=None):
    """Bulk live display names ({id: name}) with one upstream call; never raises."""
    ids = [pid for pid in dict.fromkeys(person_ids) if pid is not None]
    if not ids:
        return {}
    ctx = CallContext(purpose='eventos.render_attendees', tenant_schema=tenant_schema)
    try:
        found = get_profile_client().resolve_people(ids, ctx)
        audit('identity.person.resolved', count=len(ids), found=len(found))
    except UpstreamUnavailable as exc:
        audit('identity.person.resolve_failed', count=len(ids), reason=exc.code)
        found = {}
    return {pid: (found[pid].display_name if pid in found and found[pid].display_name else f'Person #{pid}')
            for pid in ids}
