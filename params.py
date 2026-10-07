# Deliberately blank. The tenant must come from --tenant-id or the selected
# environment file's TENANT_ID so a run can never fall back to another
# customer's tenant.
TENANT_ID = ""

# Services to analyze - valid values: "M365", "Entra", "Defender", "Purview", "Power Platform", "Copilot Studio"
# Empty array = analyze all services
# Note: "Defender" includes Copilot-specific security recommendations (Security Posture, Threat Intelligence, Data Governance)
#       Copilot Data Governance recommendation will also use Purview data if available (run with: .\collect_purview_data.ps1)
SERVICES = []  # e.g., ["M365", "Entra"], ["Defender", "Purview"], or [] for all
