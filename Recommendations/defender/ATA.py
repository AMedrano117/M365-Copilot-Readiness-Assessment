"""
Microsoft Defender for Identity - Copilot & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation, NOT_ASSESSED_STATUS
from Core.friendly_names import get_friendly_sku_name

def get_recommendation(sku_name, status="Success", client=None, defender_client=None, defender_insights=None):
    """
    Defender for Identity detects compromised accounts attempting to abuse
    Copilot to access and exfiltrate organizational data through AI.
    """
    feature_name = "Microsoft Defender for Identity"
    friendly_sku = get_friendly_sku_name(sku_name)
    
    if status == "Success":
        observation = f"{feature_name} is active, protecting Copilot workloads"
        recommendation = ""
        effective_status = status
        effective_priority = ""
        
        # Enrich with identity risk metrics from pre-computed insights
        if defender_insights and defender_insights.available:
            if defender_insights.has_identity_risks():
                observation += ". " + ", ".join(defender_insights.identity_metrics)
                recommendation = defender_insights.identity_recommendation
            elif defender_insights.source_was_read('risky_users', 'risky_sign_ins'):
                # Clean status - identity risk data was read and came back empty
                observation += ". No risky users or sign-ins detected"
            else:
                # Identity risk data was never retrieved - do not report unread as clean
                observation += ". Identity risk data could not be retrieved, so account risk is unverified"
                recommendation = ("Grant the assessment IdentityRiskyUser.Read.All and "
                                  "IdentityRiskEvent.Read.All and rerun, or review risky users directly in "
                                  "Entra ID Protection. Compromised accounts are a primary Copilot data "
                                  "exfiltration path and cannot be left unchecked.")
                effective_status = NOT_ASSESSED_STATUS
                effective_priority = "Medium"
        
        return new_recommendation(
            service="Defender",
            feature=feature_name,
            observation=observation,
            recommendation=recommendation,
            link_text="Defender for Identity",
            link_url="https://learn.microsoft.com/defender-for-identity/what-is",
            priority=effective_priority or "Medium",
            status=effective_status
        )
    
    return new_recommendation(
        service="Defender",
        feature=feature_name,
        observation=f"{feature_name} is {status} in {friendly_sku}, missing detection of compromised accounts using Copilot",
        recommendation=f"Enable {feature_name} to detect when stolen credentials are used to abuse Copilot for data theft. Attackers who compromise user accounts can use Copilot to quickly gather intelligence ('Summarize all M&A discussions from the last month'), identify valuable data, and exfiltrate information at scale. Defender for Identity detects anomalous Copilot usage patterns, unusual data access through AI, and reconnaissance activities where attackers use Copilot to map your organization. This is a critical security control because Copilot makes data discovery extremely efficient for both legitimate users and attackers.",
        link_text="Identity Protection for AI Access",
        link_url="https://learn.microsoft.com/defender-for-identity/what-is",
        priority="High",
        status=status
    )
