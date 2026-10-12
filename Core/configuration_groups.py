"""Shared, additive configuration presentation. Native/PFI identities are untouched.

Only retained SharePoint configuration observations are enrolled initially.
Members preserve every selected occurrence; counts use distinct scoped objects.
"""
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json

VERSION = '1.0.0'
DIMENSIONS = ('finding_id', 'configuration_key', 'configuration', 'observed', 'recommended', 'assessment',
              'object_type', 'environment', 'provider', 'scope', 'period', 'collection_state',
              'qualification', 'conflict_state', 'configuration_source')
POPULATION = ('finding_id', 'configuration_key', 'object_type', 'environment', 'provider', 'scope', 'period')


def attach_configuration_groups(result, bundle):
    """Narrow enrollment avoids another pass over unrelated large inventories."""
    if 'configuration_groups' in result:
        return result
    from .evidence_selection import build_evidence_selection
    rows = [r for r in result.get('recommendations') or [] if str(r.get('FindingKey') or '').startswith('sharepoint.')]
    sources = {k:v for k,v in (bundle.get('assessment_sources') or {}).items() if k in
               {'sharepoint_site_settings','sharepoint_tenant_settings','sharepoint_graph_settings'}}
    if rows and sources:
        narrowed = dict(result, recommendations=rows)
        result = dict(result, configuration_groups=build_configuration_groups(build_evidence_selection(narrowed, dict(bundle, assessment_sources=sources))))
    return result


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _key(row, dimensions):
    return _json({key: row.get(key) for key in dimensions})


def group_observations(observations):
    members = [deepcopy(row) for row in observations]
    populations, grouped = defaultdict(list), defaultdict(list)
    for row in members:
        row['qualification'] = sorted(set(row.get('qualification') or []))
        row['group_id'] = 'CFG-' + sha256(_key(row, DIMENSIONS).encode()).hexdigest()[:24]
        populations[_key(row, POPULATION)].append(row)
        grouped[row['group_id']].append(row)
    groups = []
    for identifier, rows in sorted(grouped.items()):
        first = rows[0]
        population = populations[_key(first, POPULATION)]
        objects = {r['object_id'] for r in rows if r.get('object_id')}
        assessed = {r['object_id'] for r in population if r.get('object_id')}
        states = defaultdict(set)
        for row in population:
            if row.get('object_id'):
                states[row['object_id']].add(_json(row.get('observed')))
        conflicting = {obj for obj, values in states.items() if len(values) > 1}
        conflicting.update(r['object_id'] for r in population if r.get('object_id') and r.get('conflict_state'))
        unknown = {r['object_id'] for r in population if r.get('object_id') and r.get('assessment') in {'Unknown', 'Not established', 'Not assessed'}}
        groups.append(dict({key: deepcopy(first.get(key)) for key in DIMENSIONS}, group_id=identifier,
                           affected_object_count=len(objects), total_objects_assessed=len(assessed),
                           unknown_object_count=len(unknown), conflicting_object_count=len(conflicting),
                           unidentified_record_count=sum(not r.get('object_id') for r in rows),
                           member_count=len(rows), evidence_count=len({i for r in rows for i in r.get('evidence_ids') or []}),
                           tenant_population_total=None,
                           population_qualification='Distinct returned objects assessed; tenant population coverage is not established.'))
    return {'version': VERSION, 'groups': groups, 'members': sorted(members, key=_json)}


def _value(row, *keys):
    names = {str(k).replace(' ', '').lower(): v for k, v in row.items()}
    return next((names[k.replace(' ', '').lower()] for k in keys if names.get(k.replace(' ', '').lower()) not in (None, '')), None)


