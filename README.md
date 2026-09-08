# Enterprise AI Readiness Assessment for the Microsoft 365 Data Estate

This automated assessment helps organizations evaluate the Microsoft 365 foundation used by Microsoft 365 Copilot, AI agents, and external AI services that receive or connect to M365 data. It analyzes tenant evidence across identity, content access, data protection, application consent, endpoints, threats, licensing, and adoption.

The report distinguishes actionable tenant conditions from optional adoption opportunities,
verified controls, and data the assessment could not read. See [METHODOLOGY.md](METHODOLOGY.md)
for decision rules, evidence standards, and the boundary for ChatGPT, Claude, Cursor, and other
external AI providers.

## Assessing Copilot Readiness

Determining whether your organization meets the prerequisites for Microsoft 365 Copilot deployment requires evaluating licensing, security controls, compliance policies, and infrastructure across multiple service areas. Traditional assessment approaches rely on manual questionnaires and subjective evaluation.

**Automated Readiness Assessment** provides an objective, data-driven alternative by retrieving data from Microsoft APIs to analyze your actual tenant configuration and generate actionable recommendations.

## Automated Readiness Assessment

Automated Readiness Assessment is an evolution of manual evaluation processes. It uses a script-based approach to analyze current configurations across six M365 service areas. High-level workflow:

```mermaid
graph LR
    A[User<br/>Configure & Execute] --> B[Assessment Tool<br/>Python + PowerShell]
    B --> C[Microsoft Graph APIs<br/>Licenses, Identity, Security]
    B --> D[Defender APIs<br/>Threats, Endpoints, Posture]
    B --> E[Exchange Online APIs<br/>Purview Compliance]
    B --> F[Power Platform APIs<br/>Environments, DLP, AI Builder]
    C --> G[Reports<br/>CSV & Excel]
    D --> G
    E --> G
    F --> G
```

Each component:

- **User** - Clone repository, execute Python script with command-line options to select tenant and services (or configure `params.py`)
- **Assessment Tool** - Python orchestrator using PowerShell cmdlets and Microsoft APIs for data collection
- **Microsoft Graph APIs**
  - **Microsoft Graph**: Organization details, license assignments, identity protection, conditional access
  - **Defender for Endpoint**: Security recommendations, exposure scores, threat intelligence, incidents
  - **Exchange Online (Purview)**: DLP policies, sensitivity labels, retention policies
  - **Power Platform Management**: Environments, DLP boundaries, AI Builder, connectors
- **Service Areas**
  - **M365**: Copilot license consumption, Microsoft 365 Apps features, Teams Premium capabilities
  - **Entra**: Risky users, MFA enforcement, conditional access, B2B guest policies
  - **Defender**: XDR activation, endpoint coverage, security posture, OAuth app risks
  - **Purview**: Data classification, information governance, compliance boundaries
  - **Power Platform**: Environment governance, connector policies, AI Builder readiness
  - **Copilot Studio**: Agent licensing, custom agent deployment, conversation analytics

Querying APIs enables precise evaluation of configuration status across these design areas:

- **M365 Licensing**: Copilot license assignments, service plan provisioning status, feature availability
- **Security Posture**: Defender exposure scores, critical vulnerabilities, compromised accounts, OAuth app risks
- **Identity Protection**: Risky user counts, MFA coverage, conditional access policies, sign-in risk policies
- **Compliance Readiness**: DLP policy coverage, sensitivity label adoption, retention enforcement
- **Power Platform Governance**: Environment-level DLP, connector classification, AI Builder model monitoring
- **Copilot Studio**: Agent deployment readiness, authentication configurations, transcript retention

There are multiple design area evaluations implemented in Automated Readiness Assessment, each producing observations and prioritized recommendations.

### Benefits

**Time**: Assessment completes in seconds. Target specific services (e.g., only Defender + Purview) or run comprehensive analysis across all six areas. No manual form-filling or lengthy questionnaires.

**Cost**: Open-source tool with no licensing fees. Leverages existing Microsoft 365 admin permissions - no third-party agents or data exports required.

**Quality**: API-driven analysis eliminates guesswork from architectural discussions. Precise outcomes based on actual tenant configuration can be reviewed with stakeholders, auditors, and executive sponsors to improve quality further.

**Reproducibility**: Re-run assessments after implementing recommendations to measure progress. Timestamped reports enable tracking readiness improvements over time.

There are multiple design area evaluations implemented in Automated Readiness Assessment, each producing observations and prioritized recommendations.

## Assessment Report

The assessment generates a decision-oriented HTML report and detailed CSV/Excel evidence. The
HTML report leads with deployment actions; inventory and engineer detail remain available without
overwhelming the main decision:

