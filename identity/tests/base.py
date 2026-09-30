# identity/tests/base.py
from companies.models import Domain
from identity.employer_client import get_employer_client
from identity.entitlement_client import get_entitlement_client
from identity.profile_client import get_profile_client
from tests.fixtures import TENANT_A_HOST, TenantTestCase

from .fake_keycloak import FakeKeycloak, authorize_params, keycloak_settings


class KeycloakTestCase(TenantTestCase):
    """Tenant tests with Keycloak enabled (dual mode) against the in-process fake realm."""

    settings_overrides = {}

    def setUp(self):
        super().setUp()
        override = keycloak_settings(**self.settings_overrides)
        override.enable()
        self.addCleanup(override.disable)
        self.kc = FakeKeycloak()
        self.kc.start(self)
        self.approve(TENANT_A_HOST)

    # ── helpers ──────────────────────────────────────────────────────────
    def approve(self, host, **extra):
        Domain.objects.filter(domain=host).update(oidc_callback_approved=True, **extra)

    @property
    def profiles(self):
        return get_profile_client()

    @property
    def employers(self):
        return get_employer_client()

    @property
    def entitlements(self):
        return get_entitlement_client()

    def start_login(self, client, next_path=None, **params):
        query = {**params}
        if next_path:
            query['next'] = next_path
        response = client.get('/auth/login/', query)
        return authorize_params(response)

    def sso_login(self, client, *, id_claims=None, access_claims=None, next_path=None, bind_pkce=True):
        """Drive login → (fake) Keycloak → callback. Returns the callback response."""
        params, _ = self.start_login(client, next_path=next_path)
        code = self.kc.issue_code(nonce=params['nonce'], id_claims=id_claims, access_claims=access_claims)
        if bind_pkce:
            self.kc.codes[code]['challenge'] = params['code_challenge']
            self.kc.codes[code]['redirect_uri'] = params['redirect_uri']
        return client.get('/auth/callback/', {'state': params['state'], 'code': code})
