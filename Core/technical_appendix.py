"""Bounded, named review examples; complete inventories remain in the workbook."""
from html import escape
from urllib.parse import quote

from .device_summary import setting_reason
from .device_examples import device_example, error_detail, status_name, example_timing


def _example_tables(finding, tables, subsets):
    selected = [tables.get(key, {}) for key in finding['evidence'].get('tables', [])]
    if not subsets:
        return selected
    # Offer a representative from each reporting-age subset before filling the
    # three-example limit. The complete populations remain in linked evidence.
    rows = [record for table in selected for record in table.get('rows', []) if record.get('finding_id') == finding['finding_id']]
    preferred = []
    seen_devices = set()
    from .endpoint_observations import LABELS
    for key in LABELS:
        subset = subsets[key]
        ids = set(subset['evidence_ids'])
        for record in rows:
            device = record.get('managedDeviceId') or record.get('ParentId') or record.get('machineId') or record.get('deviceId')
            if device and str(device).casefold() in seen_devices:
                continue
            if ids.intersection(str(record.get('evidence_record_ids') or '').split('; ')):
                preferred.append(record)
                if device:
                    seen_devices.add(str(device).casefold())
                break
    return [{'rows': preferred + [record for record in rows if record not in preferred]}]


def render_technical_appendix(result, bundle, workbook_url, technical_url, model_findings, collection_rows):
    from .customer_report import _heading, prose, slug, table
    model = bundle.get('finding_evidence') or {}
    tables = model.get('tables') or {}
    devices = {str(item['device_id']).casefold(): item for item in (result.get('device_compliance') or {}).get('devices', [])
               if item.get('device_id') and not item.get('conflict')}
    target_file = technical_url or workbook_url
    rows_by_id = {row.get('RecommendationId'): row for row in result.get('recommendations', [])}
    settings_by_id = {item['evidence']['evidence_id']: item
                      for item in (result.get('device_compliance') or {}).get('settings', [])}
    rendered, used = [], set()
    for action in [*result.get('actions', []), *result.get('opportunities', [])]:
        identifier = action.get('RecommendationId')
        finding = model_findings.get(identifier)
        if not finding or identifier in used:
            continue
        used.add(identifier)
        row = rows_by_id.get(identifier, action)
        kind = row.get('DeviceFindingType')
        if kind == 'InventoryComplianceState' and (result.get('device_compliance') or {}).get('summary'):
            continue  # Aggregate context already appears once in the device overview.
        subsets = row.get('EndpointObservationSubsets') or {}
        interpretations = {item['evidence_id']: item for item in row.get('DeviceEvidenceInterpretations', [])}
        selected_settings_by_device = {}
        for key in row.get('DeviceEvidenceIds', []):
            item = settings_by_id.get(key)
            if item and item.get('device_id'):
                selected_settings_by_device.setdefault(str(item['device_id']).casefold(), []).append(item)
        examples, seen = [], set()
        for evidence_table in _example_tables(finding, tables, subsets):
            for record in evidence_table.get('rows', []):
                if record.get('finding_id') != identifier:
                    continue
                settings = [settings_by_id[key] for key in str(record.get('evidence_record_ids') or '').split('; ')
                            if key in settings_by_id]
                interpreted = [interpretations[key] for key in str(record.get('evidence_record_ids') or '').split('; ')
                               if key in interpretations]
                device_id = (settings[0].get('device_id') if settings else None) or record.get('managedDeviceId') or record.get('deviceId')
                if not device_id and record.get('dataset') == 'managed_devices':
                    device_id = record.get('id')
                if not device_id and record.get('dataset') == 'windows_protection':
                    device_id = record.get('ParentId')
                if interpreted and not any(item.get('identity_unresolved') for item in interpreted):
                    device_id = interpreted[0].get('intune_id') or device_id
                unresolved = any(item.get('identity_unresolved') for item in interpreted) or (settings and not settings[0].get('device_id'))
                device_model = {} if unresolved else devices.get(str(device_id or '').casefold(), {})
                if settings and not unresolved:
                    settings = selected_settings_by_device.get(str(device_id or '').casefold(), settings)
                device = device_model.get('device') or {}
                name = next((record.get(key) for key in ('deviceName', 'upn', 'userPrincipalName', 'userDisplayName',
                    'displayName', 'policyName', 'compliancePolicyName', 'name', 'title') if record.get(key)), None)
                name = name or device.get('deviceName')
                if not isinstance(name, str) or not name.strip():
                    continue
                identity = str(device_id or record.get('userId') or record.get('principalId') or record.get('id') or name)
                if identity in seen:
                    continue
                seen.add(identity)
                status = record.get('riskState') or record.get('complianceState') or record.get('state') or record.get('status') or ''
                setting = record.get('settingName') or record.get('setting')
                note = (setting_reason(setting) if setting and str(status).casefold() == 'noncompliant' else str(setting or status))
                if record.get('riskLevel'):
                    note = str(status) + '; risk level: ' + str(record['riskLevel'])
                policy = record.get('policyName') or record.get('compliancePolicyName')
                if policy and policy != name:
                    note += '; policy: ' + str(policy)
                example = (device_example(record, device_model, settings, kind) if kind and kind not in {
                    'CompliantDeviceAccessEnforcementNotEstablished', 'UnverifiedLegacyDeviceObservation'} else
                    {'detail': note or 'No descriptive reason retained', 'status': status_name(status),
                     'diagnostic': error_detail(record) or 'Not retained'})
                if kind and 'setting' in example:
                    timing = example_timing(record, device_model, interpreted, settings,
                        row.get('EndpointReportingDays') or (result.get('device_compliance') or {}).get('reporting_days', 7))
                    example.update({key: timing[key] for key in ('activity', 'recency', 'reported')})
                    if timing['date_basis']:
                        example['detail'] += '; '+timing['date_basis']
                examples.append({'name': name[:120], **{key: str(value)[:240] for key, value in example.items()}})
                if len(examples) == 3:
                    break
            if len(examples) == 3:
                break
        if not examples:
            continue
        location = next(iter(row.get('TechnicalEvidenceRanges') or []), '')
        target = target_file + ('#' + quote(location, safe='!:$') if location else '')
        link = '<p><a href="' + escape(target, quote=True) + '">Review the complete finding evidence in the workbook</a></p>' if target_file else ''
        evidence = finding['evidence']
        count = row.get('DeviceEvidenceCounts', {}).get('resolved_device_count')
        population = (str(count) + (' device' if count == 1 else ' devices') if count is not None else
                      str(evidence['record_count']) + ' ' + str(evidence['record_unit']))
        population = row.get('DeviceSummary', {}).get('display_count', population)
        columns = ([('Example', 'name'), ('Observation', 'setting'), ('Status', 'status'),
                    ('Device activity', 'activity'), ('Evidence recency', 'recency'), ('Reported time', 'reported'), ('Context', 'detail')]
                   if kind in {'DefenderRealTimeProtectionObservation', 'DefenderSignatureObservation'} else
                   [('Example', 'name'), ('Compliance policy', 'policy'), ('Check / diagnostic' if kind == 'ReportingErrorOrConflict' else 'Requirement / result', 'setting'),
                    ('Status', 'status'), ('Device activity', 'activity'), ('Evidence recency', 'recency'), ('Reported time', 'reported'), ('Details', 'detail')] if examples and 'policy' in examples[0] else
                   [('Example', 'name'), ('Reason', 'detail'), ('Status', 'status'), ('Diagnostic details', 'diagnostic')])
        qualification = ('<p>Overall noncompliance is an inventory total, not an additional failure count. '
                         'Devices can also appear in the setting and reporting findings below; do not add these counts.</p>'
                         if kind == 'InventoryComplianceState' else
                         '<p>Intune did not establish results for these checks. These are incomplete observations, separate from confirmed noncompliance; devices may appear in both populations.</p>'
                         if kind == 'ReportingErrorOrConflict' else
                         '<p>Defender may be inactive when another antivirus is the primary provider. '
                         'An Off result alone does not establish missing protection; verify the provider and operating mode.</p>'
                         if kind == 'DefenderRealTimeProtectionObservation' else '')
        if subsets:
            from .endpoint_observations import subset_lines
            qualification += '<ul>' + ''.join('<li>' + prose(line) + '</li>' for line in subset_lines(subsets, row['EndpointReportingDays'])) + '</ul>'
        rendered.append('<details class="technical-example" id="evidence-' + slug(identifier) + '" data-finding-id="' + escape(str(identifier), quote=True) + '"><summary>'
            + prose(_heading(row)) + '</summary><p>' + str(len(examples)) + (' named example from ' if len(examples) == 1 else ' named examples from ')
            + prose(population) + '. Examples are a sample, not the complete population.</p>'
            + qualification + table(examples, columns, mobile_labels=True) + link + '</details>')
    # Keep existing report/summary anchors valid without embedding the entire
    # reference register. Those entries lead to the complete workbook links.
    for domain in result.get('domains', []):
        for row in domain.get('strengths', []):
            key = row.get('RecommendationId') or row.get('FindingKey') or row.get('Feature')
            if key:
                rows_by_id.setdefault(key, row)
    anchors = ''.join('<span id="evidence-' + slug(identifier) + '" data-finding-id="' + escape(str(identifier), quote=True) + '"></span>' for identifier in rows_by_id if identifier not in used or not any(
        'id="evidence-' + slug(identifier) + '"' in fragment for fragment in rendered))
    links = ' · '.join('<a href="' + escape(url, quote=True) + '">' + label + '</a>' for url, label in
                       ((workbook_url, 'Assessment workbook'), (technical_url, 'Technical evidence workbook')) if url)
    if bundle.get('html_evidence_folder'):
        links += (' · <a href="' + escape(quote(str(bundle['html_evidence_folder']) + '/index.html', safe='/'), quote=True)
                  + '">Complete technical evidence pages</a>')
    limited = [row for row in collection_rows if row.get('Availability state') not in {'available', 'empty', 'not_selected', 'skipped'}]
    limited_rows = [{'source': row.get('Dataset'), 'state': row.get('Availability state'),
                     'issue': str(row.get('Qualification') or row.get('Completeness') or '')[:160]}
                    for row in limited[:5]]
    source_note = ('<h3>Collection limitations</h3>' + table(limited_rows, [('Source', 'source'), ('State', 'state'), ('Review', 'issue')], mobile_labels=True)
                   + '<p>Complete source outcomes and report dates are recorded in the workbooks.</p>') if limited_rows else ''
    metadata = ('<p class="qualification">Evaluation: ' + prose(result.get('evaluation_date')) + ' · Methodology: '
                + prose(result.get('methodology_version')) + '. Complete run, source, lifecycle and governance registers are retained in the workbooks.</p>')
    context = bundle.get('collection_context') or {}
    run = result.get('run_context') or {}
    if run.get('RunId'):
        metadata += '<p>Run: ' + prose(run['RunId']) + ' · ' + prose(run.get('RunType')) + '.</p>'
    if context.get('permission_profile'):
        metadata += '<p>Collection permission profile: ' + prose(context['permission_profile']) + '.</p>'
    if context.get('collected_at'):
        metadata += ('<p>Original tenant collection: ' + prose(context['collected_at'])
                     + '. Report execution: ' + prose(context.get('mode') or 'Evidence supplied directly') + '.</p>')
    migration = context.get('methodology_migration') or {}
    if migration:
        metadata += ('<p>Saved collection methodology: ' + prose(migration.get('from'))
                     + '. Reassessed using methodology ' + prose(migration.get('to'))
                     + '. Original collection evidence and dates are preserved.</p>')
    prior = bundle.get('prior_report') or {}
    if prior:
        metadata += ('<p>Original report date: <strong>' + prose(prior.get('generated_at'))
                     + '</strong>. Historical conclusions require revalidation; the original register and source mapping remain in the Prior workbook tabs.</p>')
    historical = context.get('historical_sources') or []
    if historical:
        metadata += table(historical[:3], [('Historical input', 'source_type'), ('Original date', 'reported_at')])
        if len(historical) > 3:
            metadata += '<p>Additional historical input dates are retained in the workbook.</p>'
    reports = [row for source in (bundle.get('data_exposure') or {}).get('sources', {}).values()
               for row in source.get('reports', [])]
    if reports:
        metadata += '<h3>Imported report dates</h3>' + table(reports[:5], [('Report type', 'report_type'), ('Original date', 'report_date'), ('Selection', 'status')])
        metadata += '<p>Complete source filenames, dates and selection records are retained in the workbook.</p>'
    return ('<details class="appendix-panel" id="engineer-appendix"><summary>Technical appendix and evidence workbook</summary>'
        '<p>Review up to three named examples per finding. Use the technical workbook for all devices, users, policies and source records.</p>'
        '<p>' + links + '</p><section id="technical-review-examples" data-example-limit="3">' + ''.join(rendered) + '</section>' + anchors + source_note + metadata + '</details>')
