"""Small Microsoft Graph REST client shared by all Python collectors.

The project intentionally does not use the generated ``msgraph-sdk`` package. Apart
from being much larger than the handful of endpoints used here, generated Graph paths
can exceed the default Windows path limit during installation. This client keeps the
small ``client.organization.get()`` surface used by older code and also provides
explicit JSON, CSV, streaming, pagination, and source-status helpers.
"""

from __future__ import annotations
from . import console_reporting as console

import asyncio
import csv
import io
import logging
import os
import re

import httpx
from azure.identity import CertificateCredential, ClientSecretCredential


logging.getLogger("azure.identity").setLevel(logging.ERROR)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE_URL = "https://graph.microsoft.com"


def _load_env(env_path=None):
    """Load the selected environment file without adding another dependency."""
    if env_path is None:
        env_path = os.getenv("ASSESSMENT_ENV_FILE") or os.path.join(
            os.path.dirname(__file__), "..", ".env"
        )
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8-sig") as env_file:
            for line in env_file:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()


def _ensure_env_loaded():
    has_secret = bool(os.getenv("CLIENT_SECRET"))
    has_certificate = bool(os.getenv("CERTIFICATE_PATH"))
    if not (
        os.getenv("TENANT_ID")
        and os.getenv("CLIENT_ID")
        and (has_secret or has_certificate)
    ):
        _load_env()


CREDENTIAL_HELP = (
    "Missing required environment variables. Ensure the .env file contains TENANT_ID, "
    "CLIENT_ID and ONE of:\n"
    "  CERTIFICATE_PATH=<path to .pem or .pfx>   (optionally CERTIFICATE_PASSWORD)\n"
    "  CLIENT_SECRET=<your-client-secret>\n"
    "Run setup-service-principal.ps1 to create or reconcile these credentials."
)


def _build_credential(tenant_id=None):
    """Create a certificate credential when possible, otherwise use the client secret."""
    _ensure_env_loaded()
    tenant_id = tenant_id or os.getenv("TENANT_ID")
    client_id = os.getenv("CLIENT_ID")
    certificate_path = os.getenv("CERTIFICATE_PATH")
    client_secret = os.getenv("CLIENT_SECRET")
    if not (tenant_id and client_id):
        raise ValueError(CREDENTIAL_HELP)

    if certificate_path:
        certificate_path = os.path.expanduser(os.path.expandvars(certificate_path))
        if not os.path.exists(certificate_path):
            raise ValueError(f"CERTIFICATE_PATH does not exist: {certificate_path}")
        password = os.getenv("CERTIFICATE_PASSWORD") or None
        return (
            CertificateCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                certificate_path=certificate_path,
                password=password.encode() if isinstance(password, str) else password,
            ),
            "certificate",
        )

    if client_secret:
        return (
            ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
            ),
            "client secret",
        )
    raise ValueError(CREDENTIAL_HELP)


def _snake_to_camel(name):
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class AttrDict(dict):
    """Graph JSON object that remains a dict and supports SDK-style attributes."""

    def __getattr__(self, name):
        if name in self:
            return self[name]
        camel = _snake_to_camel(name)
        if camel in self:
            return self[camel]
        raise AttributeError(name)


