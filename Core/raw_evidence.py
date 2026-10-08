"""Restricted raw source inventory and lossless worksheet continuation ranges."""

from copy import deepcopy
import re

from .investigation_details import _ref


MAX_DATA_ROWS = 1_048_575
KEY_SOURCES = {
 'authentication_detail':['auth_methods'], 'authentication_methods_detail':['auth_methods'],
 'authentication_preferences_detail':['auth_methods'], 'mfa_registration_detail':['auth_methods'],
 'conditional_access_detail':['ca_policies','authentication_strengths','ca_group_members'],
 'legacy_signin_detail':['signin_logs'], 'entra_device_detail':['managed_devices','compliance_setting_states','windows_protection'],
 'defender_device_detail':['machines','antivirus_health','directory_devices'],
 'defender_incident_detail':['incidents','alerts'], 'app_access_detail':['service_principals','oauth_grants','application_permissions','application_owners'],
 'app_consent_policy_detail':['authorization_policy','consent_policies'],
 'purview_policy_detail':['sensitivity_labels','label_policies','dlp_policies','dlp_rules','retention_policies','audit_config','irm_config'],
 'external_connection_detail':['external_connections'], 'data_exposure_detail':['content_exposure','sensitive_exposure','label_coverage'],
 'ai_usage_detail':['copilot_audit','shadow_ai','users','licenses','copilot_usage'],
 'copilot_readiness_detail':['copilot_readiness'], 'copilot_user_usage_detail':['copilot_usage'],
 'm365_activity_detail':['email_activity','teams_activity','sharepoint_usage','onedrive_usage','office_activations','active_users'],
 'service_plan_inventory':['licenses','license_users'],
}


def safe_record(value):
    """Do not copy authentication secrets or prompt/response bodies into detail."""
    blocked = {'password','passwordprofile','passwordcredentials','keycredentials','clientsecret','accesstoken',
               'refreshtoken','idtoken','authorization','privatekey','prompt','prompttext','response','responsetext','auditdata',
               'secrettext','secret','token','credentials','clientassertion','cookie','setcookie','privatekeydata','promptcontent','responsecontent'}
    if isinstance(value, dict):
        return {key:safe_record(item) for key,item in value.items() if re.sub('[^a-z0-9]','',str(key).lower()) not in blocked}
    if isinstance(value, list): return [safe_record(item) for item in value]
    return value


def prepare_raw_details(bundle, result):
    sheets = bundle.setdefault('sheets', {})
    for key in list(sheets):
        if key.startswith('raw_source.'):
            del sheets[key]
    used = {sheet.get('title','').casefold() for key,sheet in sheets.items() if not key.startswith('raw_source.')}
    from .workbook_evidence import RESERVED_TITLES
    used.update(title.casefold() for title in RESERVED_TITLES)
    ranges = {}
    from .evidence_selection import evidence_record_ids
    identifiers = evidence_record_ids(bundle.get('assessment_sources', {}),
                                      result.get('tenant_id') or bundle.get('expected_tenant_id'))
    for name, datasets in bundle.get('assessment_sources', {}).items():
        title = ('Raw ' + name.replace('_',' ').title())[:31]
        original, suffix = title, 2
        while title.casefold() in used:
            ending = f' {suffix}'; title=original[:31-len(ending)]+ending; suffix+=1
        used.add(title.casefold())
        records = []
        for dataset_index, dataset in enumerate(datasets):
            source = safe_record(dataset.get('source', {}))
            for position, record in enumerate(dataset.get('records', []),1):
                record = safe_record(record)
                if not isinstance(record, dict):
                    record = {'value':record}
                # Put the actual log fields first. Full nested records and source
                # metadata remain available at the right of the worksheet. The
                # evidence ID matches the technical workbook and HTML evidence pages.
                records.append({**record, 'Evidence Record ID':identifiers.get((name, dataset_index, position-1), ''),
                                'Source dataset':name, 'Source record':position,
                                'Source metadata':source, 'Raw record':record})
        if not records: continue
        from .export_recommendations import _excel_safe_rows
        records = _excel_safe_rows(records,title)
        sheets['raw_source.'+name] = {'title':title,'rows':records,'restricted':True,'preview_columns':[],
            'summary':'Retained source records. Inventory scope is distinct from the selected affected population.', 'details':[]}
        width = len(dict.fromkeys(key for row in records for key in row))
        ranges[name] = _ref(title,2,len(records)+1,width)
    for finding in result.get('recommendations', []):
        names = [name for key in str(finding.get('EvidenceKey') or '').split(';')
                 for name in KEY_SOURCES.get(key.strip(), []) if name in ranges]
        # Shared Purview inventories are not interchangeable evidence. A label
        # observation must not link to every DLP and retention record as well.
        if 'purview_policy_detail' in str(finding.get('EvidenceKey') or ''):
            text = ' '.join(str(finding.get(key) or '') for key in
                            ('ControlId','FindingKey','Feature','Observation','EvidenceSource')).lower()
            control_sources = {'DATA.AUDIT':['audit_config','copilot_audit'],
                               'DATA.RETENTION':['retention_policies'],
                               'DATA.DLP':['dlp_policies','dlp_rules'],
                               'DATA.PUBLISHING':['sensitivity_labels','label_policies']}
            selected = control_sources.get(finding.get('ControlId')) or (['audit_config','copilot_audit'] if 'audit' in text else
                        ['retention_policies'] if 'retention' in text else
                        ['dlp_policies','dlp_rules'] if 'dlp' in text or 'data loss prevention' in text else
                        ['sensitivity_labels','label_policies'] if 'label' in text or 'classification' in text else
                        ['irm_config'] if 'rights management' in text or 'azure-rms' in text else names)
            names = [name for name in selected if name in ranges]
        locations = list(dict.fromkeys(ranges[name] for name in names))
        finding['RawEvidenceRanges'] = list(dict.fromkeys(locations))
        finding['RawEvidenceQualification'] = ('These ranges retain the source inventories. The investigation range identifies selected affected records when that selection can be reproduced.' if locations else 'Raw source rows were not retained for this finding; consult its investigation qualification.')
        if locations and finding.get('Disposition') in {'Assurance','Reference'} and not finding.get('InvestigationEvidence') and finding.get('Historical') != 'Yes':
            # Point at the retained inventory once, rather than making a new
            # "Evidence <finding>" copy for every licensing or assurance row.
            finding['_shared_inventory_support'] = {
                'InvestigationRange':locations[0], 'InvestigationRanges':locations,
                'InvestigationCount':sum(len(sheets['raw_source.'+name]['rows']) for name in dict.fromkeys(names)),
                'InvestigationStatus':'Records available' if finding.get('EvidenceLevel')=='observed_operation' else 'Configuration evidence',
                'InvestigationSummary':'Shared source inventory',
                'InvestigationQualification':'Shared retained inventory supporting this observation. These are supporting records, not a selected affected population or proof of effective operation.',
                'InvestigationPublicQualification':'Supporting inventory; effective operation requires separate confirmation.',
            }
        else:
            finding.pop('_shared_inventory_support', None)
    return ranges


