"""Validate operator-supplied SharePoint administration endpoints."""

import re
from urllib.parse import urlparse


SHAREPOINT_ADMIN_URL_REQUIRED = (
    'SharePoint admin URL is missing or invalid. Set SHAREPOINT_ADMIN_URL in the selected '
    'environment file or pass --sharepoint-admin-url with the actual SharePoint admin-center '
    'HTTPS origin (for example https://contoso-admin.sharepoint.com). The initial '
    'onmicrosoft.com domain does not verify this URL; SharePoint governance remains not assessed.'
)


def is_valid_sharepoint_admin_url(value):
    """Check commercial-cloud admin URL syntax, without claiming tenant verification."""
    try:
        parsed = urlparse(str(value or '').strip())
        hostname = parsed.hostname or ''
        return bool(
            parsed.scheme.lower() == 'https'
            and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?-admin\.sharepoint\.com', hostname, re.I)
            and len(hostname.split('.')[0]) <= 63
            and not parsed.path.strip('/')
            and not parsed.username and not parsed.password
            and parsed.port is None and not parsed.query and not parsed.fragment
        )
    except (TypeError, ValueError):
        return False
