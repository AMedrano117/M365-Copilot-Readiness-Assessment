"""Optional delegated (signed-in administrator) access for enrichment only.

Application permissions remain the baseline for every dataset. A delegated
sign-in is used only for data Microsoft exposes exclusively to signed-in
administrators, such as the Copilot limited mode setting. It never replaces
evidence an application permission can read.

The assessment application is used as a public client with a loopback
redirect (authorization code with PKCE); setup registers ``http://localhost``
and the delegated scopes in the Standard profile. Microsoft Entra ignores the
port of loopback redirects, so the credential listens on the first free
localhost port it finds. The token cache is
persisted per tenant with the operating system's protection (DPAPI on
Windows) and an authentication record stores only the account identifiers.
"""

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from . import console_reporting as console

GRAPH_RESOURCE = "https://graph.microsoft.com/"
DELEGATED_SCOPES = ("CopilotSettings-LimitedMode.Read",)
# Registered by setup. azure-identity requires an explicit port when a
# redirect URI is passed, so the credential is built without one and picks a
# free localhost port; Entra matches it against this registration.
REDIRECT_URI = "http://localhost"
CACHE_DIRECTORY = Path(__file__).resolve().parent.parent / ".cache" / "delegated"

OFF = "off"
CACHED = "cached"
CAN_PROMPT = "can_prompt"
UNAVAILABLE = "unavailable"
SIGNED_IN = "signed_in"


def graph_scopes(names=DELEGATED_SCOPES):
    return [GRAPH_RESOURCE + name for name in names]


def _tenant_key(tenant_id, client_id):
    return hashlib.sha256(f"{str(tenant_id).lower()}|{str(client_id).lower()}".encode("utf-8")).hexdigest()[:16]


def _record_path(tenant_id, client_id):
    return CACHE_DIRECTORY / f"{_tenant_key(tenant_id, client_id)}.json"


def _claims(token):
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part.encode()))
    except Exception:
        return {}


def _failure_summary(exc):
    """First line of a sign-in error, without tokens or trailing diagnostics."""
    import re
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else "no details"
    text = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]*", "<token>", text)
    return text[:200]


def can_prompt(interactive_auth="auto"):
    return interactive_auth != "skip" and sys.stdin.isatty() and sys.stdout.isatty()


def delegated_state(tenant_id, client_id, mode="auto", interactive_auth="auto", permission_profile="standard"):
    """Classify delegated availability without contacting Microsoft Entra."""
    if mode == "off" or permission_profile == "restricted" or not (tenant_id and client_id):
        return OFF
    if _record_path(tenant_id, client_id).exists():
        return CACHED
    if can_prompt(interactive_auth) or mode == "required":
        return CAN_PROMPT
    return UNAVAILABLE


class DelegatedSession:
    """A signed-in administrator session used only for delegated-only datasets."""

    def __init__(self, credential, identity, scopes, reused=False):
        self.credential = credential
        self.identity = identity
        self.scopes = list(scopes)
        # True when a saved sign-in was used without a new browser prompt.
        self.reused = reused

    def graph_client(self):
        from .get_graph_client import GraphRestClient
        return GraphRestClient(self.credential, scopes=self.scopes)


def _build_credential(tenant_id, client_id, record=None, allow_prompt=True):
    from azure.identity import InteractiveBrowserCredential, TokenCachePersistenceOptions
    options = TokenCachePersistenceOptions(name=f"m365-readiness-{_tenant_key(tenant_id, client_id)}")
    return InteractiveBrowserCredential(
        tenant_id=tenant_id, client_id=client_id,
        cache_persistence_options=options, authentication_record=record,
        disable_automatic_authentication=not allow_prompt,
    )


def _load_record(tenant_id, client_id):
    path = _record_path(tenant_id, client_id)
    if not path.exists():
        return None
    try:
        from azure.identity import AuthenticationRecord
        return AuthenticationRecord.deserialize(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_record(tenant_id, client_id, record):
    try:
        CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        _record_path(tenant_id, client_id).write_text(record.serialize(), encoding="utf-8")
    except OSError:
        pass


async def acquire_delegated_session(tenant_id, client_id, *, mode="auto", interactive_auth="auto",
                                    permission_profile="standard", scopes=None):
    """Return ``(session or None, reason)``. Never raises for sign-in failures."""
    import asyncio
    state = delegated_state(tenant_id, client_id, mode, interactive_auth, permission_profile)
    if state == OFF:
        return None, "Delegated sign-in is off (--delegated off or the Restricted profile)."
    if state == UNAVAILABLE:
        return None, "No cached delegated sign-in and no interactive terminal; delegated-only data is not collected."
    scopes = scopes or graph_scopes()
    record = _load_record(tenant_id, client_id)
    reused = record is not None
    allow_prompt = can_prompt(interactive_auth) or mode == "required"
    try:
        credential = _build_credential(tenant_id, client_id, record=record, allow_prompt=allow_prompt)
        if record is None:
            console.status("Delegated sign-in: complete the browser prompt to add data that only signed-in administrators can read "
                           "(Copilot limited mode). Application-permission collection does not depend on it.")
            record = await asyncio.to_thread(credential.authenticate, scopes=scopes)
            _save_record(tenant_id, client_id, record)
        token = await asyncio.to_thread(credential.get_token, *scopes)
    except Exception as exc:
        name = type(exc).__name__
        if name == "AuthenticationRequiredError":
            return None, "The cached delegated sign-in expired and interactive sign-in is not available."
        return None, f"Delegated sign-in did not complete ({name}: {_failure_summary(exc)})."
    claims = _claims(token.token)
    if str(claims.get("tid", "")).lower() != str(tenant_id).lower():
        return None, "The delegated sign-in belongs to a different tenant; it was not used."
    identity = {
        "upn": claims.get("upn") or claims.get("preferred_username") or "",
        "object_id": claims.get("oid", ""),
        "roles": sorted(claims.get("wids", []) or []),
        "scopes": sorted(str(claims.get("scp", "")).split()),
    }
    return DelegatedSession(credential, identity, scopes, reused=reused), ""


def forget_delegated_session(tenant_id, client_id):
    """Remove the local authentication record for a tenant (cleanup helper)."""
    try:
        _record_path(tenant_id, client_id).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def environment_client():
    return os.environ.get("TENANT_ID", ""), os.environ.get("CLIENT_ID", "")
