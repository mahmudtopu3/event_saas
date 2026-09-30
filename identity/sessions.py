# identity/sessions.py
"""
Server-side session state for Keycloak logins.

Sessions are DB-backed and live in the tenant's own schema, so a login
established on one tenant host is never presented to another. Tokens are kept
only server-side (never in URLs, cookies or logs) and only for refresh and logout.
"""
import time

from .principal import SESSION_KEY, Principal

PENDING_KEY = '_eventos_oidc_pending'
TOKENS_KEY = '_eventos_oidc_tokens'
MAX_PENDING = 5


def add_pending(session, state, data):
    pending = {k: v for k, v in (session.get(PENDING_KEY) or {}).items()
               if v.get('expires_at', 0) > time.time()}
    while len(pending) >= MAX_PENDING:
        pending.pop(min(pending, key=lambda k: pending[k]['expires_at']))
    pending[state] = data
    session[PENDING_KEY] = pending


def pop_pending(session, state):
    """Single use: a state value is consumed whether or not the callback succeeds."""
    pending = session.get(PENDING_KEY) or {}
    data = pending.pop(state, None) if state else None
    session[PENDING_KEY] = pending
    return data


def store_login(session, principal, tokens):
    session[SESSION_KEY] = principal.to_session()
    session[TOKENS_KEY] = {
        'id_token': tokens.get('id_token', ''),
        'access_token': tokens.get('access_token', ''),
        'refresh_token': tokens.get('refresh_token', ''),
        'validated_at': time.time(),
    }


def load_principal(session):
    data = session.get(SESSION_KEY)
    if not data:
        return None
    try:
        return Principal.from_session(data)
    except (KeyError, TypeError, ValueError):
        return None


def tokens(session):
    return session.get(TOKENS_KEY) or {}


def update_tokens(session, new_tokens):
    current = tokens(session)
    for key in ('id_token', 'access_token', 'refresh_token'):
        if new_tokens.get(key):
            current[key] = new_tokens[key]
    current['validated_at'] = time.time()
    session[TOKENS_KEY] = current