![Assessment Report Output](Media/ReportHTMLOutput1.png)

The report includes:
- **Disposition**: Action, Opportunity, Assurance, Coverage, or Reference
- **Readiness Stage**: Before pilot, before broad rollout, pilot condition, optimize, or maintain
- **Impact Area**: Identity, data protection, content access, endpoint, threat, agent, adoption, or licensing
- **AI Applicability**: M365 Copilot, AI agents/connected apps, or all AI using M365 data
- **Evidence Basis and Confidence**: Tenant evidence, tenant observation, license signal, or not verified
- **Service Area**: M365, Entra, Defender, Purview, Power Platform, or Copilot Studio
- **Feature**: Configuration domain (Licensing, Security, Compliance, Governance)
- **Status**: Current state (Compliant, Warning, Not Configured)
- **Priority**: Recommended action priority (High, Medium, Low)
- **Observation**: Detailed description of what was discovered
- **Recommendation**: Specific action to improve Copilot readiness

Reports are timestamped (e.g., `m365_recommendations_20260106_143106.csv`) to track progress across multiple assessment runs.

### Data exposure and oversharing

The Data Exposure service analyzes completed SharePoint Advanced Management Data Access
Governance and Microsoft Purview DSPM exports. It checks explicit Anyone, EEEU/Everyone,
organization-wide, external, potentially overshared, ownerless, sensitive, and unlabeled-content
signals. It does not infer oversharing from file or site volume.

Because Microsoft runs these scans asynchronously, the tool reuses recent exports and records a
Coverage item—with enablement, scan, export, and rerun instructions—when a scan is missing, stale,
unreadable, or undated. See [RUN.md](RUN.md#data-exposure-and-oversharing-reports) for usage.

### AI adoption and usage evidence

The HTML report separates license coverage, actual Copilot activation and engagement, Microsoft
365 app readiness, optional Power Platform extensibility, and optional external-AI discovery.
Aggregate Copilot and Microsoft 365 Apps reports use the existing `Reports.Read.All` application
permission and direct Graph REST calls; no additional Graph SDK package is introduced for them.

Optional switches:

```powershell
python main.py --include-user-usage-detail
python main.py --copilot-dashboard-export .\exports\copilot-dashboard.csv
python main.py --power-platform-inventory .\exports\power-platform-inventory.csv
python main.py --preview-collectors shadow-ai
```

User-level Copilot activity is collected only by explicit request and appears only in the
restricted Excel workbook. Shadow AI is aggregate-only, disabled by default, and requires the
optional Graph application permission `CloudApp-Discovery.Read.All` plus configured Defender for
Cloud Apps discovery data. Missing optional evidence is never reported as zero use and cannot
change core security readiness.

### Cross-provider and use-case assessment

Without an assessment profile, the tool concludes only on the general Microsoft 365 foundation. Provider/tier approval and use-case readiness remain **Not assessed**.

```powershell
python main.py `
  --assessment-profile examples/assessment-profile.example.json `
  --provider-evidence examples/provider-evidence.example.csv
```

- `--assessment-profile PATH` supplies proposed AI products, tiers, users, use cases, data boundaries, action capabilities, approvals, and measurements.
- `--provider-evidence PATH` supplies the product-and-tier review register. Evidence is stale after 90 days by default; set `PROVIDER_EVIDENCE_MAX_AGE_DAYS` to change that threshold.
- `--baseline PATH` compares the current run with a prior workbook or snapshot using stable finding fingerprints.
- `--snapshot-json PATH` writes an optional automation snapshot. It does not add a default output file.

Templates are available in [examples/assessment-profile.example.json](examples/assessment-profile.example.json) and [examples/provider-evidence.example.csv](examples/provider-evidence.example.csv).

## Next Steps

[Run Automated Readiness Assessment](RUN.md)

## Additional Resources

- [Microsoft 365 Copilot Overview](https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-overview)
- [Copilot Adoption Framework](https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-adoption)
- [Data, Privacy, and Security for Copilot](https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-privacy)
- [Microsoft 365 Apps Admin Center](https://learn.microsoft.com/microsoft-365-apps/admin-center/overview)
- [Microsoft Entra (Identity)](https://learn.microsoft.com/entra/identity/)
- [Microsoft Defender for Endpoint](https://learn.microsoft.com/microsoft-365/security/defender-endpoint/)
- [Microsoft Purview (Compliance & Information Protection)](https://learn.microsoft.com/purview/purview)
- [Power Platform Admin Center](https://learn.microsoft.com/power-platform/admin/admin-documentation)
- [Microsoft Copilot Studio](https://learn.microsoft.com/microsoft-copilot-studio/)
