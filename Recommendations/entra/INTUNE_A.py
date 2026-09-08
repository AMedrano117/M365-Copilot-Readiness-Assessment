"""
Microsoft Intune (Plan A) - Enhanced with Device Compliance Analysis
Provides license check + device compliance state + CA integration for Copilot device security.
"""
from Core.new_recommendation import new_recommendation
from Core.friendly_names import get_friendly_sku_name
from Recommendations.entra.entra_insights import entra_source_was_read

def get_recommendation(sku_name, status="Success", client=None, entra_insights=None):
    """
    Generate Intune recommendations with device compliance analysis for Copilot security.
    
    Returns multiple observations:
    1. License check (with upgrade path for lower SKUs)
    2. Device compliance analysis (if entra_insights available):
       - Non-compliant devices accessing Copilot: High priority
       - No managed devices (BYOD): Medium priority
       - Good compliance: Success
    3. CA integration check - Verify compliant device requirement for Copilot
    
    Args:
        sku_name: SKU name where feature is found
        status: Provisioning status
        client: Graph client (unused, for compatibility)
        entra_insights: Pre-computed identity metrics with device_summary
    """
    feature_name = "Microsoft Intune (Plan A)"
    friendly_sku = get_friendly_sku_name(sku_name)
    observations = []
    
    # ========================================
    # OBSERVATION 1: License Check
    # ========================================
    if status == "Success":
        observations.append(new_recommendation(
            service="Entra",
            feature=feature_name,
            observation=f"{feature_name} is active in {friendly_sku}, managing devices that access M365 Copilot with comprehensive security policies",
            recommendation="",
            link_text="Microsoft 365 Documentation",
            link_url="https://learn.microsoft.com/microsoft-365/",
            status=status
        ))
    else:
        # License not available - drive upgrade for device management
        observations.append(new_recommendation(
            service="Entra",
            feature=feature_name,
            observation=f"{feature_name} is {status} in {friendly_sku} - upgrade required for device compliance enforcement",
            recommendation=f"Upgrade to Microsoft 365 E3/E5 or Business Premium to enable Intune for Copilot device security. Without device management, users can access Copilot from: 1) Personal unmanaged devices where data can be copied/screenshot without DLP, 2) Non-compliant devices with malware that could intercept Copilot prompts/responses, 3) Jailbroken/rooted devices bypassing security controls, 4) Devices without encryption exposing Copilot data if lost/stolen. Intune allows you to: require device encryption and PIN, deploy compliance policies blocking non-compliant device access, wipe corporate data from lost devices, prevent copy/paste from Copilot on personal devices. Essential for preventing Copilot data leakage through unmanaged endpoints.",
            link_text="Intune Overview",
            link_url="https://learn.microsoft.com/mem/intune/fundamentals/what-is-intune",
            priority="High",
            status=status
        ))
    
    # ========================================
    # OBSERVATION 2: Device Compliance State
    # ========================================
    devices_read = entra_source_was_read(entra_insights, "managed_devices")
    if entra_insights and status == "Success" and not devices_read:
        observations.append(new_recommendation(
            service="Entra",
            feature=feature_name,
            observation="Managed-device inventory could not be read, so device enrollment and compliance coverage are unverified",
            recommendation="Grant DeviceManagementManagedDevices.Read.All and rerun, or verify Intune enrollment and compliance directly before making an endpoint-readiness decision.",
            link_text="Device Inventory Permissions",
            link_url="https://learn.microsoft.com/graph/api/intune-devices-manageddevice-list",
            priority="Medium",
            status="Not Assessed",
            disposition="Coverage"
        ))

    if entra_insights and status == "Success" and devices_read:
        device_summary = entra_insights.get('device_summary', {})
        total_devices = device_summary.get('total_managed_devices', 0)
        compliant_devices = device_summary.get('compliant_devices', 0)
        non_compliant_devices = device_summary.get('non_compliant_devices', 0)
        
        # An empty inventory is not proof that access is unmanaged. The tenant may
        # use another MDM, browser/session controls, or network controls that this
        # collector cannot see.
        if total_devices == 0:
            observations.append(new_recommendation(
                service="Entra",
                feature=feature_name,
                observation="The Intune query returned no managed devices. This alone does not establish whether AI access is controlled or uncontrolled.",
                recommendation="Confirm the tenant's endpoint control model (Intune, another MDM, browser/session controls, or network controls) and verify how it is enforced for Microsoft 365 before making an endpoint-readiness decision.",
                link_text="Device Enrollment",
                link_url="https://learn.microsoft.com/mem/intune/enrollment/",
                priority="Medium",
                status="Not Assessed",
                disposition="Coverage"
            ))
        
        # Non-compliant devices detected
        elif non_compliant_devices > 0:
            compliance_rate = (compliant_devices / total_devices * 100) if total_devices > 0 else 0
            observations.append(new_recommendation(
                service="Entra",
                feature=feature_name,
                observation=f"{non_compliant_devices} of {total_devices} managed devices ({100-compliance_rate:.1f}%) are non-compliant - may access Copilot despite policy violations",
                recommendation=f"Block Copilot access from {non_compliant_devices} non-compliant device(s) using Conditional Access. Non-compliant devices fail security requirements: missing encryption, outdated OS, disabled antivirus, jailbroken/rooted, or policy violations. These devices can: 1) Leak Copilot responses through screenshots on unencrypted storage, 2) Be compromised by malware intercepting AI prompts, 3) Violate compliance frameworks (HIPAA, SOC 2) requiring device security. Create CA policy targeting Microsoft 365 requiring 'Require device to be marked as compliant'. Review non-compliance reasons in Intune console and remediate or block access.",
                link_text="Require Compliant Devices",
                link_url="https://learn.microsoft.com/mem/intune/protect/device-compliance-get-started",
                priority="High",
                status=status
            ))
        
        # Good compliance
        else:
            observations.append(new_recommendation(
                service="Entra",
                feature=feature_name,
                observation=f"{compliant_devices} of {total_devices} managed devices (100%) are compliant, enforcing security policies for Copilot access",
                recommendation="",
                link_text="Device Compliance Best Practices",
                link_url="https://learn.microsoft.com/mem/intune/protect/device-compliance-get-started",
                status=status
            ))
    
    # ========================================
    # OBSERVATION 3: CA Integration Check
    # ========================================
    if entra_insights and status == "Success" and devices_read:
        device_summary = entra_insights.get('device_summary', {})
        ca_requires_compliance = device_summary.get('ca_requires_compliance', False)
        total_devices = device_summary.get('total_managed_devices', 0)
        
        # Has managed devices but CA doesn't require compliance
        if total_devices > 0 and not ca_requires_compliance:
            observations.append(new_recommendation(
                service="Entra",
                feature=feature_name,
                observation=f"{total_devices} devices managed by Intune, but Conditional Access does not require compliant devices for Copilot",
                recommendation="Create Conditional Access policy to require compliant devices for Microsoft 365 apps (including Copilot). Currently, non-compliant devices can bypass Intune security policies and access Copilot. Configure: Target 'Office 365' application, Grant 'Require device to be marked as compliant', Apply to all users with Copilot licenses. This ensures only encrypted, malware-protected, policy-compliant devices can access AI capabilities, preventing data leakage through compromised endpoints.",
                link_text="Device Compliance with CA",
                link_url="https://learn.microsoft.com/entra/identity/conditional-access/howto-conditional-access-policy-compliant-device",
                priority="Medium",
                status=status
            ))
        
        # CA properly requires compliance - success observation
        elif total_devices > 0 and ca_requires_compliance:
            observations.append(new_recommendation(
                service="Entra",
                feature=feature_name,
                observation=f"Conditional Access requires compliant devices for Microsoft 365 apps, enforcing Intune policies for Copilot access",
                recommendation="",
                link_text="Device Compliance with CA",
                link_url="https://learn.microsoft.com/entra/identity/conditional-access/howto-conditional-access-policy-compliant-device",
                status=status
            ))
    
    return observations
