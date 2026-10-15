"""Contextual portal guidance; evidence/billing distinctions reviewed 2026-10-12."""


GUIDES = {
    'sharepoint': {
        'title': 'SharePoint permission and sharing reports',
        'owner': 'SharePoint administrator and content owners.',
        'prerequisites': (
            'Confirm a supported base subscription and SharePoint Advanced Management entitlement. '
            'Microsoft documents qualifying Copilot, SAM Plan 1 and E7 routes. E5-only access '
            'provides limited activity reports, without permission snapshots. If activity '
            'collection needs enabling, allow 24 hours; history starts from enablement.'
        ),
        'steps': [
            'Open SharePoint admin center > Reports > Data access governance. Check completed '
            'reports, dates and filters first. Standard live collection downloads existing completed '
            'results automatically. Check download outcomes and scope; if a download failed, retry '
            'collection or manually export that result. Let running reports finish without duplicating scans. '
            'Restricted collection uses owner-provided exports.',
            'Permission baseline: open Site permissions across your organization > View reports. '
            'Download the SharePoint and OneDrive CSVs separately. Compare Site IDs and filters '
            'with earlier exports; ask owners to confirm omitted sites. If a new baseline is '
            'needed, select Create report or Run reports. The first run can take five days, '
            'later runs 24 hours; reruns are limited to every 30 days.',
            'Sharing activity: open Sharing links. Download completed CSVs for Anyone links, '
            'People in the organization links, and Specific people links shared externally. '
            'Run only the missing or outdated reports; allow up to 24 hours. The portal covers '
            'SharePoint; use Microsoft\'s documented PowerShell path for OneDrive.',
            'EEEU activity: under Activity reports, open Shared with \'Everyone except external '
            'users\' > View reports. Review applicable site and item scenarios and filters. '
            'Use Download detailed report for completed CSVs; new reports can take 24 hours. '
            'OneDrive activity uses PowerShell and supports item-level reporting only.',
            'Item details, when required: under Snapshot reports, open Sites and files shared '
            'via special SharePoint groups > View reports. Reuse each relevant group\'s '
            'completed report and download its ZIP containing CSV. This needs an existing '
            'organization baseline and the SharePoint Advanced Management Administrator role, '
            'assigned by a Global Administrator. Reports cover both workloads together and '
            'permit reruns every 30 days.',
        ],
        'completion': (
            'Provide original exports, report dates, workloads, filters and completion status. '
            'Record archived/NoAccess exclusions and owner confirmation for older-only sites. '
            'An omitted site or empty export does not by itself prove that exposure was removed.'
        ),
        'sources': [
            ('SAM prerequisites', 'https://learn.microsoft.com/en-us/sharepoint/sharepoint-advanced-management-prerequisites'),
            ('DAG access and limits', 'https://learn.microsoft.com/en-us/sharepoint/data-access-governance-reports'),
            ('Permission baseline', 'https://learn.microsoft.com/en-us/sharepoint/data-access-governance-site-permissions-report'),
            ('Sharing activity', 'https://learn.microsoft.com/en-us/sharepoint/data-access-governance-sharing-links-report'),
            ('EEEU activity', 'https://learn.microsoft.com/en-us/sharepoint/data-access-governance-everyone-except-external-user-report'),
            ('Everyone/EEEU item details', 'https://learn.microsoft.com/en-us/sharepoint/data-access-governance-detailed-eeeu-everyone-permissions-report'),
            ('OneDrive and PowerShell reporting', 'https://learn.microsoft.com/en-us/sharepoint/powershell-for-data-access-governance'),
        ],
    },
    'dspm': {
        'title': 'a Purview sensitive-data risk assessment',
        'owner': 'Information protection and compliance owner.',
        'prerequisites': (
            'A Compliance Administrator can create assessments. Purview Security Reader or '
            'Data Security Viewer can review results. File details additionally require '
            'Content Explorer List Viewer/Content Viewer access. Confirm the required Purview '
            'licenses for the capabilities and users in scope. Reusing existing results does not '
            'require activating PAYG; verify entitlement and billing before additional scans or protections.'
        ),
        'steps': [
            'Open Microsoft Purview > Solutions > DSPM > Discover > Data risk assessments > '
            'Microsoft 365. Check existing completed default and custom assessments before '
            'starting another scan. Confirm that the date and selected sites cover the pilot.',
            'Open the applicable completed assessment and select Export. Prefer the original '
            'CSV with its headers intact; Microsoft also offers Excel, JSON and TSV. Retain '
            'the assessment date, scope and completion status alongside the export.',
            'If no suitable completed result exists, the compliance administrator can select '
            'Create custom assessment after agreeing the missing scope and confirming entitlement. '
            'The weekly default covers the top 100 SharePoint sites by usage; '
            'its first results can take four days. Custom assessment '
            'results take at least 48 hours. Wait for Completed before exporting.',
            'Additional on-demand classification is separate from an existing data risk assessment '
            'and can be metered. Confirm applicable per-user or PAYG entitlement, setup, '
            'estimate and Usage center before initiating it. Record its actual supported '
            'locations and coverage; the assessment tool does not initiate scans.',
        ],
        'completion': (
            'Provide the original export, assessment date, selected users/sites/workloads, '
            'scan level and completion status. Record missing roles, entitlement or excluded '
            'scope. A top-100-site assessment does not establish tenant-wide coverage.'
        ),
        'sources': [
            ('Data risk assessment instructions', 'https://learn.microsoft.com/en-us/purview/data-security-posture-management-oversharing'),
            ('Billing models', 'https://learn.microsoft.com/en-us/purview/purview-billing-models'),
            ('On-demand classification', 'https://learn.microsoft.com/en-us/purview/on-demand-classification'),
            ('DSPM permissions', 'https://learn.microsoft.com/en-us/purview/data-security-posture-management-permissions'),
            ('Purview licensing', 'https://learn.microsoft.com/en-us/office365/servicedescriptions/microsoft-365-service-descriptions/microsoft-365-tenantlevel-services-licensing-guidance/microsoft-purview-service-description'),
        ],
    },
}


_GUIDE_KEYS = {
    'sharepoint.dag.not_assessed': 'sharepoint',
    'data_exposure.freshness.sam': 'sharepoint',
    'data_exposure.snapshot_scope': 'sharepoint',
    'data_exposure.coverage.sensitive-data_exposure_evidence': 'dspm',
    'coverage.data.exposure': 'dspm',
}


def guide_for(row):
    """Return guidance only for an explicitly supported evidence finding."""
    if not isinstance(row, dict):
        return ''
    return _GUIDE_KEYS.get(str(row.get('FindingKey') or '').strip().lower(), '')
