"""Confirm the tenant a live run is about to collect from.

Consultants run this tool against many customers. Every live run therefore
checks that the application token belongs to the configured tenant and, when
an expected domain is known, that the tenant's domains include it. A mismatch
stops the run before any evidence is collected or saved.
"""

import base64
import json
import os
import sys
from uuid import UUID

from . import console_reporting as console


class TenantMismatchError(ValueError):
    """The authenticated tenant is not the tenant selected for this run."""


def _normalize_guid(value):
    try:
        return str(UUID(str(value))).lower()
    except (ValueError, TypeError, AttributeError):
        return ""


def _token_claims(token):
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part.encode()))
    except Exception:
        return {}


async def read_organization(client):
    """Read /organization once per client; returns {} when unavailable."""
    # GraphRestClient resolves unknown attributes to Graph endpoints, so only a
    # dict stored by this function counts as a cached organization.
    cached = vars(client).get("_assessment_organization") if hasattr(client, "__dict__") else None
    if isinstance(cached, dict):
        return cached
    organization = {}
    try:
        payload = await client.get_json(
            "/v1.0/organization", params={"$select": "id,displayName,verifiedDomains"}
        )
        values = payload.get("value", []) if isinstance(payload, dict) else []
        if values and isinstance(values[0], dict):
            organization = values[0]
    except Exception:
        organization = {}
    try:
        client._assessment_organization = organization
    except Exception:
        pass
    return organization


def initial_domain(organization):
    domains = organization.get("verifiedDomains") or []
    initial = next((d.get("name", "") for d in domains if isinstance(d, dict) and d.get("isInitial")), "")
    if initial:
        return initial
    return next((d.get("name", "") for d in domains
                 if isinstance(d, dict) and str(d.get("name", "")).lower().endswith(".onmicrosoft.com")), "")


def verified_domain_names(organization):
    return {str(d.get("name", "")).lower() for d in (organization.get("verifiedDomains") or [])
            if isinstance(d, dict) and d.get("name")}


def expected_tenant_domain(explicit=None):
    """Return (expected domain, source) from the CLI or the selected environment."""
    for value, source in (
        (explicit, "--confirm-tenant"),
        (os.environ.get("EXPECTED_TENANT_DOMAIN"), "EXPECTED_TENANT_DOMAIN"),
        (os.environ.get("PURVIEW_ORGANIZATION"), "PURVIEW_ORGANIZATION"),
    ):
        if value and str(value).strip():
            return str(value).strip().lower(), source
    return "", ""


async def _token_tenant(client):
    from .get_graph_client import GRAPH_SCOPE, get_access_token
    try:
        token = await get_access_token(GRAPH_SCOPE, getattr(client, "credential", None))
    except Exception:
        return ""
    return _normalize_guid(_token_claims(token).get("tid"))


async def confirm_tenant_identity(client, configured_tenant, *, expected_domain=None,
                                  interactive=None, prompt=None):
    """Verify and announce the target tenant; raise TenantMismatchError on mismatch.

    Returns a dict with ``tenant_id``, ``display_name``, ``initial_domain`` and
    ``confirmation`` (``expected_domain``, ``operator`` or ``unconfirmed``).
    """
    organization = await read_organization(client)
    tenant_guid = _normalize_guid(organization.get("id")) or _normalize_guid(configured_tenant)
    token_tenant = await _token_tenant(client)
    configured_guid = _normalize_guid(configured_tenant)

    if token_tenant and configured_guid and token_tenant != configured_guid:
        raise TenantMismatchError(
            f"The application token was issued by tenant {token_tenant}, not the configured tenant "
            f"{configured_guid}. Check TENANT_ID and the selected --env-file before collecting."
        )
    if token_tenant and tenant_guid and token_tenant != tenant_guid:
        raise TenantMismatchError(
            "The organization returned by Microsoft Graph does not match the token tenant. Collection stopped."
        )

    name = organization.get("displayName") or ""
    domain = initial_domain(organization)
    identity = {
        "tenant_id": tenant_guid or token_tenant or str(configured_tenant or ""),
        "display_name": name,
        "initial_domain": domain,
        "confirmation": "unconfirmed",
    }
    label = ", ".join(value for value in (domain, identity["tenant_id"]) if value)
    console.status(f"Target tenant: {name or 'name unavailable'} ({label})")

    expected, source = expected_tenant_domain(expected_domain)
    if expected:
        known = verified_domain_names(organization)
        if not known:
            console.status(
                f"Tenant domains could not be read to compare with {source}; the token tenant was verified instead.",
                tone="warning",
            )
            identity["confirmation"] = "token_tenant"
            return identity
        if expected not in known:
            raise TenantMismatchError(
                f"{source} is {expected}, but the signed-in tenant is {name or identity['tenant_id']} "
                f"({domain or 'initial domain unavailable'}). Collection stopped before any evidence was read."
            )
        identity["confirmation"] = "expected_domain"
        console.detail(f"Tenant confirmed against {source}.")
        return identity

    interactive = sys.stdin.isatty() if interactive is None else interactive
    if interactive:
        ask = prompt or input
        try:
            answer = ask(f"Collect evidence from {name or identity['tenant_id']} ({domain or 'domain unavailable'})? [y/N]: ")
        except EOFError:
            answer = ""
        if str(answer).strip().lower() not in {"y", "yes"}:
            raise TenantMismatchError("Collection cancelled at tenant confirmation.")
        identity["confirmation"] = "operator"
        if domain:
            console.status(f"To skip this question, set EXPECTED_TENANT_DOMAIN={domain} in the selected environment file.")
        return identity

    console.status(
        "No expected tenant domain is configured. Set EXPECTED_TENANT_DOMAIN or pass --confirm-tenant "
        "so unattended runs verify the customer tenant.",
        tone="warning",
    )
    return identity
