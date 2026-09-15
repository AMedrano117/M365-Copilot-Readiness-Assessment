"""Legacy compatibility hook for a removed inference-based recommendation.

Ordinary endpoint process, network, file, and inbound-email telemetry cannot prove
Copilot or external-AI usage. External AI is assessed through Defender Cloud Apps
discovery, while real XDR alerts and incidents contribute to the general threat posture.
"""


def get_recommendation(defender_client=None, defender_insights=None):
    return None
