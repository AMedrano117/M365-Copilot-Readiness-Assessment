"""Run fictional Content & Sharing scenarios through production functions only.

The harness follows the order used by Core/processor.py: SharePoint sharing
findings, exported exposure reports, DAG reconciliation, governance outcomes,
then the shared assessment result. It implements no assessment semantics.
All tenants, sites and values are invented.
"""
import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace

from Core.assessment_result import build_assessment_result
from Core.data_exposure_assessment import build_data_exposure_assessment
from Core.evidence_layer import build_evidence_bundle
from Core.sharepoint_governance import (build_sharepoint_recommendations, merge_sharepoint_payloads,
                                        reconcile_dag_coverage, summarize_sharepoint_governance)
from Core.sharepoint_governance_evidence import assess_sharepoint_governance

TENANT = '00000000-0000-4000-8000-00000000c0de'
EVALUATION = '2026-10-20'
COLLECTED = '2026-10-18T09:00:00+00:00'
CURRENT_REPORT = '2026-10-15T00:00:00Z'
OLD_REPORT = '2026-08-20T00:00:00Z'
SITE_A = 'https://fictional.example/sites/alpha'
SITE_B = 'https://fictional.example/sites/beta'
PERSONAL = 'https://fictional-my.example/personal/user_fictional_example'

SPO_STATE = {'available': True, 'availability_status': 'available', 'records_collected': 1, 'pages_collected': 1,
             'truncated': False, 'reason': '', 'coverage': 'full'}
GRAPH_STATE = dict(SPO_STATE, coverage='partial', source='Microsoft Graph SharePoint tenant settings')
COMPLIANT = {'SharingCapability': 'ExternalUserSharingOnly', 'OneDriveSharingCapability': 'ExternalUserSharingOnly',
             'DefaultSharingLinkType': 'Direct', 'RequireAnonymousLinksExpireInDays': 0,
             'FileAnonymousLinkType': 'View', 'FolderAnonymousLinkType': 'View',
             'ExternalUserExpirationRequired': True, 'ExternalUserExpireInDays': 60,
             'PreventExternalUsersFromResharing': True, 'LegacyAuthProtocolsEnabled': False}

SNAPSHOT_HEADER = ('Tenant ID,Site ID,Site name,Site URL,Site template,Number of users having access,Guest user permissions,'
                   'External participant permissionst,PeopleInYourOrg link count,Anyone link count,EEEU permission count,'
                   'Everyone permission count,Report date')
DSPM_HEADER = 'Tenant ID,Site URL,Workload,Potentially overshared items,Unlabeled sensitive item count,Sensitive item count,Report date'


def spo(settings, dag=None):
    return {'source': 'SharePoint Online Management Shell', 'available': True, 'availability_status': 'available',
            'tenant': {'available': True, 'settings': copy.deepcopy(settings), 'coverage': 'full'},
            'sites': {'available': False, 'items': []},
            'dag_reports': {'available': dag is not None, 'reports': copy.deepcopy(dag or [])},
            'dag_activity_data': {'available': False, 'items': []},
            'collection_status': {'sharepoint_tenant_settings': dict(SPO_STATE)}}


def graph(settings):
    return {'source': 'Microsoft Graph SharePoint tenant settings', 'available': True, 'availability_status': 'partial',
            'tenant': {'available': True, 'settings': copy.deepcopy(settings), 'coverage': 'partial', 'unavailable_fields': []},
            'sites': {'available': False, 'items': []}, 'dag_reports': {'available': False, 'reports': []},
            'dag_activity_data': {'available': False, 'items': []},
            'collection_status': {'sharepoint_tenant_settings_graph': dict(GRAPH_STATE)}}


def conflicting():
    return merge_sharepoint_payloads(graph({'SharingCapability': 'Disabled'}), spo(COMPLIANT))


def snapshot_row(site_id, url, *, users=10, guests=0, external=0, org=0, anyone=0, eeeu=0, everyone=0, date=CURRENT_REPORT,
                 template='Team site'):
    return ','.join(str(value) for value in (TENANT, site_id, 'Fictional ' + site_id, url, template, users, guests, external,
                                             org, anyone, eeeu, everyone, date))


def snapshot(*rows):
    return SNAPSHOT_HEADER + '\n' + '\n'.join(rows) + '\n'


def zero_snapshot(date=CURRENT_REPORT):
    return snapshot(snapshot_row('site-alpha', SITE_A, date=date),
                    snapshot_row('site-personal', PERSONAL, template='Personal site', date=date))


def dspm(*rows):
    return DSPM_HEADER + '\n' + '\n'.join(rows) + '\n'


def dspm_row(url, workload, *, overshared=0, unlabeled=0, sensitive=0, date=CURRENT_REPORT):
    return ','.join(str(value) for value in (TENANT, url, workload, overshared, unlabeled, sensitive, date))


def run(*, payload=None, sam=(), dspm_files=(), collected=COLLECTED, evaluation=EVALUATION):
    """Production order. ``sam`` and ``dspm_files`` are (file name, CSV text) pairs."""
    rows, statuses = [], {}
    if payload is not None:
        rows += build_sharepoint_recommendations(payload, evaluation_date=evaluation)
        statuses.update(payload.get('collection_status') or {})
    with tempfile.TemporaryDirectory(prefix='content-sharing-scenario-') as folder:
        def write(files):
            paths = []
            for name, text in files:
                path = Path(folder, name)
                path.write_text(text, encoding='utf-8')
                paths.append(str(path))
            return paths
        exposure = build_data_exposure_assessment(sam_report_paths=write(sam), dspm_report_paths=write(dspm_files),
                                                  enabled=True, evaluation_date=evaluation)
    rows = reconcile_dag_coverage(rows, ((exposure.get('sources') or {}).get('sam') or {}).get('reports'))
    governance = assess_sharepoint_governance(summarize_sharepoint_governance(payload or {}, evaluation_date=evaluation),
                                              exposure, {}, rows, evaluation_date=evaluation)
    exposure = dict(exposure, recommendations=list(exposure['recommendations']) + list(governance['recommendations']),
                    observations=list(exposure.get('observations') or []) + list(governance['observations']),
                    sharepoint_governance=governance)
    context = {'tenant_id': TENANT, 'collected_at': collected, 'source_file': 'fictional-collection.json',
               'evaluation_date': evaluation, 'mode': 'offline'}
    m365_client = SimpleNamespace(sharepoint_governance=payload) if payload is not None else None
    # build_evidence_bundle assigns display IDs, resolves detail sheets and builds the verified strengths.
    bundle = build_evidence_bundle(copy.deepcopy(rows + exposure['recommendations']), ({'_client': m365_client}, []),
                                   {}, {}, {}, {}, {}, exposure, collection_context=context)
    bundle.update(expected_tenant_id=TENANT, source_statuses=statuses, collection_context=context,
                  observations=exposure['observations'])
    result = build_assessment_result(bundle['recommendations'], bundle, evaluation_date=evaluation, expected_tenant_id=TENANT)
    return result, exposure


def control(result, control_id):
    return next(item for item in result['controls'] if item['control_id'] == control_id)


def rows_with_key(result, key):
    return [row for row in result['recommendations'] if row.get('FindingKey') == key]
