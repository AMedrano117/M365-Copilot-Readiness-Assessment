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

The direct `/v1.0/deviceManagement/settings` response is the production source.
Parent selected/full and SDK responses are diagnostic comparisons; they can be
empty even when the direct endpoint returns values. Null is not false or a default
validity period. `-IncludeBeta` adds explicitly labeled beta
requests for troubleshooting; it does not enable beta in production collection.
If only beta returns the values, retain that distinction when reporting the result.
Testing another tenant checks API behavior; it does not establish the original
customer's configuration. Do not copy its values into the customer's assessment.

The production collector reads the direct settings singleton and validates each
required value. A successful HTTP request with missing or unusable values remains
partial evidence; the original response is retained. An offline
rebuild cannot recover values that were never saved; future collection must use
the updated collector.

If the API returns numeric `deviceComplianceCheckinThresholdDays: 0`, the assessment
uses **30 days** for compliance check-in calculations and displays **30 (assumed
default; API returned 0)**. The raw response remains 0. Microsoft documents a
default of 30 days and a configurable range of 1-120 days; a zero API response has
also been reported on previously unchanged tenants. Missing, null, Boolean,
malformed and out-of-range values remain unknown. A nonzero configured period
remains authoritative; conflicting retained periods remain unresolved. The probe
prints the raw API value, so it will still print 0. Do not change tenant settings
just to make the diagnostic return a different value.

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
- [Compliance policy settings and documented validity range](https://learn.microsoft.com/en-us/intune/device-security/compliance/overview)
- [Microsoft365DSC report of a zero API value](https://github.com/Microsoft365DSC/Microsoft365DSC/issues/6450)
