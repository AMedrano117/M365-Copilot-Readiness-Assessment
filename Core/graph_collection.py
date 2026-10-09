"""Validate Graph collection pages before they can establish complete-empty evidence."""
from urllib.parse import urlsplit


def collection_page(payload):
    """Return records, continuation and partial state; reject unusable envelopes.

    Graph collection responses use an object containing a value array. Validate
    the entire page before retaining it; callers retain earlier valid pages.
    A partial marker is conservative imported-source metadata, not an inferred
    Graph completeness guarantee. Never follow a continuation to another host.
    """
    if not isinstance(payload, dict) or 'error' in payload:
        raise ValueError('Malformed Graph collection: expected an object without an error envelope')
    if not isinstance(payload.get('value'), list) or any(not isinstance(row, dict) for row in payload['value']):
        raise ValueError('Malformed Graph collection: expected a value array of records')
    continuation = payload.get('@odata.nextLink')
    if '@odata.nextLink' in payload:
        if not isinstance(continuation, str) or not continuation.strip():
            raise ValueError('Malformed Graph collection: continuation must be a nonempty URL')
        url = urlsplit(continuation)
        # Saved collections and existing callers also use root-relative links.
        # They resolve against the fixed Graph base; protocol-relative or foreign
        # origins must never receive the Graph authorization header.
        relative = continuation.startswith('/') and not continuation.startswith('//') and not url.scheme and not url.netloc
        absolute = url.scheme == 'https' and url.hostname == 'graph.microsoft.com' and url.port in (None, 443)
        if (not (relative or absolute) or url.username or url.password or url.fragment
                or '\\' in continuation or any(char.isspace() for char in continuation)):
            raise ValueError('Malformed Graph collection: continuation must reference Microsoft Graph HTTPS')
    partial = (payload.get('complete') is False or payload.get('truncated') is True
               or payload.get('availability_status') == 'partial' or payload.get('@odata.partial') is True)
    return payload['value'], continuation, partial