def build_configuration_groups(payload):
    """Build from retained selection and source envelopes, never worksheet coordinates."""
    from .customer_report import SHARING_SETTING_LABELS, _sharing_setting_value, _sharing_check
    sources = {s['dataset_id']: s for s in payload.get('sources') or []}
    native = {r['record_id']: r for r in payload.get('evidence_records') or []}
    observations = []
    for finding in payload.get('findings') or []:
        key = finding.get('finding_key') or ''
        if not key.startswith('sharepoint.'):
            continue
        relevant = ({'DefaultSharingLinkType'} if key == 'sharepoint.sharing.organization_default' else
                    {'LegacyAuthProtocolsEnabled'} if key == 'sharepoint.authentication.legacy_permitted' else
                    {'SharingCapability', 'OneDriveSharingCapability', 'DefaultSharingLinkType', 'RequireAnonymousLinksExpireInDays',
                     'FileAnonymousLinkType', 'FolderAnonymousLinkType'} if key in
                    {'sharepoint.sharing.permissive_anonymous_defaults', 'sharepoint.sharing.anyone_enabled'} else set(SHARING_SETTING_LABELS))
        for detail in finding.get('records') or []:
            ids = detail.get('evidence_record_ids') or []
            # Each native occurrence is independently retained. Joined tenant
            # support is not mislabeled as a second site observation.
            for evidence_id in ids:
                record = native.get(evidence_id, {})
                source_entry = sources.get(record.get('dataset_id'), {})
                dataset = source_entry.get('dataset') or record.get('dataset')
                if dataset not in {'sharepoint_site_settings', 'sharepoint_tenant_settings', 'sharepoint_graph_settings'}:
                    continue
                raw = record.get('raw') or {}
                if not isinstance(raw, dict):
                    continue
                settings = raw.get('settings') if isinstance(raw.get('settings'), dict) else raw
                capture = source_entry.get('source') or {}
                site = dataset == 'sharepoint_site_settings'
                complete = capture.get('complete') is True and not capture.get('truncated') and capture.get('availability_status', 'available') == 'available'
                collection = 'complete' if complete else capture.get('availability_status') or 'unknown'
                if collection == 'available': collection = 'partial'
                verified = not site or (capture.get('settings_verified') is True or capture.get('single_site_query') is True or
                                       capture.get('settings_read_mode') == 'per_site_identity') and raw.get('SettingsReadStatus') != 'unavailable'
                values = {name: _value(settings, name) for name in SHARING_SETTING_LABELS if _value(settings, name) is not None}
                anyone = any(_sharing_setting_value(name, values[name]).startswith('Anyone links') for name in
                             ('SharingCapability', 'OneDriveSharingCapability') if name in values)
                for name in sorted(set(values) & relevant):
                    observed = _sharing_setting_value(name, values[name])
                    recommended, state = _sharing_check(name, observed, anyone)
                    qualifications = list(detail.get('qualifications') or [])
                    for field in ('qualification', 'limitations', 'partial_errors', 'errors'):
                        value = capture.get(field)
                        if value:
                            qualifications.extend(str(v) for v in value) if isinstance(value,list) else qualifications.append(str(value))
                    if not verified:
                        qualifications.append('Unverified site inventory/list defaults; effective configuration requires a targeted read or dated review.')
                    origin = _value(settings, name + 'Source', 'ConfigurationSource', 'SettingSource') or 'unknown'
                    # An expiry override flag does not establish sharing/default-link inheritance.
                    if name in {'AnonymousLinkExpirationInDays', 'RequireAnonymousLinksExpireInDays'}:
                        flag = settings.get('OverrideTenantAnonymousLinkExpirationPolicy')
                        if type(flag) is bool: origin = 'explicit site override' if flag else 'inherited tenant expiry'
                    obj_type = _value(raw, 'ObjectType', 'SiteType') or ('OneDrive account' if name == 'OneDriveSharingCapability' or
                               raw.get('IsPersonalSite') is True or '/personal/' in str(_value(raw, 'Url', 'siteUrl') or '') else
                               'Channel site' if raw.get('IsChannelSite') is True else 'Teams-connected site' if raw.get('IsTeamsConnected') is True else 'SharePoint site')
                    scope = 'site' if site else 'tenant'
                    object_id = _value(raw, 'Id', 'SiteId', 'Site ID', 'Url', 'siteUrl') if site else payload.get('tenant_id')
                    observations.append(dict(finding_id=finding['finding_id'], configuration_key=name,
                        configuration=SHARING_SETTING_LABELS[name], observed=observed, raw_observed=deepcopy(values[name]),
                        recommended=recommended, assessment='Conflict' if detail.get('conflicts') or capture.get('conflicting') is True else 'Review' if not verified or collection != 'complete' else
                        {'change': 'Change', 'review': 'Review', 'ok': 'Meets'}[state], object_type=obj_type,
                        object_id=str(object_id).casefold().rstrip('/') if object_id is not None else None,
                        object_name=_value(raw, 'Title', 'displayName'), locator=_value(raw, 'Url', 'siteUrl'), scope=scope,
                        environment=capture.get('environment') or capture.get('cloud') or 'not established',
                        provider=capture.get('provider') or ('Microsoft Graph' if dataset == 'sharepoint_graph_settings' else 'SharePoint'),
                        period={'capture':capture.get('collected_at'), 'window':capture.get('collection_window'),
                                'historical':finding.get('historical')}, collection_state=collection, qualification=qualifications,
                        conflict_state=bool(detail.get('conflicts')) or capture.get('conflicting') is True, configuration_source=origin,
                        evidence_ids=list(ids), primary_evidence_id=evidence_id, detail_record_id=detail.get('record_id'),
                        source_refs=deepcopy(detail.get('source_refs') or []), source_capture=deepcopy(capture), dataset=dataset,
                        source_record_index=record.get('source_record_index'), source_record_id=record.get('source_record_id')))
    # The same native row can support several normalized joins. Preserve those
    # relationships in members; distinct-object counters never count joins.
    return group_observations(observations)


def validate_configuration_groups(model):
    try:
        expected = group_observations(model.get('members') or [])
        if expected == model:
            return []
    except (TypeError, ValueError, AttributeError, KeyError):
        pass
    return ['Configuration grouping identity, membership or counts do not reconcile.']


def summary_html(model):
    """Customer summary deliberately excludes object identity and protected lineage."""
    from html import escape
    rows = []
    for group in model.get('groups') or []:
        cells = [group['configuration'], group['observed'], group['recommended'], group['assessment'], group['object_type'],
                 group['scope'], group['provider'], group['environment'], group['configuration_source'], group['collection_state'],
                 group['affected_object_count'], group['total_objects_assessed'], group['population_qualification']]
        rows.append('<tr>' + ''.join('<td>' + escape(str(v)) + '</td>' for v in cells) + '</tr>')
    if not rows: return ''
    headers = ('Configuration', 'Observed', 'Recommended', 'Assessment', 'Object type', 'Scope', 'Provider', 'Environment',
               'Configuration source', 'Collection state', 'Affected objects', 'Objects assessed', 'Population qualification')
    return '<h3>Grouped configuration results</h3><div class="table-scroll"><table><thead><tr>' + ''.join('<th>'+h+'</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
