# Copilot admin-center details collected automatically

Run the normal live assessment first. Request only the additional portal details relevant to
the customer's scope. The three Usage, Security and Optimize PDFs are no longer a standard
intake requirement. They remain optional visual context. To include them, put the PDFs in
`--reports-dir`; [automatic PDF import](PORTAL_REVIEW.md) creates JSON, local text/OCR and
page previews. This does not refresh the PDFs from the portal or establish scored controls.

```powershell
.\.venv\Scripts\python.exe main.py --mode live
```

## Coverage

| Admin-center topic | Automatic coverage | Remaining review |
|---|---|---|
| Paid Copilot adoption | Enabled and active users, application counts and trends from Microsoft Graph. | Compare the source period/date before comparing with a portal card. The default v2 period is 28 days, which can differ from a 30-day portal view. |
| Paid Copilot engagement | Aggregate prompts across apps, work-chat prompts, web-chat prompts and active user-days from the v2 usage report. | Missing fields remain unknown. User-days sum users' active days; they are not distinct calendar days. |
| Copilot subscription capacity | Enabled and assigned seats from recognized paid Copilot products in the subscription catalog. | Seats are not unique people, actual usage, or proof of successful provisioning. |
| Copilot DLP | Policy mode, returned Copilot targeting, inclusion/exclusion counts and linked content-processing block actions. Monitoring-only policies are identified explicitly. | Review conditions, group membership, exclusions and effective behavior for the intended users. A policy name alone does not prove its target. |
| Other security/configuration evidence | Existing audit configuration, label/policy evidence, identity/security checks, SharePoint sharing settings and visible external connections. | These do not reproduce the portal's recommendation percentages or referenced file/site totals. |
| Unlicensed Copilot Chat | Not returned by the Graph Copilot reporting endpoints. | Review the Chat usage report if this adoption measure is in scope. Audit activity is a different measure. |
| Agent activity, Search, credits and assisted hours | The collectors do not reproduce these portal metrics. | Request relevant cards only when these experiences or value/cost measures are in scope. |
| Full Optimize checklist | Some underlying configuration is collected; the full checklist is not. | Review applicable remaining settings. Screenshots are optional evidence of the review. |

The HTML places these observations under **Collected Copilot configuration and activity**
in the relevant assessment areas. The workbook adds **Copilot Admin Data**, **Copilot DLP Detail**
and **Portal Review Remaining**. Missing values are not treated as zero or as a new readiness
failure. Configuration observations alone do not approve a pilot or broader rollout.

## Access, privacy and saved assessments

No additional permission or command-line option is required beyond the existing Graph
reporting/subscription access and Purview policy-reading roles. The usage collector reads
the v2 user-detail response in memory to calculate aggregates. It saves no raw user-detail
rows unless `--include-user-usage-detail` is explicitly selected. It does not collect prompt
content. DLP targeting details remain in the technical workbook; the customer tables summarize
inclusions/exclusions without listing their identities.

These additional fields require a new live collection. Rebuilding an older collection can
show already saved policy modes, but cannot recover fields that were never collected. Missing
dates remain identified as unrecorded. New observations keep their source dates on offline replay.

The Purview live cache format is now version 3, so an older live cache triggers a fresh policy
read. Explicit offline imports still accept versions 2 and 3. No tenant settings or scans are
changed by this collection.

The full Optimize checklist is not exposed through the interfaces used here. Microsoft's
individual Limited Mode API currently requires delegated access; it is not added to the
service-principal workflow. Preview-only settings are also not added to the default collection.

## Microsoft references

- [Copilot usage user-detail API: v2 fields, permissions and unlicensed Chat limitation](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/api/admin-settings/reports/copilotreportroot-getmicrosoft365copilotusageuserdetail).
- [Read DLP policies](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-dlpcompliancepolicy?view=exchange-ps) and [read DLP rules](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-dlpcompliancerule?view=exchange-ps).
- [Limited Mode API and supported permission types](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/api/admin-settings/copilotadminlimitedmode-get).
- [Optimize configuration guidance](https://learn.microsoft.com/en-us/microsoft-365/copilot/optimize-microsoft-365-configuration-settings).

For optional captures, see [Reviewed portal captures](PORTAL_REVIEW.md). For the complete
assessment intake, see [New tenant checklist](NEW_TENANT_CHECKLIST.md).
