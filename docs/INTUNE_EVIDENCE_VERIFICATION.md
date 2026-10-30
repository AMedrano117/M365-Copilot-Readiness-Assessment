# Verify Intune compliance evidence

Use this bounded, read-only diagnostic when saved tenant settings are missing or
setting results appear inconsistent with overall device compliance. It does not
run an assessment, change settings or write a report. Run it as an operator who
can sign in to the intended tenant and read Intune configuration and devices.

From the repository root, use the script in the checkout being reviewed. For PR
29, that checkout is `.cache/devices`; after merge, use `tools` at the root.

```powershell
# Once, if these Graph modules are not installed:
Install-Module Microsoft.Graph.Authentication, Microsoft.Graph.DeviceManagement -Scope CurrentUser

# Replace the placeholder with the tenant being tested:
.\.cache\devices\tools\Test-IntuneEvidence.ps1 -TenantId "<tenant-guid>" -IncludeBeta
```

The script verifies the signed-in tenant before data reads. It requests only
`DeviceManagementConfiguration.Read.All` and
`DeviceManagementManagedDevices.Read.All`. Sign-in and consent requirements depend
on the tenant. No environment file is needed for this delegated diagnostic.

## Tenant settings

Compare `TenantSettings` with Intune **Devices > Compliance > Compliance policy
settings** (portal navigation may vary):

| Output | Portal setting |
| --- | --- |
| `SecureByDefault = false` | Mark devices with no compliance policy assigned as **Compliant** |
| `SecureByDefault = true` | Mark devices with no compliance policy assigned as **Not compliant** |
| `ComplianceValidityDays` | Compliance status validity period (days) |

Null is not false or a default validity period. Compare the selected and full
v1.0 responses and the SDK response. `-IncludeBeta` adds explicitly labeled beta
requests for troubleshooting; it does not enable beta in production collection.
If only beta returns the values, retain that distinction when reporting the result.
Testing another tenant checks API behavior; it does not establish the original
customer's configuration. Do not copy its values into the customer's assessment.

The production collector validates each required value, retries an incomplete
selected response using the full v1.0 singleton and retains both attempts. A
successful HTTP request with missing values remains partial evidence. An offline
rebuild cannot recover values that were never saved; future collection must use
the updated collector.

## Compare an incomplete check on one device

Choose two or three affected devices locally. Use the **Intune managed-device
ID**, not the Entra object ID, to query each device:

```powershell
.\.cache\devices\tools\Test-IntuneEvidence.ps1 `
  -TenantId "<tenant-guid>" `
  -ManagedDeviceId "<intune-managed-device-guid>" `
  -IncludeBeta
```

For the same device in Intune, inspect overall compliance, last check-in, each
policy's status, and the individual setting result. Include the Default Device
Compliance Policy's assignment, active-device and enrolled-user checks. Compare
the same check and reporting period, rather than assuming overall noncompliance
explains every setting result.

The probe keeps overall, policy and setting states separate. `error`, `unknown`,
`conflict` and unevaluated results do not establish a failed security requirement.
If the portal and detailed endpoint both show `nonCompliant` for the same check
while saved summary evidence says `error`, that is useful evidence of an endpoint
or timing discrepancy. Preserve both results and their dates before changing
classification logic. Current device activity and report freshness are independent.

Output excludes device/user/policy names, IDs, diagnostic messages and tokens;
policy labels are local aliases. It includes technical setting keys, states,
diagnostic codes and whether diagnostic text/contributing policies were returned.
Reads are capped at five policies and five pages per collection; completeness
flags identify truncation. Beta setting reads can fail independently. Review
output locally before sharing. Provide only the settings comparison and anonymized
overall/policy/setting statuses, portal check label and observation time. Do not
share the environment file or raw collection.

## References

- [Device-management settings](https://learn.microsoft.com/en-us/graph/api/resources/intune-deviceconfig-devicemanagementsettings?view=graph-rest-1.0)
- [Detailed compliance setting states and diagnostics](https://learn.microsoft.com/en-us/graph/api/resources/intune-deviceconfig-devicecompliancepolicysettingstate?view=graph-rest-1.0)
- [Get-MgDeviceManagement](https://learn.microsoft.com/en-us/powershell/module/microsoft.graph.devicemanagement/get-mgdevicemanagement?view=graph-powershell-1.0)
