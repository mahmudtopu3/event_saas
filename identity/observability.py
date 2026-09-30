# identity/observability.py
"""
Structured, secret-free audit logging and correlation IDs.

Every audit line is ``<event name> {json}`` on the ``eventos.audit`` logger.
Values under sensitive keys are dropped before anything is logged.
"""
import contextvars
import json
import logging
import re
import uuid

logger = logging.getLogger('eventos.audit')

_correlation_id = contextvars.ContextVar('eventos_correlation_id', default=None)

_SENSITIVE = re.compile(
    r'(token|secret|password|passwd|code_verifier|authorization|cookie|session_key|^code$|email|phone|cv)',
    re.IGNORECASE,
)
_CORRELATION_ID_RE = re.compile(r'^[A-Za-z0-9._-]{8,128}$')


def get_correlation_id():
    return _correlation_id.get()


def audit(event, **fields):
    record = {k: v for k, v in fields.items() if v is not None and not _SENSITIVE.search(k)}
    cid = get_correlation_id()
    if cid:
        record['correlation_id'] = cid
    logger.info('%s %s', event, json.dumps(record, default=str, sort_keys=True))


class CorrelationIdMiddleware:
    """Accept a well-formed inbound X-Correlation-ID / X-Request-ID or mint one; echo it back."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inbound = request.headers.get('X-Correlation-ID') or request.headers.get('X-Request-ID') or ''
        cid = inbound if _CORRELATION_ID_RE.match(inbound) else uuid.uuid4().hex
        token = _correlation_id.set(cid)
        request.correlation_id = cid
        try:
            response = self.get_response(request)
        finally:
            _correlation_id.reset(token)
        response['X-Correlation-ID'] = cid
        return response
