# identity/service_http.py
"""
Shared plumbing for calls to canonical Skill.jobs services.

Every outbound call can carry: credentials, tenant context, purpose and the
current correlation ID. EventOS never reads another application's database.
"""
from dataclasses import dataclass
from typing import Optional

import requests
from django.conf import settings
from django.utils.module_loading import import_string

from .exceptions import UpstreamUnavailable
from .observability import get_correlation_id


@dataclass(frozen=True)
class CallContext:
    purpose: str
    tenant_schema: Optional[str] = None
    canonical_tenant_id: Optional[int] = None


def build_headers(ctx, *, bearer_token=None):
    headers = {'Accept': 'application/json', 'X-Purpose': ctx.purpose}
    cid = get_correlation_id()
    if cid:
        headers['X-Correlation-ID'] = cid
    if ctx.tenant_schema:
        headers['X-EventOS-Tenant'] = ctx.tenant_schema
    if ctx.canonical_tenant_id is not None:
        headers['X-Skilljobs-Tenant-ID'] = str(ctx.canonical_tenant_id)
    token = bearer_token or getattr(settings, 'EVENTOS_SERVICE_TOKEN', '')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers


def backend_url(path):
    base = (getattr(settings, 'SKILLJOBS_BACKEND_URL', '') or '').rstrip('/')
    if not base:
        raise UpstreamUnavailable('SKILLJOBS_BACKEND_URL not configured', code='backend_not_configured')
    return f'{base}{path}'


def request_json(method, url, ctx, *, bearer_token=None, json=None):
    timeout = float(getattr(settings, 'SKILLJOBS_BACKEND_TIMEOUT', 5))
    try:
        resp = requests.request(method, url, headers=build_headers(ctx, bearer_token=bearer_token),
                                json=json, timeout=timeout)
    except requests.RequestException as exc:
        raise UpstreamUnavailable('canonical service unreachable', code='upstream_unreachable') from exc
    if resp.status_code >= 500:
        raise UpstreamUnavailable('canonical service error', code=f'upstream_{resp.status_code}')
    try:
        body = resp.json()
    except ValueError as exc:
        raise UpstreamUnavailable('canonical service returned non-json', code='upstream_bad_body') from exc
    if not isinstance(body, dict):
        raise UpstreamUnavailable('canonical service returned unexpected body', code='upstream_bad_body')
    return resp.status_code, body


_instances = {}


def load_client(setting_name, default_path):
    path = getattr(settings, setting_name, '') or default_path
    if path not in _instances:
        _instances[path] = import_string(path)()
    return _instances[path]


def reset_clients():
    """Drop cached client instances (tests / settings changes)."""
    _instances.clear()
