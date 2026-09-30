# identity/principal.py
"""
The request Principal: *who* is calling, identified by canonical PersonID.

A Principal carries identifiers and authentication context only. Profile data
(name, email, photo...) is read from the canonical Person API when a page needs it;
it is never persisted on the Principal or in EventOS tables.
"""
from dataclasses import dataclass, field

SESSION_KEY = '_eventos_principal'


@dataclass(frozen=True)
class Principal:
    subject: str                      # Keycloak `sub`
    person_id: int                    # canonical Skill.jobs PersonID
    issuer: str
    tenant_schema: str                # tenant the browser session was established for
    scopes: frozenset = field(default_factory=frozenset)
    acr: str = ''                     # authentication context class (e.g. MFA level)
    amr: tuple = ()
    sid: str = ''                     # Keycloak SSO session id
    auth_time: int = 0
    source: str = 'keycloak'          # 'keycloak' (browser) | 'bearer' (API)

    is_authenticated = True

    def has_scope(self, scope):
        return scope in self.scopes

    # ── session (de)serialisation: identifiers only ──────────────────────
    def to_session(self):
        return {
            'sub': self.subject, 'person_id': self.person_id, 'iss': self.issuer,
            'tenant': self.tenant_schema, 'scopes': sorted(self.scopes), 'acr': self.acr,
            'amr': list(self.amr), 'sid': self.sid, 'auth_time': self.auth_time,
        }

    @classmethod
    def from_session(cls, data):
        return cls(
            subject=data['sub'], person_id=int(data['person_id']), issuer=data['iss'],
            tenant_schema=data['tenant'], scopes=frozenset(data.get('scopes') or ()),
            acr=data.get('acr') or '', amr=tuple(data.get('amr') or ()), sid=data.get('sid') or '',
            auth_time=int(data.get('auth_time') or 0),
        )

    @classmethod
    def from_claims(cls, claims, *, person_id, tenant_schema, source='keycloak'):
        return cls(
            subject=str(claims['sub']), person_id=int(person_id), issuer=str(claims['iss']),
            tenant_schema=tenant_schema, scopes=frozenset(str(claims.get('scope') or '').split()),
            acr=str(claims.get('acr') or ''), amr=tuple(claims.get('amr') or ()),
            sid=str(claims.get('sid') or ''), auth_time=int(claims.get('auth_time') or 0), source=source,
        )


class PrincipalUser:
    """
    Thin, non-persistent `request.user` adapter so `login_required`, templates and DRF
    permission checks keep working for Keycloak principals. It has no DB row, no
    password, no profile fields, and no Django admin/staff rights.
    """

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False
    pk = id = None

    def __init__(self, principal):
        self.principal = principal

    @property
    def person_id(self):
        return self.principal.person_id

    @property
    def username(self):
        return f'person:{self.principal.person_id}'

    def get_username(self):
        return self.username

    def __str__(self):
        return self.username

    def has_perm(self, perm, obj=None):
        return False

    def has_perms(self, perm_list, obj=None):
        return False

    def has_module_perms(self, app_label):
        return False

    def get_all_permissions(self, obj=None):
        return set()

    def save(self, *args, **kwargs):
        raise TypeError('PrincipalUser is not persistent')

    def delete(self, *args, **kwargs):
        raise TypeError('PrincipalUser is not persistent')


def get_principal(request):
    """The request's Keycloak principal, or None (anonymous or legacy-password user)."""
    return getattr(request, 'principal', None)


def current_person_id(request):
    principal = get_principal(request)
    return principal.person_id if principal else None