def _model(value):
    if isinstance(value, dict):
        return AttrDict({key: _model(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_model(item) for item in value]
    return value


class GraphRequestError(RuntimeError):
    """HTTP failure with fields used by connection and license classification."""

    def __init__(self, status_code, message, response=None):
        super().__init__(message or f"Microsoft Graph returned HTTP {status_code}")
        self.status_code = status_code
        self.message = message
        self.response = response


class _Endpoint:
    """Compatibility adapter for ``client.x.y.get()`` calls that remain."""

    def __init__(self, client, segments):
        self._client = client
        self._segments = list(segments)

    def __getattr__(self, name):
        return _Endpoint(self._client, self._segments + [_snake_to_camel(name)])

    async def get(self, request_configuration=None):
        path = "/v1.0/" + "/".join(self._segments)
        payload = await self._client.get_json(path)
        return _model(payload)


class GraphRestClient:
    """Authenticated async REST client with throttling retry and pagination."""

    def __init__(self, credential, timeout=60.0, max_retries=4):
        self.credential = credential
        self.max_retries = max_retries
        self._http = httpx.AsyncClient(
            base_url=GRAPH_BASE_URL,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    def __getattr__(self, name):
        return _Endpoint(self, [_snake_to_camel(name)])

    async def _authorization_header(self):
        token = await asyncio.to_thread(self.credential.get_token, GRAPH_SCOPE)
        return {"Authorization": f"Bearer {token.token}"}

    async def request(self, method, path, *, params=None, headers=None, content=None, json=None):
        request_headers = await self._authorization_header()
        request_headers.update(headers or {})
        last_response = None
        for attempt in range(self.max_retries + 1):
            last_response = await self._http.request(
                method,
                path,
                params=params,
                headers=request_headers,
                content=content,
                json=json,
            )
            if last_response.status_code not in {429, 500, 502, 503, 504}:
                break
            if attempt >= self.max_retries:
                break
            try:
                delay = min(float(last_response.headers.get("Retry-After", "")), 30.0)
            except (TypeError, ValueError):
                delay = min(2 ** attempt, 16)
            await asyncio.sleep(max(delay, 0.1))

        if last_response is None:
            raise GraphRequestError(0, "Microsoft Graph returned no response")
        if last_response.is_error:
            message = ""
            try:
                message = last_response.json().get("error", {}).get("message", "")
            except Exception:
                message = last_response.text[:500]
            raise GraphRequestError(last_response.status_code, message, last_response)
        return last_response

    async def get_json(self, path, *, params=None, headers=None):
        response = await self.request("GET", path, params=params, headers=headers)
        return response.json() if response.content else {}

    async def get_csv(self, path, *, params=None, headers=None):
        csv_headers = {"Accept": "text/csv"}
        csv_headers.update(headers or {})
        response = await self.request("GET", path, params=params, headers=csv_headers)
        text = response.content.decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(text)))

    async def get_collection(self, path, *, params=None, headers=None, max_pages=1000):
        """Read all pages and return records plus structured evidence status."""
        items = []
        pages = 0
        next_path = path
        next_params = params
        try:
            while next_path and pages < max_pages:
                payload = await self.get_json(next_path, params=next_params, headers=headers)
                pages += 1
                page_items = payload.get("value", []) if isinstance(payload, dict) else []
                if isinstance(page_items, list):
                    items.extend(page_items)
                next_path = payload.get("@odata.nextLink") if isinstance(payload, dict) else None
                next_params = None
            truncated = bool(next_path)
            return {
                "available": True,
                "availability_status": "partial" if truncated else "available",
                "value": items,
                "records_collected": len(items),
                "pages_collected": pages,
                "truncated": truncated,
                "reason": "Pagination safety limit reached" if truncated else "",
            }
        except GraphRequestError as exc:
            return {
                "available": bool(items),
                "availability_status": "partial" if items else "unavailable",
                "value": items,
                "records_collected": len(items),
                "pages_collected": pages,
                "truncated": bool(items),
                "status_code": exc.status_code,
                "reason": str(exc),
                "error": str(exc),
            }

    async def stream_to_file(self, path, destination, *, params=None, headers=None):
        response = await self.request("GET", path, params=params, headers=headers)
        with open(destination, "wb") as target:
            async for chunk in response.aiter_bytes():
                target.write(chunk)
        return destination

    async def aclose(self):
        await self._http.aclose()


_graph_client = None
_credential = None


async def get_graph_client(tenant_id=None, silent=False):
    """Return the shared lightweight Graph REST client."""
    global _graph_client, _credential
    if _graph_client:
        return _graph_client
    _ensure_env_loaded()
    from .spinner import get_timestamp

    if _credential is None:
        _credential, auth_method = _build_credential(tenant_id)
    else:
        auth_method = "certificate" if os.getenv("CERTIFICATE_PATH") else "client secret"
    if not silent:
        console.detail(f"[{get_timestamp()}] ℹ️     Authenticating with service principal ({auth_method})...")
    _graph_client = GraphRestClient(_credential)
    await _graph_client._authorization_header()
    if not silent:
        console.detail(f"[{get_timestamp()}] ✅ Authenticated successfully")
    return _graph_client


def get_shared_credential():
    """Return the shared credential for non-Graph resources."""
    global _credential
    if _credential is None:
        _credential, _ = _build_credential()
    return _credential


def get_power_platform_credential():
    return get_shared_credential()


async def get_api_client(service_name):
    """Create an authenticated HTTP client for a supported non-Graph resource."""
    service_config = {
        "defender": {
            "scope": "https://api.securitycenter.microsoft.com/.default",
            "base_url": "https://api.security.microsoft.com",
        },
        "power_platform": {
            "scope": "https://api.powerplatform.com/.default",
            "base_url": "https://api.powerplatform.com",
        },
    }
    if service_name not in service_config:
        raise ValueError(f"Unknown service: {service_name}")
    config = service_config[service_name]
    token = await asyncio.to_thread(get_shared_credential().get_token, config["scope"])
    return httpx.AsyncClient(
        base_url=config["base_url"],
        headers={
            "Authorization": f"Bearer {token.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=60.0,
        follow_redirects=True,
    )
