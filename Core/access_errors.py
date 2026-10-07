"""Consistent classification of access failures for collectors and info modules.

The Graph REST client raises ``GraphRequestError``; the Azure SDK raises
``HttpResponseError``. Both must be handled, otherwise a single 403 escapes an
info module and fails the whole service pipeline instead of being recorded as
an evidence gap.
"""

from azure.core.exceptions import HttpResponseError

from .get_graph_client import GraphRequestError


ACCESS_ERRORS = (HttpResponseError, GraphRequestError)

PERMISSION_DENIED = "permission_denied"
SIGN_IN_REJECTED = "sign_in_rejected"
NOT_PROVISIONED = "not_provisioned"
THROTTLED = "throttled"
TRANSIENT = "transient"
COLLECTION_ERROR = "collection_error"


def status_code_of(exc):
    """Return the HTTP status carried by either exception type, or 0."""
    code = getattr(exc, "status_code", None)
    if code is None:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
    try:
        return int(code or 0)
    except (TypeError, ValueError):
        return 0


def category_for_status(status_code):
    if status_code == 401:
        return SIGN_IN_REJECTED
    if status_code == 403:
        return PERMISSION_DENIED
    if status_code == 404:
        return NOT_PROVISIONED
    if status_code == 429:
        return THROTTLED
    if 500 <= status_code < 600:
        return TRANSIENT
    return COLLECTION_ERROR


def describe_access_failure(area, exc, *, delegated=False):
    """Return ``(category, operator message)`` for an access failure.

    Application (app-only) calls are authorized by application permissions and
    workload RBAC, not by an administrator role of the operator, so the
    message names the right fix for the identity that made the call.
    """
    status_code = status_code_of(exc)
    category = category_for_status(status_code)
    if category == PERMISSION_DENIED:
        if delegated:
            fix = "the signed-in account lacks the administrator role this read requires"
        else:
            fix = ("the application lacks a required application permission or workload role, "
                   "or the feature is not licensed; run --check-connections for the exact cause")
        message = f"{area}: access denied (HTTP 403) - {fix}."
    elif category == SIGN_IN_REJECTED:
        message = f"{area}: authentication was rejected (HTTP 401). Check the credential and tenant."
    elif category == NOT_PROVISIONED:
        message = f"{area}: the endpoint or feature is not available in this tenant (HTTP 404)."
    elif category == THROTTLED:
        message = f"{area}: Microsoft throttled the request (HTTP 429); retry later."
    elif category == TRANSIENT:
        message = f"{area}: the service returned HTTP {status_code}; retry later."
    else:
        message = f"{area}: request failed" + (f" (HTTP {status_code})" if status_code else "") + "."
    return category, message
