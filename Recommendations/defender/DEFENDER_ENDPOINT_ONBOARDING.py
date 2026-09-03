"""
Microsoft Defender for Endpoint - Device Onboarding Status
Checks if Defender for Endpoint API is accessible and devices are onboarded
"""
from Core.new_recommendation import new_recommendation


async def get_recommendation(client, defender_client=None, services_and_licenses=None, purview_client=None):
    """
    Check if Defender for Endpoint is properly onboarded with devices
    
    A failed or forbidden machines query is an assessment coverage gap. It is not evidence
    that the tenant has zero onboarded devices.
    """
    
    # Check if defender_client is available and if Defender API is working
    if defender_client is None:
        return new_recommendation(
            service="Defender",
            feature="Defender for Endpoint - Device Onboarding",
            status="Not Assessed",
            priority="Medium",
            observation=(
                "**Defender client not initialized.** Unable to assess device onboarding status."
            ),
            recommendation=(
                "Ensure Defender for Endpoint license is assigned and service principal has required permissions "
                "(Machine.Read.All, Incident.Read.All). Re-run the tool after configuration."
            ),
            link_text="Set up Defender for Endpoint",
            link_url="https://learn.microsoft.com/en-us/microsoft-365/security/defender-endpoint/onboarding",
            disposition="Coverage",
            evidence_key="defender_device_detail",
            evidence_summary="See Defender Device Detail for the available endpoint inventory and risk posture returned by the tenant review."
        )

    data_sources = getattr(defender_client, "data_sources", {}) or {}
    machines_read = data_sources.get(
        "machines",
        getattr(defender_client, "defender_api_available", False),
    )
    if not machines_read:
        return new_recommendation(
            service="Defender",
            feature="Defender for Endpoint - Device Onboarding",
            status="Not Assessed",
            priority="Medium",
            observation=(
                "**Defender for Endpoint device inventory could not be read.** "
                "The assessment cannot determine whether devices are onboarded or healthy."
            ),
            recommendation=(
                "Grant the assessment application Machine.Read.All for the Microsoft Defender for Endpoint API, "
                "confirm Defender for Endpoint is provisioned, and rerun. Alternatively, verify device onboarding "
                "directly in security.microsoft.com. Do not treat this result as zero onboarded devices."
            ),
            link_text="Verify Defender for Endpoint access",
            link_url="https://learn.microsoft.com/en-us/microsoft-365/security/defender-endpoint/onboarding",
            disposition="Coverage",
            evidence_key="defender_device_detail",
            evidence_summary="The Defender machines query was not successfully read; no device-count conclusion is supported."
        )
    
    # Defender API is working - check device count
    machines_count = defender_client.device_summary.get('total', 0) if defender_client.device_summary else 0
    
    if machines_count == 0:
        # API works but no devices (shouldn't happen, but defensive check)
        return new_recommendation(
            service="Defender",
            feature="Defender for Endpoint - Device Onboarding",
            status="Warning",
            priority="Medium",
            observation=(
                f"**Defender for Endpoint is active but no devices are reporting.**\n\n"
                "API is accessible but device inventory is empty. This limits threat detection capabilities."
            ),
            recommendation=(
                "Onboard devices to Defender for Endpoint to enable full security monitoring. "
                "See Settings → Endpoints → Onboarding in Microsoft 365 Defender portal."
            ),
            link_text="Device onboarding guide",
            link_url="https://learn.microsoft.com/en-us/microsoft-365/security/defender-endpoint/onboarding",
            disposition="Action",
            evidence_key="defender_device_detail",
            evidence_summary="See Defender Device Detail for the device inventory currently available for follow-up."
        )
    elif machines_count < 10:
        # Few devices onboarded - suggest scaling up
        return new_recommendation(
            service="Defender",
            feature="Defender for Endpoint - Device Onboarding",
            status="Success",
            priority="Low",
            observation=(
                f"**{machines_count} device(s) onboarded** to Defender for Endpoint.\n\n"
                "API is fully functional. Consider onboarding additional devices for comprehensive security coverage."
            ),
            recommendation=(
                "Review device inventory to ensure all critical endpoints are protected. "
                "Use Intune or Group Policy to automate onboarding at scale for production environments."
            ),
            link_text="Scale Defender for Endpoint deployment",
            link_url="https://learn.microsoft.com/en-us/microsoft-365/security/defender-endpoint/deployment-phases",
            evidence_key="defender_device_detail",
            evidence_summary="See Defender Device Detail for the currently onboarded devices and their reported risk posture."
        )
    else:
        # Good device coverage
        return new_recommendation(
            service="Defender",
            feature="Defender for Endpoint - Device Onboarding",
            status="Success",
            priority="Low",
            observation=(
                f"**{machines_count} devices onboarded** to Defender for Endpoint. API is fully operational."
            ),
            recommendation=(
                "Device onboarding is healthy. Continue monitoring device compliance and onboard new devices as they join the environment."
            ),
            link_text="Monitor device health",
            link_url="https://learn.microsoft.com/en-us/microsoft-365/security/defender-endpoint/device-health-sensor-health-os",
            evidence_key="defender_device_detail",
            evidence_summary="See Defender Device Detail for the onboarded devices and the risk/exposure fields returned for each device."
        )
