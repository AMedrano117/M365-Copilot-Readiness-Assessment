"""Shared, historical client-name classification for sign-in evidence."""

LEGACY_CLIENT_MARKERS = ('pop', 'imap', 'smtp', 'activesync', 'other clients', 'exchange web services')
LEGACY_CLASSIFICATION_RULE = (
    'Case-insensitive substring match of clientAppUsed against: '
    + ', '.join(LEGACY_CLIENT_MARKERS)
    + '. This classifies the reported client; it does not establish successful authentication or a control bypass.'
)


def is_legacy_signin(record):
    """Use exactly the collector's historical substring predicate.

    Accept raw Graph dictionaries or SDK model attributes. Missing client data
    does not match; a non-match alone is not evidence of modern authentication.
    Authentication outcome and Conditional Access status are independent fields.
    """
    value = None
    for name in ('clientAppUsed', 'client_app_used'):
        candidate = record.get(name) if isinstance(record, dict) else getattr(record, name, None)
        if candidate is not None:
            value = candidate
            break
    client_app = str(value or '').lower()
    return any(marker in client_app for marker in LEGACY_CLIENT_MARKERS)
