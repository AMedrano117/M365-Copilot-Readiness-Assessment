"""Customer-facing AI adoption guidance, kept as data.

This content explains how to get started with Microsoft 365 Copilot and AI,
which security baseline to reach first, and how to guide everyday use. It is
advisory: guidance items never change control results, actions or the
readiness decision. Owner-review items can only be recorded as reviewed by the
responsible owner; they cannot pass a control.
"""

CONTENT_VERSION = "1.0"

ROADMAP_PHASES = (
    {
        "id": "days_0_30",
        "title": "Days 0-30: Secure the foundation and prepare the pilot",
        "goal": "Close the security gates that apply to the pilot population and agree how the pilot will be run.",
        "standard_steps": (
            "Name an executive sponsor, a business owner for each pilot use case, and an adoption lead.",
            "Choose a bounded pilot group (typically 20-50 people across two or three roles) with clear, repeatable tasks.",
            "Complete every open security gate below for the pilot users' identities, devices and content.",
            "Publish an acceptable-use statement for AI and brief pilot users on it before licenses are assigned.",
            "Record a usage and outcome baseline so the pilot can be measured.",
        ),
    },
    {
        "id": "days_31_60",
        "title": "Days 31-60: Run a controlled pilot",
        "goal": "Assign licenses to the pilot group, train users on real scenarios, and watch for data exposure and adoption signals.",
        "standard_steps": (
            "Assign Copilot licenses to the pilot group only, and confirm app prerequisites on their devices.",
            "Run role-based training with prompts drawn from the agreed use cases; set up office hours and a champions channel.",
            "Review sensitive-content exposure weekly (sharing links, broad permissions, DLP alerts) and act on findings.",
            "Collect structured feedback every two weeks: time saved, quality, blockers and any surprising results.",
            "Keep an issue log for anything Copilot surfaced that the user should not have been able to see.",
        ),
    },
    {
        "id": "days_61_90",
        "title": "Days 61-90: Measure, decide and expand",
        "goal": "Compare outcomes with the baseline, decide whether and where to expand, and extend governance to agents and external AI.",
        "standard_steps": (
            "Compare pilot results with the baseline and the agreed success measures; stop, adjust or expand per use case.",
            "Close or formally accept the remaining 'before broad rollout' actions.",
            "Decide the next population and the controls it needs (for example additional sensitivity labels or DLP scope).",
            "Review agents, connectors and external AI services against the owner checklist below before enabling them more widely.",
            "Rerun this assessment to record progress and refresh evidence dates.",
        ),
    },
)

