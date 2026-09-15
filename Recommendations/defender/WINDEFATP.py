"""
Microsoft Defender for Endpoint - Copilot & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation, NOT_ASSESSED_STATUS
from Core.friendly_names import get_friendly_sku_name

def get_recommendation(sku_name, status="Success", client=None, defender_client=None, defender_insights=None):
    """
    Defender for Endpoint protects devices where users interact with Copilot,
    preventing AI-powered attacks and malicious prompt injection at the endpoint.
    """
    feature_name = "Microsoft Defender for Endpoint"
    friendly_sku = get_friendly_sku_name(sku_name)
    
    if status == "Success":
        observation = f"{feature_name} is licensed; operational protection depends on the collected controls"
        recommendation = ""
        effective_status = status
        effective_priority = ""
        
        # Enrich with incident and device metrics from pre-computed insights
        if defender_insights and defender_insights.available:
            metrics = []
            
            # Add incident metrics
            if defender_insights.has_incidents():
                metrics.extend(defender_insights.incident_metrics)
                recommendation = defender_insights.incident_recommendation
            
            # Add device metrics (only if Defender API available - requires onboarded devices)
            if defender_insights.defender_api_available:
                dev = defender_insights.defender_client.device_summary
                if dev.get('high_risk', 0) > 0:
                    metrics.append(f"{dev['high_risk']} high-risk devices")
                    if not recommendation:
                        recommendation = f"Secure {dev['high_risk']} high-risk device(s)"
            
            if metrics:
                observation += ". " + ", ".join(metrics)
            elif defender_insights.source_was_read('incidents'):
                # Clean status - the incident feed was read and came back empty
                observation += ". No active incidents were returned by the completed incident query"
            else:
                # Incident data was never retrieved - do not report unread as clean
                observation += ". Endpoint incident data could not be retrieved, so device threat status is unverified"
                recommendation = ("Grant the assessment read access to Defender incidents and rerun, or review "
                                  "endpoint incidents directly at security.microsoft.com. Devices where staff "
                                  "use Copilot should be confirmed clean before broad rollout.")
                effective_status = NOT_ASSESSED_STATUS
                effective_priority = "Medium"
        
        return new_recommendation(
            service="Defender",
            feature=feature_name,
            observation=observation,
            recommendation=recommendation,
            link_text="Defender for Endpoint",
            link_url="https://learn.microsoft.com/microsoft-365/security/defender-endpoint/",
            priority=effective_priority or "Medium",
            status=effective_status
        )
    
    return new_recommendation(
        service="Defender",
        feature=feature_name,
        observation=f"{feature_name} is {status} in {friendly_sku}, leaving AI-enabled devices vulnerable to attacks",
        recommendation=f"Enable {feature_name} to protect the devices where employees use Copilot and agents. Endpoint security is critical because compromised devices could be used to inject malicious prompts, steal AI-generated sensitive data, or manipulate agent responses. Defender for Endpoint detects when attackers attempt to exploit AI interfaces, monitors for data exfiltration through copy/paste of Copilot outputs, and ensures that devices accessing powerful AI assistants meet security baselines. Essential for protecting the expanding attack surface created by AI adoption.",
        link_text="Endpoint Security for AI Workstations",
        link_url="https://learn.microsoft.com/microsoft-365/security/defender-endpoint/",
        priority="High",
        status=status
    )
