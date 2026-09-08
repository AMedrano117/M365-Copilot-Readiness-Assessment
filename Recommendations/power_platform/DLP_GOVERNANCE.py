"""
DLP Policy Governance - Copilot Extensibility Impact Assessment
This is a pseudo-feature to assess DLP policy impact on Copilot plugin development
"""
from Core.new_recommendation import new_recommendation, NOT_ASSESSED_STATUS, CATEGORY_SCAN_COVERAGE

async def get_recommendation(sku_name, status="Success", client=None, pp_client=None, pp_insights=None):
    """
    Analyze DLP policies for Copilot extensibility blockers.
    This runs when any Power Platform license is active.
    """
    # DLP data not available in standard pp_insights - need pp_client for this
    if not pp_client or not hasattr(pp_client, 'dlp_summary'):
        return [new_recommendation(
            service="Power Platform",
            feature="DLP Governance - Assessment Needed",
            observation="Power Platform DLP policy evidence was not collected, so its effect on optional agent and connector extensibility is unverified.",
            recommendation="If Power Platform extensibility is in scope, have an authorized Power Platform administrator review Data policies in the admin center and document connector classifications for the proposed agent use cases. The tenant-wide inventory export or preview Power Platform Reader API can establish inventory, but it does not replace the administrative DLP review. Keep this as optional extensibility coverage; it does not reduce core Microsoft 365 security readiness.",
            link_text="DLP Policy Review",
            link_url="https://learn.microsoft.com/power-platform/admin/wp-data-loss-prevention",
            priority="Medium",
            status=NOT_ASSESSED_STATUS,
            category=CATEGORY_SCAN_COVERAGE
        )]
    
    dlp_summary = pp_client.dlp_summary
    
    # Fallback when API call failed
    if 'error' in dlp_summary:
        return [new_recommendation(
            service="Power Platform",
            feature="DLP Governance - Verification Failed",
            observation=f"Unable to verify DLP policies - {dlp_summary.get('error', 'Unknown error')}. Manual review recommended.",
            recommendation="Manually review DLP policies in Power Platform admin center (admin.powerplatform.microsoft.com): 1) Navigate to Policies > Data policies, 2) Check if HTTP connector is allowed (critical for Copilot plugins), 3) Review custom connector permissions, 4) Identify any tenant-wide policies that may block Copilot extensibility. Document current governance stance for agent development planning. DLP assessment is critical before investing in plugin development.",
            link_text="Power Platform Admin Center",
            link_url="https://admin.powerplatform.microsoft.com",
            priority="Medium",
            status="Success"
        )]
    
    total_policies = dlp_summary.get('total', 0)
    
    # No policies - ideal for extensibility
    if total_policies == 0:
        return [new_recommendation(
            service="Power Platform",
            feature="DLP Governance - Copilot Extensibility",
            observation="No DLP policies configured - maximum flexibility for Copilot plugin development",
            recommendation="Consider implementing DLP policies for governance while preserving Copilot extensibility: 1) Create environment-specific policies (not tenant-wide) to isolate plugin development, 2) Allow HTTP and Custom Connectors in dedicated 'Copilot Innovation' environment, 3) Restrict only high-risk connectors (social media, consumer storage), 4) Document governance exceptions for Copilot plugins. Start with permissive policies and tighten based on usage patterns. Balance innovation with data protection.",
            link_text="DLP Policy Best Practices",
            link_url="https://learn.microsoft.com/power-platform/admin/wp-data-loss-prevention",
            priority="Low",
            status="Success"
        )]
    
    # Has policies - check for blockers
    blocks_http = dlp_summary.get('blocks_http', False)
    blocks_custom = dlp_summary.get('blocks_custom_connectors', False)
    blocks_premium = dlp_summary.get('blocks_premium', False)
    tenant_wide = dlp_summary.get('tenant_wide_policies', 0)
    restricted = dlp_summary.get('restricted_connectors', [])
    
    # Critical blocker: HTTP connector blocked
    if blocks_http:
        return [new_recommendation(
            service="Power Platform",
            feature="DLP Governance - BLOCKER: HTTP Connector",
            observation=f"DLP policies block HTTP connector ({tenant_wide} tenant-wide) - BLOCKS Copilot plugin development entirely",
            recommendation=f"CRITICAL: HTTP connector is blocked by DLP policy, preventing ALL Copilot plugin creation. Required actions: 1) Create dedicated 'Copilot Extensibility' environment exempt from HTTP blocking, 2) Move plugin development to this environment, 3) Update DLP policy to allow HTTP in this environment only, 4) Document plugin approval process before production deployment. Without HTTP connector, you CANNOT build custom Copilot plugins or actions. This is the #1 blocker for M365 Copilot adoption. Escalate to governance team immediately. Blocked connectors: {', '.join(restricted)}",
            link_text="DLP Policy Configuration",
            link_url="https://learn.microsoft.com/power-platform/admin/create-dlp-policy",
            priority="High",
            status="Success"
        )]
    
    # High impact: Custom connectors blocked
    if blocks_custom:
        return [new_recommendation(
            service="Power Platform",
            feature="DLP Governance - WARNING: Custom Connectors",
            observation=f"DLP policies block custom connectors - limits Copilot extensibility to pre-built connectors only",
            recommendation=f"Custom connectors are restricted by DLP policy, limiting Copilot extensibility options. Recommended actions: 1) Create 'Copilot Innovation' environment with custom connector access, 2) Implement approval workflow for custom connectors (security review + IT approval), 3) Allow custom connectors for approved use cases (internal APIs, legacy systems), 4) Maintain list of approved custom connectors. Custom connectors enable Copilot to interact with proprietary systems and internal APIs - essential for enterprise scenarios. Consider risk-based exceptions rather than blanket blocking.",
            link_text="Custom Connector Governance",
            link_url="https://learn.microsoft.com/power-platform/admin/dlp-custom-connector-parity",
            priority="Medium",
            status="Success"
        )]
    
    # Medium impact: Premium connectors blocked
    if blocks_premium:
        return [new_recommendation(
            service="Power Platform",
            feature="DLP Governance - Premium Connector Restrictions",
            observation=f"DLP policies restrict premium connectors - may limit enterprise system integrations for Copilot",
            recommendation=f"Some premium connectors are restricted by DLP policy. Review restrictions: 1) Identify which premium connectors are needed for Copilot scenarios (SAP, Salesforce, ServiceNow, SQL), 2) Create exception policies for approved enterprise integrations, 3) Implement connector-specific governance (e.g., allow SAP but block social media), 4) Document business justification for each premium connector. Premium connectors enable Copilot to access enterprise systems - restricting them limits agent capabilities. Balance governance with innovation needs.",
            link_text="Premium Connector Management",
            link_url="https://learn.microsoft.com/power-platform/admin/dlp-connector-classification",
            priority="Medium",
            status="Success"
        )]
    
    # Has policies but no major blockers
    return [new_recommendation(
        service="Power Platform",
        feature="DLP Governance - Copilot Friendly",
        observation=f"{total_policies} DLP polic(ies) configured without blocking critical Copilot extensibility connectors - well balanced governance",
        recommendation=f"Current DLP governance ({total_policies} policies) preserves Copilot extensibility. Maintain this balance: 1) Monitor plugin connector usage monthly, 2) Add new connector restrictions only after risk assessment, 3) Communicate DLP changes to plugin developers, 4) Maintain dedicated environment for plugin testing. Good governance enables innovation while protecting data. Continue current approach while scaling agent adoption.",
        link_text="DLP Policy Review",
        link_url="https://learn.microsoft.com/power-platform/admin/dlp-policy-commands",
        priority="Low",
        status="Success"
    )]