# One expectation per readiness question; owners match the assessment domains.
BASELINE_CONTROLS = {
    "IDENTITY.AUTH": {"expectation": "Conditional Access requires MFA and blocks legacy authentication for all pilot users.",
                      "owner": "Identity and access administrator",
                      "link": "https://learn.microsoft.com/entra/identity/conditional-access/plan-conditional-access"},
    "IDENTITY.MFA": {"expectation": "Every pilot user has a phishing-resistant or strong MFA method registered.",
                     "owner": "Identity and access administrator",
                     "link": "https://learn.microsoft.com/entra/identity/authentication/overview-authentication"},
    "IDENTITY.ADMIN": {"expectation": "Privileged roles have reviewed purpose, scope, duration and documented exceptions; eligibility and activity remain distinct.",
                       "owner": "Identity and access administrator",
                       "link": "https://learn.microsoft.com/entra/id-governance/privileged-identity-management/pim-configure"},
    "CONTENT.SHARING": {"expectation": "Default sharing links are 'Specific people'; Anyone links expire or are disabled.",
                        "owner": "SharePoint administrator",
                        "link": "https://learn.microsoft.com/sharepoint/turn-external-sharing-on-or-off"},
    "CONTENT.PERMISSIONS": {"expectation": "Sites and files shared with 'Everyone' or large groups have been reviewed for the pilot's content.",
                            "owner": "SharePoint administrator and content owners",
                            "link": "https://learn.microsoft.com/sharepoint/data-access-governance-reports"},
    "CONTENT.OWNERSHIP": {"expectation": "Sites have accountable owners; inactive and ownerless sites have a decision.",
                          "owner": "SharePoint administrator and content owners",
                          "link": "https://learn.microsoft.com/sharepoint/site-lifecycle-management"},
    "DATA.LABELS": {"expectation": "A small sensitivity label taxonomy (for example Public, General, Confidential, Highly Confidential) exists.",
                    "owner": "Information protection and compliance owner",
                    "link": "https://learn.microsoft.com/purview/create-sensitivity-labels"},
    "DATA.PUBLISHING": {"expectation": "Labels are published to pilot users with a default label for new content.",
                        "owner": "Information protection and compliance owner",
                        "link": "https://learn.microsoft.com/purview/create-sensitivity-labels#publish-sensitivity-labels-by-creating-a-label-policy"},
    "DATA.DLP": {"expectation": "DLP covers the pilot's sensitive information, including the Microsoft 365 Copilot location where licensed.",
                 "owner": "Information protection and compliance owner",
                 "link": "https://learn.microsoft.com/purview/dlp-microsoft365-copilot-location-learn-about"},
    "DATA.AUDIT": {"expectation": "Unified audit logging is on so Copilot interactions can be investigated.",
                   "owner": "Information protection and compliance owner",
                   "link": "https://learn.microsoft.com/purview/audit-copilot"},
    "DATA.RETENTION": {"expectation": "Retention requirements for Copilot interactions and business content are decided.",
                       "owner": "Information protection and compliance owner",
                       "link": "https://learn.microsoft.com/purview/retention-policies-copilot"},
    "DATA.EXPOSURE": {"expectation": "You know where sensitive data sits and who can reach it before Copilot can summarize it.",
                      "owner": "Information protection and compliance owner",
                      "link": "https://learn.microsoft.com/purview/dspm-for-ai"},
    "APPS.CONSENT": {"expectation": "Users cannot consent to risky applications; existing broad grants are reviewed.",
                     "owner": "Application and security administrators",
                     "link": "https://learn.microsoft.com/entra/identity/enterprise-apps/configure-user-consent"},
    "APPS.CONNECTIONS": {"expectation": "Copilot connectors index only content their audience should see.",
                         "owner": "Application and security administrators",
                         "link": "https://learn.microsoft.com/microsoft-365/copilot/connectors/overview"},
    "ENDPOINT.POSTURE": {"expectation": "Pilot devices are managed, compliant and protected by Defender.",
                         "owner": "Endpoint and security operations owners",
                         "link": "https://learn.microsoft.com/intune/device-security/compliance/overview"},
    "THREAT.INCIDENTS": {"expectation": "No unresolved high-severity incidents affect pilot users or devices.",
                         "owner": "Endpoint and security operations owners",
                         "link": "https://learn.microsoft.com/defender-xdr/incidents-overview"},
    "LICENSE.ASSIGNMENT": {"expectation": "Copilot licenses are assigned to the pilot group through groups, not ad hoc.",
                           "owner": "Microsoft 365 administrator",
                           "link": "https://learn.microsoft.com/microsoft-365/copilot/microsoft-365-copilot-enable-users"},
    "LICENSE.APPS": {"expectation": "Pilot users run supported Microsoft 365 Apps versions and update channels.",
                     "owner": "Microsoft 365 administrator",
                     "link": "https://learn.microsoft.com/microsoft-365/copilot/microsoft-365-copilot-requirements"},
    "ADOPTION.BASELINE": {"expectation": "Pilot population, usage baseline and success measures are agreed and dated.",
                          "owner": "Business sponsor and adoption lead",
                          "link": "https://adoption.microsoft.com/copilot/"},
}

