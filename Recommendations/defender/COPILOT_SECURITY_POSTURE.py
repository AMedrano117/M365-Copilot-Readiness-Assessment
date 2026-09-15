"""Compatibility hook for the removed synthetic Copilot posture card.

The former card combined unavailable hunting, vulnerability, email, OAuth, device,
incident, and Purview values into a single READY/NOT READY label. Direct controls now
evaluate each supported evidence source independently, so no aggregate recommendation
is emitted here.
"""


def get_recommendation(defender_client=None, purview_client=None, defender_insights=None):
    return None