def split_evidence_sheets(bundle, result, max_rows=MAX_DATA_ROWS):
    """Split before Excel export and remap every retained reference, including worklists."""
    if type(max_rows) is not int or max_rows < 1:
        raise ValueError('Worksheet row capacity must be a positive integer.')
    sheets = bundle.setdefault('sheets', {})
    splits = {}
    bundle['worksheet_row_capacity'] = max_rows
    used = {sheet.get('title','').casefold() for sheet in sheets.values()}
    for key, sheet in list(sheets.items()):
        data = sheet.get('rows') or []
        if len(data) <= max_rows: continue
        title = sheet['title']
        parts = []
        # Each part has identical columns so a remapped range stays valid.
        fields = list(dict.fromkeys(field for row in data for field in row))
        for offset in range(0,len(data),max_rows):
            number = offset//max_rows+1
            part_title = title if number==1 else title[:31-len(f' {number}')]+f' {number}'
            suffix = number
            while number>1 and part_title.casefold() in used:
                suffix += 1
                ending = f' {suffix}'
                part_title=title[:31-len(ending)]+ending
            used.add(part_title.casefold())
            part = dict(sheet, title=part_title, rows=[{field:row.get(field,'') for field in fields} for row in data[offset:offset+max_rows]])
            sheets[key if number==1 else key+f'.part{number}']=part
            parts.append((part_title,offset,min(offset+max_rows,len(data))))
        splits[title]=parts
    pattern = re.compile(r"^'((?:[^']|'')+)'!([A-Z]+)(\d+):([A-Z]+)(\d+)$")
    def remap(value):
        match=pattern.fullmatch(str(value))
        if not match or match[1].replace("''", "'") not in splits: return [value]
        first,last=int(match[3])-2,int(match[5])-2
        return ["'"+title.replace("'","''")+f"'!{match[2]}{max(first,start)-start+2}:{match[4]}{min(last,end-1)-start+2}"
                for title,start,end in splits[match[1].replace("''", "'")] if first < end and last >= start]
    visited=set()
    def visit(value):
        if not isinstance(value,(dict,list)) or id(value) in visited: return
        visited.add(id(value))
        if isinstance(value,list):
            for item in value: visit(item)
            return
        for key,item in list(value.items()):
            if key in {'reconciliation','identity','identity_validation','SourceOccurrences','FindingMembership','evidence'}:
                continue  # Semantic declarations and original locators are immutable.
            if key=='rows':
                for row in item: visit(row)
            elif isinstance(item,str) and pattern.fullmatch(item):
                locations=remap(item)
                value[key]=locations[0]
                if len(locations)>1: value[key+'s']=locations
            elif key in {'RawEvidenceRanges','InvestigationRanges','EvidenceRecordsRanges'} and isinstance(item,list):
                value[key]=[location for original in item for location in remap(original)]
            elif key not in {'InvestigationEvidence','assessment_sources'}: visit(item)
    if splits:
        visit(result); visit(sheets)
    return splits
