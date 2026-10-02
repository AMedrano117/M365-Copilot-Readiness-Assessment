"""Application tokens for workload PowerShell sessions (Exchange and Purview).

The ExchangeOnlineManagement module (3.8.0 or later) accepts an OAuth access
token for both Connect-ExchangeOnline and Connect-IPPSSession. Acquiring the
token with the assessment application's existing credential lets a client
secret drive app-only Purview collection without a certificate. The session
still needs Exchange.ManageAsApp consent and a workload role (for example the
Security Reader or Global Reader directory role) to read configuration.

Tokens are passed to PowerShell over stdin only, never on the command line,
and are registered for redaction from diagnostics.
"""

import base64
import json

EXCHANGE_SCOPE = "https://outlook.office365.com/.default"
COMPLIANCE_SCOPE = "https://ps.compliance.protection.outlook.com/.default"
MANAGE_AS_APP = "Exchange.ManageAsApp"


def token_claims(token):
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part.encode()))
    except Exception:
        return {}


def token_roles(token):
    return set(token_claims(token).get("roles", []) or [])


def token_directory_roles(token):
    """Directory role template IDs carried in an app-only token (wids claim)."""
    return set(token_claims(token).get("wids", []) or [])


async def acquire_workload_token(scope, credential=None):
    """Return (token, roles, error). Never raises."""
    from .get_graph_client import get_access_token
    from .orchestrator_powershell import remember_runtime_secret
    try:
        token = await get_access_token(scope, credential)
    except Exception as exc:  # credential or network failure
        return "", set(), f"{type(exc).__name__}: {str(exc)[:200]}"
    remember_runtime_secret(token)
    return token, token_roles(token), ""


async def purview_token_secrets(credential=None):
    """Acquire Exchange and compliance tokens that carry Exchange.ManageAsApp.

    Returns ``(secrets, details)``. ``secrets`` contains only tokens whose
    ``roles`` claim includes Exchange.ManageAsApp; ``details`` explains what
    was found so preflight and the collector can report the unlock step.
    """
    secrets = {}
    details = {"exchange_roles": set(), "compliance_roles": set(), "directory_roles": set(), "reason": ""}
    exchange, exchange_roles, exchange_error = await acquire_workload_token(EXCHANGE_SCOPE, credential)
    compliance, compliance_roles, compliance_error = await acquire_workload_token(COMPLIANCE_SCOPE, credential)
    details["exchange_roles"] = exchange_roles
    details["compliance_roles"] = compliance_roles
    details["directory_roles"] = token_directory_roles(exchange or compliance)
    if MANAGE_AS_APP in exchange_roles:
        secrets["exchange_access_token"] = exchange
    if MANAGE_AS_APP in compliance_roles:
        secrets["compliance_access_token"] = compliance
    missing = []
    if MANAGE_AS_APP not in exchange_roles:
        missing.append("Office 365 Exchange Online")
    if MANAGE_AS_APP not in compliance_roles:
        missing.append("Microsoft Exchange Online Protection")
    if exchange_error and compliance_error:
        details["reason"] = f"Workload tokens could not be acquired: {exchange_error}"
    elif missing:
        details["reason"] = ("Exchange.ManageAsApp is not consented on " + " and ".join(missing)
                             + ". Rerun setup-service-principal.ps1 and grant admin consent.")
    return secrets, details
