# First assessment of a new customer tenant

[Documentation index](README.md) | [Project overview](../README.md)

Use this checklist for each new customer. Collect that tenant's evidence and establish its
assessment scope before preparing the customer deliverable. Never reuse another customer's
collection, exports, screenshots, review statements or approvals.

Collect supported evidence automatically first, then request only remaining portal details
relevant to the customer's scope. The three Copilot admin-center PDFs are optional, not a
standard intake requirement. See [automatic coverage and remaining review](COPILOT_AUTOMATIC_COLLECTION.md).
If a page or report is unavailable, record why and what remains unverified. Missing screenshots
alone do not prove a failed control.

## 1. Customer information, scope and access

- [ ] Customer name and tenant GUID.
- [ ] Agreed permission profile: Standard or Restricted, with [remaining evidence gaps](PERMISSIONS.md#evidence-gaps-and-offline-replay) understood.
- [ ] For Standard SharePoint administration: open this customer's **Microsoft 365 admin center > Admin centers > SharePoint**, verify the tenant, and record the actual HTTPS origin without a page path or query. Do not infer the hostname from an `onmicrosoft.com` domain. See [URL configuration](prereq.md#sharepoint-admin-url).
- [ ] For Standard Purview administration: record the initial `onmicrosoft.com` organization domain separately from the SharePoint URL.
- [ ] Assessment application configured and consented in this tenant, with its client ID
      and supported certificate or client secret. Arrange the workload access in [prereq.md](prereq.md).
- [ ] Selected environment file exists; review the startup tenant, application, profile and authentication summary before collection. Resolve any setup expiry or credential-recovery message.
- [ ] For Standard delegated collection: administrators available for SharePoint and Purview/Exchange sign-in. Restricted uses customer exports or saved evidence instead.
- [ ] Business sponsor, proposed pilot users, approved content/sites and devices.
- [ ] Intended use cases, current deployment status, baseline, success measures and stop/expansion criteria.
- [ ] Agents and external AI explicitly included or excluded from the assessment scope.

Use a separate environment file, for example `.env.newcustomer`, starting from `.env.example`.
Set the new tenant's `TENANT_ID`, `CLIENT_ID` and authentication values. For Standard administrative collection, also set the confirmed `SHAREPOINT_ADMIN_URL`
and, where needed, the separate `PURVIEW_ORGANIZATION`. Restricted needs neither administrative setting. Check all optional certificate and report paths
belong to this customer. Keep credentials outside the portable assessment package.

The application setup script writes Standard configuration to `.env` and Restricted configuration
to `.env.restricted`; it does not accept an output environment-file parameter. Run setup in a separate customer working copy if needed
to preserve an existing customer's configuration. For an already configured application,
`main.py --env-file` can select a separate customer configuration in this working copy.

Standard setup accepts `-SharePointAdminUrl "https://<actual-prefix>-admin.sharepoint.com"` to save the confirmed URL. Without it, setup preserves a valid saved URL only for the same tenant or leaves it blank. The live run can prompt when SharePoint collection is selected and interaction is allowed; noninteractive collection with no usable URL leaves SharePoint administration unassessed as a configuration gap, not a permission failure.

For Restricted, setup uses the dedicated `M365 Copilot Readiness Assessment Tool - Restricted`
application. It omits Graph site inventory, group licensing, delegated consent-grant inventory
and all SharePoint/Purview administrative PowerShell while keeping stable identity, device,
security, usage and external-connection reads. Restricted rejects Unattended setup, preview packs
and legacy administrative collection. A profile change or `--services` selection does not revoke
consent. Review the [permission matrix and grant cleanup](PERMISSIONS.md) before reusing an app;
Restricted setup and preflight stop on excess requested or granted application permissions.

## 2. Evidence to request

| Item | What to obtain | How it is used |
|---|---|---|
| Fresh live collection | Run this tool against the new tenant using the agreed permission profile and retain the complete portable package. | Supported tenant, licensing, usage, identity, security and policy evidence. Check each source's outcome and intentional exclusions; sign-in success alone does not establish collection completeness. |
| Remaining Copilot Usage details | Only relevant cards not automatically collected, such as unlicensed Chat, agent activity or credits. Show the tenant, reporting window and refresh date. | Optional context. Paid usage, prompt aggregates and subscription seats are collected automatically when accessible. |
| Remaining Copilot Security details | Relevant dashboard recommendations or totals not explained by the collected policies and access evidence. | Optional context. Standard administrative collection reads policy modes and returned Copilot targeting/actions. Restricted needs separate evidence; dashboard completion percentages and file/site totals are separate. |
| Copilot Optimize review | Review applicable checklist items not covered by collected configuration; optionally retain readable captures. | The full checklist is not automatically collected. Record unavailable sections and any relevant unresolved settings. |
| Copilot Readiness CSV | Original user-table export with refresh date and report period. | Supported technical eligibility and application-readiness evidence. This does not measure actual Copilot usage. |
| SharePoint and OneDrive access reports | Completed organization permission snapshots, plus relevant sharing-link and Everyone/Everyone except external users activity or detail exports. | Content access and oversharing evidence. Reuse files successfully retrieved by the live collector; manually supply missing exports. |
| Content Management Assessment | Original CSV covering site ownership and inactivity, with its confirmed report date. | Ownership and lifecycle review. The tool's default lifecycle age limit is 90 days. |
| Sensitive-content access evidence | Completed Purview data risk assessment covering the intended sites/users, or a dated scoped owner review of sensitive content and its effective access. | Answers the sensitive-data access question. A label inventory or tenant sharing default alone is insufficient. |
| Pilot plan and control reviews | Dated sponsor-approved scope/plan and specific owner reviews for remaining control questions. | Needed to establish the readiness milestones. Record actual reviews using the assessment-profile workflow. |
| Agent or external-service inventory | Supported inventory and boundary reviews when those products are in scope. | Supplemental evidence for the agreed scope. |

Request long-running reports early. First inspect completed results before asking an administrator
to start another report. The assessment tool does not initiate Microsoft scans.

Microsoft instructions:

- [Copilot Readiness and Usage reports](https://learn.microsoft.com/en-us/microsoft-365/copilot/microsoft-365-copilot-reports-for-admins).
- [SharePoint Data Access Governance](https://learn.microsoft.com/en-us/sharepoint/data-access-governance-reports).
- [Content Management Assessment](https://learn.microsoft.com/en-us/sharepoint/content-management-assessment).
- [Purview data risk assessments](https://learn.microsoft.com/en-us/purview/data-security-posture-management-oversharing).
- [Copilot Optimize settings](https://learn.microsoft.com/en-us/microsoft-365/copilot/optimize-microsoft-365-configuration-settings).

Report access depends on the customer's roles, subscriptions and feature provisioning. For an
unavailable report, record the exact limitation, affected scope and any suitable alternative
evidence. Use the [compatibility matrix](PORTAL_REPORTS_AND_OFFLINE.md#sample-compatibility-matrix)
to check structured imports. In particular, a new portal Usage CSV is not automatically supported
by the Readiness importer; live usage is collected separately.

## 3. Run the assessment

The paths below are examples. First prepare the environment file and the customer's export folder.

For a new Restricted configuration in this customer working copy:

```powershell
.\setup-service-principal.ps1 -PermissionProfile Restricted
.\.venv\Scripts\python.exe main.py --mode live --env-file .env.restricted --check-connections
.\.venv\Scripts\python.exe main.py --mode live --env-file .env.restricted `
  --reports-dir ".\output\newcustomer\exports"
```

Setup writes `PERMISSION_PROFILE=restricted`. Live profile precedence is explicit
`--permission-profile standard|restricted`, then `PERMISSION_PROFILE` in the selected environment,
then `standard`. A CLI override must be used with the intended app; it does not change its grants.
The commands below show the existing Standard workflow with a manually selected customer file:

```powershell
.\.venv\Scripts\python.exe main.py --mode live `
  --env-file ".\.env.newcustomer" `
  --check-connections

.\.venv\Scripts\python.exe main.py --mode live `
  --env-file ".\.env.newcustomer" `
  --interactive-auth fresh `
  --reports-dir ".\output\newcustomer\exports"
```

Omit `--reports-dir` if exports are not yet available. Sign in to the intended customer tenant
for each interactive service. The live run saves a new collection and package automatically.
Use the exact path printed under **COLLECTION INPUT** for subsequent builds.

Organize local inputs separately:

```text
.env.newcustomer                       credentials; outside the package
output/newcustomer/
  exports/                             supported CSV/XLSX exports and PDF captures
    Copilot Admin Pages/               optional PDF subfolder
  assessment-profile.json              actual scope, plan and owner reviews
```

Put any relevant PDFs in `exports/` or a subfolder. `--reports-dir` automatically creates
JSON and previews, and extracts local text/OCR. No manual manifest is needed. Check the
extracted context against its pages before sharing; see [PDF import](PORTAL_REVIEW.md).
Structured CSV/XLSX/ZIP discovery is nonrecursive; place those exports directly in `exports/`
or repeat `--reports-dir` for their separate folders.

Prepare any completed owner reviews using [READINESS_REVIEWS.md](READINESS_REVIEWS.md).
Then rebuild with the inputs that actually exist:

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input "<new customer's saved collection.json>" `
  --reports-dir ".\output\newcustomer\exports" `
  --assessment-profile ".\output\newcustomer\assessment-profile.json" `
  --open-html-report
```

Omit `--assessment-profile` while reviews are still pending; the report will identify the
unanswered requirements. Do not invent pass results to complete the profile. Subsequent builds restore the
packaged inputs, so those inputs do not need to be supplied again.

The same offline command replays Restricted collections without an environment file. The saved
permission profile and intentional exclusions remain visible; older collections show an unrecorded
profile. SAM/DAG and DSPM exports add their own scoped evidence but do not replace every omitted
administrative configuration check. No skipped source should appear as zero exposure or a healthy result.

## 4. Complete the customer review

- [ ] Confirm the tenant identity, collection date, export dates and covered population.
- [ ] Check source failures and unsupported, empty or incomplete imports.
- [ ] Check the recorded permission profile, intentional exclusions and remaining unassessed controls.
- [ ] Review every portal capture and its notes, including failed-to-load or clipped panels.
- [ ] Confirm the pilot plan and owner reviews are recorded and linked to actual evidence.
- [ ] Review the current readiness stage and each unmet requirement with the responsible owners.
- [ ] Deliver the HTML with its companion workbook and retain the complete portable package.
- [ ] Agree the retention period and closeout owner; verify offline replay of the retained package.
- [ ] Confirm the selected cleanup environment contains the exact tenant/client GUIDs and agreed profile;
      retain the enterprise application's object ID for any workload-cleanup retry.
- [ ] Preview removal of the dedicated assessment application's access using [CLEANUP.md](CLEANUP.md).
      For Standard Unattended, review workload RBAC cleanup before deleting Entra objects.
- [ ] After authorization, apply reviewed access removal and separately run `cleanup-local-assessment.ps1`.
      Review its list of all customers' artifacts in the built-in storage locations before the single
      deletion confirmation, or use optional `-Path` to select only this customer's copies.
- [ ] Record remaining assignments, certificate reuse and retained evidence locations.

A successful run does not automatically establish readiness. Ready for pilot requires a
reviewed scope and plan, supported control coverage, and appropriate treatment of remaining
findings. Broader adoption additionally requires reviewed pilot outcomes and approval/control
coverage for the larger population. See the [exact milestone requirements](READINESS_REVIEWS.md).