ADOPTION_CHECKLIST = (
    {"id": "acceptable_use", "phase": "days_0_30", "owner": "Business sponsor with legal and HR",
     "topic": "Acceptable-use policy for AI",
     "guidance": "State what AI may be used for, which data must not be entered into non-approved tools, that people remain accountable for outputs, and how to report problems."},
    {"id": "use_cases", "phase": "days_0_30", "owner": "Business sponsor and adoption lead",
     "topic": "Prioritized use cases",
     "guidance": "Pick three to five tasks with measurable time or quality impact and low data risk (for example meeting recaps, first drafts, summarizing long threads)."},
    {"id": "champions", "phase": "days_0_30", "owner": "Adoption lead",
     "topic": "Champions network",
     "guidance": "Recruit one champion per team in the pilot to share prompts, collect feedback and answer first-line questions."},
    {"id": "training", "phase": "days_31_60", "owner": "Adoption lead",
     "topic": "Role-based training",
     "guidance": "Train with real scenarios per role, including how to verify outputs, cite sources and recognize when not to use AI."},
    {"id": "communications", "phase": "days_31_60", "owner": "Adoption lead and internal communications",
     "topic": "Communications plan",
     "guidance": "Explain why the organization is adopting AI, what is in scope, where to get help and how data is protected."},
    {"id": "feedback", "phase": "days_31_60", "owner": "Adoption lead",
     "topic": "Feedback and issue reporting",
     "guidance": "Give users one place to report surprising results, overshared content or harmful outputs, and review it weekly."},
    {"id": "success_measures", "phase": "days_61_90", "owner": "Business sponsor",
     "topic": "Success measures and expansion criteria",
     "guidance": "Measure active use, time saved, quality and user sentiment against the baseline; agree stop, adjust and expand thresholds per use case."},
    {"id": "agents", "phase": "days_61_90", "owner": "Agent platform owner",
     "topic": "Agent and extension governance",
     "guidance": "Decide who may create or share agents, which knowledge sources and actions are allowed, and how agents are reviewed and retired."},
)

# External and third-party AI services: owner reviews, never automatic passes.
CROSS_PLATFORM_MANUAL_CHECKS = [
    {
        "control": "Approved AI service inventory",
        "why": "Identify sanctioned and unsanctioned AI services, owners, user populations, and data flows.",
        "verify": "Review SaaS discovery, browser/network telemetry, procurement records, and employee use cases.",
    },
    {
        "control": "Enterprise workspace and identity controls",
        "why": "Personal AI accounts bypass centralized access, offboarding, role, and sharing controls.",
        "verify": "For each provider, validate enterprise terms, domain controls, SSO, provisioning/deprovisioning, MFA, and admin RBAC.",
    },
    {
        "control": "Provider data handling",
        "why": "Training use, retention, residency, subprocessors, and deletion behavior differ by product and contract.",
        "verify": "Record the contracted settings for prompts, files, outputs, logs, abuse monitoring, retention, residency, and model training.",
    },
    {
        "control": "Sensitive-data egress controls",
        "why": "Users can paste or upload M365 data to browser, desktop, IDE, extension, API, or agent experiences.",
        "verify": "Validate endpoint/browser DLP or equivalent controls on managed devices and test representative upload and paste scenarios.",
    },
    {
        "control": "Connectors, tools, actions, and agents",
        "why": "Connected AI can read or change business data with the user's or agent's permissions.",
        "verify": "Inventory connectors and agent identities; require least privilege, trusted publishers, bounded actions, and human approval for consequential writes.",
    },
    {
        "control": "Use-case and data policy",
        "why": "A technical control cannot decide which regulated, confidential, or safety-sensitive use cases are acceptable.",
        "verify": "Map approved use cases to data classifications, prohibited uses, human-review requirements, and accountable business owners.",
    },
    {
        "control": "Audit, response, and legal readiness",
        "why": "AI activity must fit existing investigation, records, privacy, and incident-response processes.",
        "verify": "Confirm provider logs, retention, eDiscovery/export, alerting, incident playbooks, and evidence ownership.",
    },
    {
        "control": "Outcome measurement",
        "why": "Usage volume alone does not show that AI improves quality, cycle time, risk, or employee experience.",
        "verify": "Define pilot baselines, success measures, quality checks, risk thresholds, and stop/expand decisions by use case.",
    },
]
