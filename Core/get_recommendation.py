"""
Module for retrieving feature-specific recommendations
Uses modular recommendation files organized by service
"""

from .collector_registry import source_allowed


def recommendation_graph_probes_allowed(permission_profile="standard"):
    """Legacy feature probes combine site/group reads and are not source-aware."""
    return all(source_allowed(source, permission_profile) for source in ('sites', 'groups', 'oauth_grants'))


def recommendation_graph_gap(service, permission_profile="standard"):
    if recommendation_graph_probes_allowed(permission_profile):
        return None
    from .new_recommendation import new_recommendation
    return new_recommendation(
        service=service, feature=f"{service} deployment verification",
        observation="Legacy Graph deployment probes are not assessed: site and group inventory probes were not requested by the Restricted permission profile.",
        recommendation="Use the retained collector evidence and customer-provided inventory exports for the checks they support. Verify remaining deployment questions with the workload owner.",
        status="Not Assessed", disposition="Coverage",
        finding_key=service.lower().replace(' ', '_') + '.graph_deployment.not_assessed',
    )


def get_recommendation(*args, **kwargs):
    """Retain legacy Graph query envelopes on async recommendation records.

    Context-local capture also isolates concurrently evaluated feature modules.
    Sync recommendation modules and restricted-profile transport gating retain
    their existing behavior.
    """
    import inspect
    result = _get_recommendation(*args, **kwargs)
    if not inspect.isawaitable(result):
        return result

    async def collect():
        from .get_graph_client import LEGACY_COLLECTION_EVIDENCE
        from .source_evidence import envelope_complete
        from .evidence_contract import stable_id
        datasets = {}
        token = LEGACY_COLLECTION_EVIDENCE.set(datasets)
        try:
            rows = await result
        finally:
            LEGACY_COLLECTION_EVIDENCE.reset(token)
        if datasets:
            states = [entry['source'] for entries in datasets.values() for entry in entries]
            complete = all(envelope_complete(state) for state in states)
            failures = [state for state in states if state['availability_status'] not in {'available', 'partial'}]
            source_name = 'legacy_graph.probe.' + stable_id(sorted(datasets))
            # An aggregate supports only the returned sources, never a claim of
            # tenant-wide deployment. Original per-query envelopes are retained.
            aggregate = {'availability_status': failures[0]['availability_status'] if failures else 'available' if complete else 'partial',
                         'available': not failures, 'complete': complete,
                         'scope': '; '.join(sorted({state['scope'] for state in states})),
                         'source_api': '; '.join(sorted({state['source_api'] for state in states})),
                         'collected_at': min(state['collected_at'] for state in states)}
            for row in rows if isinstance(rows, list) else [rows]:
                if isinstance(row, dict):
                    row['assessment_datasets'] = datasets
                    row.setdefault('EvidenceSource', source_name)
                    row['collection_status'] = {source_name: aggregate}
                    row.setdefault('EvidenceScope', aggregate['scope'])
                    row.setdefault('EvidenceComplete', complete)
        return rows

    return collect()


def _get_recommendation(recommendation_type, feature_name, sku_name, status="Success", client=None, pp_client=None, pp_insights=None, purview_client=None, defender_client=None, defender_insights=None, entra_insights=None, m365_insights=None, permission_profile="standard"):
    """
    Get a feature-specific recommendation based on type
    
    Each service category (entra, defender, purview, etc.) has a dedicated folder
    containing individual recommendation files for each feature. This enables
    precise, feature-specific observations and recommendations focused on
    M365 Copilot and agent adoption.
    
    Args:
        recommendation_type: Type of recommendation to create (entra, defender, purview, etc.)
        feature_name: Technical service plan name (e.g., 'AAD_PREMIUM', 'MTP')
        sku_name: Name of the SKU containing the feature
        status: Provisioning status of the feature
        client: Optional Graph client for deployment checks
        pp_client: Optional Power Platform client for deployment checks
        pp_insights: Pre-computed Power Platform insights (performance optimization)
        purview_client: Optional Purview client for deployment checks
        defender_client: Optional Defender API client for security metrics enrichment
        defender_insights: Pre-computed Defender insights (performance optimization)
        entra_insights: Pre-computed Entra insights (performance optimization)
        m365_insights: Pre-computed M365 usage insights (performance optimization)
    
    Returns:
        dict: Feature-specific recommendation object with:
            - Service: Service category
            - Feature: Friendly feature name
            - Status: Provisioning status
            - Priority: Priority level (empty for successful observations)
            - Observation: Specific observation about the feature
            - Recommendation: Specific recommendation for adoption
            - LinkText: Feature-specific link text
            - LinkUrl: Feature-specific documentation URL
    """
    # Feature modules predate source-aware collectors and some mix site/group reads
    # with other deployment probes. Withhold the transport before constructing their
    # coroutines; cached insights and customer-export clients remain available.
    if not recommendation_graph_probes_allowed(permission_profile):
        client = None

    # Lazy load recommendation modules based on type
    if recommendation_type.lower() == "entra":
        from Recommendations.entra import get_feature_recommendation as get_entra_recommendation
        recommendation_func = get_entra_recommendation
    elif recommendation_type.lower() == "defender":
        from Recommendations.defender import get_feature_recommendation as get_defender_recommendation
        recommendation_func = get_defender_recommendation
    elif recommendation_type.lower() == "purview":
        from Recommendations.purview import get_feature_recommendation as get_purview_recommendation
        recommendation_func = get_purview_recommendation
    elif recommendation_type.lower() == "power_platform":
        from Recommendations.power_platform import get_feature_recommendation as get_power_platform_recommendation
        recommendation_func = get_power_platform_recommendation
    elif recommendation_type.lower() == "copilot_studio":
        from Recommendations.copilot_studio import get_feature_recommendation as get_copilot_studio_recommendation
        recommendation_func = get_copilot_studio_recommendation
    elif recommendation_type.lower() == "m365":
        from Recommendations.m365 import get_feature_recommendation as get_m365_recommendation
        recommendation_func = get_m365_recommendation
    else:
        raise ValueError(f"Unknown recommendation type: {recommendation_type}")
    
    # Route to appropriate service-specific function with relevant clients
    if recommendation_type.lower() in ['power_platform', 'copilot_studio']:
        return recommendation_func(feature_name, sku_name, status, client=client, pp_client=pp_client, pp_insights=pp_insights)
    elif recommendation_type.lower() == 'purview':
        return recommendation_func(feature_name, sku_name, status, client=client, purview_client=purview_client)
    elif recommendation_type.lower() == 'defender':
        return recommendation_func(feature_name, sku_name, status, client=client, defender_client=defender_client, defender_insights=defender_insights)
    elif recommendation_type.lower() == 'entra':
        return recommendation_func(feature_name, sku_name, status, client=client, entra_insights=entra_insights)
    elif recommendation_type.lower() == 'm365':
        return recommendation_func(feature_name, sku_name, status, client=client, m365_insights=m365_insights)
    else:
        return recommendation_func(feature_name, sku_name, status, client=client)
