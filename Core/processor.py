"""
Processor module for generating and exporting recommendations.
This module takes the collected service data and handles recommendation aggregation and export.
"""
from .export_recommendations import (
    export_to_csv,
    export_to_excel,
    export_to_html,
    open_html_report as open_html_report_file,
    print_recommendations_summary
)
from .evidence_layer import build_evidence_bundle
from .assessment_model import summarize_readiness
from .cross_provider_assessment import (
    assess_provider_and_use_cases,
    build_run_manifest,
    compare_baseline,
    evaluate_controls,
    load_assessment_profile,
    load_provider_evidence,
    run_integrity_checks,
)


def collect_all_recommendations(m365_recommendations, entra_info, purview_info, 
                                defender_info, power_platform_info, copilot_studio_info,
                                data_exposure_info=None):
    """Collect all recommendations from different services."""
    all_recommendations = []
    all_recommendations.extend(m365_recommendations)
    all_recommendations.extend(entra_info.get('recommendations', []))
    all_recommendations.extend(purview_info.get('recommendations', []))
    all_recommendations.extend(defender_info.get('recommendations', []))
    all_recommendations.extend(power_platform_info.get('recommendations', []))
    all_recommendations.extend(copilot_studio_info.get('recommendations', []))
    if data_exposure_info:
        all_recommendations.extend(data_exposure_info.get('recommendations', []))
    return reconcile_overlapping_evidence(all_recommendations)


def reconcile_overlapping_evidence(recommendations):
    """Prevent one collector's duplicate miss from contradicting evidence read elsewhere."""
    records = [dict(row) for row in (recommendations or [])]
    entra_risk_was_read = any(
        str(row.get('Service', '') or '') == 'Entra'
        and (
            (row.get('FindingKey') == 'entra.identity_risk.users' and row.get('EvidenceComplete') is True)
            or (row.get('EvidenceComplete') is not False and (
                'risky users detected in the tenant' in str(row.get('Observation', '') or '').lower()
                or 'no risky users detected' in str(row.get('Observation', '') or '').lower()))
        )
        for row in records
    )
    if not entra_risk_was_read:
        return records

    for row in records:
        observation = str(row.get('Observation', '') or '')
        if (
            str(row.get('Service', '') or '') == 'Defender'
            and 'identity risk data could not be retrieved' in observation.lower()
        ):
            row['Observation'] = (
                'Identity-risk evidence was collected through Entra ID Protection elsewhere in '
                'this assessment. The separate Defender collector did not return a duplicate copy.'
            )
            row['Recommendation'] = ''
            row['Status'] = 'Reference'
            row['Priority'] = ''
            row['Disposition'] = 'Reference'
            row['EvidenceBasis'] = 'Tenant evidence'
            row['Confidence'] = 'High'
    return records


def resolve_report_tenant_name(tenant_name, entra_info):
    """Prefer the explicit tenant name, then fall back to Entra tenant metadata."""
    if tenant_name:
        return tenant_name
    return entra_info.get('tenant_name')


def classify_export_path(path):
    """Classify an export path by file extension."""
    if not path:
        return None, None

    lowered = str(path).lower()
    if lowered.endswith('.xlsx'):
        return 'excel', path
    if lowered.endswith('.csv'):
        return 'csv', path
    if lowered.endswith('.html'):
        return 'html', path
    return None, path


def export_tabular_reports(all_recommendations, report_tenant_name, report_format, evidence_bundle=None, stem=None, output_dir=None):
    """Export recommendations in the requested tabular format with Excel-first fallback."""
    csv_path = None
    excel_path = None
    csv_name = stem + '.csv' if stem else None

    if report_format == 'csv':
        csv_path = export_to_csv(all_recommendations, filename=csv_name, tenant_name=report_tenant_name, output_dir=output_dir)
        return csv_path, excel_path

    if report_format in {'excel', 'both'}:
        from .workbook_layout import WorkbookLayoutError
        try:
            preferred_path = export_to_excel(
                all_recommendations,
                filename=stem + '.xlsx' if stem else None,
                tenant_name=report_tenant_name,
                evidence_bundle=evidence_bundle,
                output_dir=output_dir,
            )
        except WorkbookLayoutError:
            raise
        except Exception as exc:
            print(f"Warning: Excel export failed ({exc}). Falling back to CSV export...")
            preferred_path = export_to_csv(all_recommendations, filename=csv_name, tenant_name=report_tenant_name, output_dir=output_dir)

        export_kind, resolved_path = classify_export_path(preferred_path)
        if export_kind == 'excel':
            excel_path = resolved_path
        elif export_kind == 'csv':
            csv_path = resolved_path

    if report_format == 'both' and not csv_path:
        csv_path = export_to_csv(all_recommendations, filename=csv_name, tenant_name=report_tenant_name, output_dir=output_dir)

    return csv_path, excel_path


def process_and_print_all_information(m365_result, entra_info, 
                                      purview_info, defender_info, power_platform_info, 
                                      copilot_studio_info, tenant_name=None, open_html_report=False,
                                      report_format='excel', sam_report_paths=None,
                                      dspm_report_paths=None, data_exposure_enabled=True,
                                      assessment_profile=None, provider_evidence=None,
                                      snapshot_json=None, baseline=None, enabled_collectors=None,
                                      connection_results=None, reports_dirs=None,
                                      copilot_readiness_export=None, collection_context=None,
                                      expected_tenant_id=None, offline_copilot_dashboard_export=None,
                                      offline_power_platform_inventory=None, include_user_usage_detail=True,
                                      prior_report=None, portal_review=None, output_dir=None, customer_name=None,
                                      extra_exports=None):
    """Process all service information and generate recommendations."""
    # Unpack M365 results
    (m365_info, m365_recommendations) = m365_result
    m365_info = dict(m365_info) if isinstance(m365_info, dict) else {}
    m365_recommendations = list(m365_recommendations or [])
    # SharePoint findings are rebuilt from the saved payload so replays apply
    # the current rules (for example numeric enum values from SharePoint
    # PowerShell). They are deterministic from that payload. Only findings the
    # collection originally produced from it are replaced; supplied evidence
    # without them is left as it is.
    sharepoint_payload = getattr(m365_info.get('_client'), 'sharepoint_governance', None)
    if (isinstance(sharepoint_payload, dict) and sharepoint_payload
            and any(str(row.get('FindingKey') or '').startswith('sharepoint.') for row in m365_recommendations)):
        from .sharepoint_governance import build_sharepoint_recommendations
        m365_recommendations = [row for row in m365_recommendations
                                if not str(row.get('FindingKey') or '').startswith('sharepoint.')]
        m365_recommendations.extend(build_sharepoint_recommendations(sharepoint_payload))
    m365_result = (m365_info, m365_recommendations)
    evidence_bundle = None
    from datetime import datetime, timezone
    evaluation_date = (collection_context or {}).get('evaluation_date') or datetime.now(timezone.utc).date().isoformat()
    from .portal_review import load_portal_review
    portal_review_data = load_portal_review(portal_review, expected_tenant_id, evaluation_date)
    expected_tenant_id = expected_tenant_id or portal_review_data.get('tenant_id')
    from types import SimpleNamespace
    from .new_recommendation import new_recommendation, CATEGORY_SCAN_COVERAGE
    from .portal_report_import import route_portal_reports, select_readiness_report, validate_report_tenants
    routed = route_portal_reports(reports_dirs)
    included_pdfs = {row['Source'] for row in portal_review_data.get('receipt', [])}
    handled = [warning for warning in routed['reference_warnings'] if warning.split(':', 1)[0] in included_pdfs]
    routed['warnings'] = [warning for warning in routed['warnings'] if warning not in handled]
    routed['reference_warnings'] = [warning for warning in routed['reference_warnings'] if warning not in handled]
    # Empty lists explicitly disable environment fallback, especially in offline mode.
    sam_report_paths = list(sam_report_paths or []) + routed['sam']
    dspm_report_paths = list(dspm_report_paths or []) + routed['dspm']
    if not collection_context:
        from .data_exposure_assessment import _split_env_paths
        sam_report_paths = sam_report_paths or _split_env_paths('SAM_DAG_REPORT_PATHS')
        dspm_report_paths = dspm_report_paths or _split_env_paths('DSPM_REPORT_PATHS')
    readiness_inputs = list(dict.fromkeys(([copilot_readiness_export] if copilot_readiness_export else []) + routed['readiness']))
    readiness_paths, identity_receipt = select_readiness_report(readiness_inputs, copilot_readiness_export)
    imported_tenant_id = validate_report_tenants(
        sam_report_paths + dspm_report_paths + readiness_inputs + [
            path for path in (offline_copilot_dashboard_export, offline_power_platform_inventory) if path
        ], expected_tenant_id, receipt=identity_receipt,
    )
    expected_tenant_id = expected_tenant_id or imported_tenant_id
    if readiness_paths:
        from .copilot_readiness_import import load_copilot_readiness_export
        readiness = load_copilot_readiness_export(readiness_paths[0], include_user_details=include_user_usage_detail)
        if not m365_info.get('_client'):
            m365_info['_client'] = SimpleNamespace()
        m365_info['_client'].copilot_readiness_export = readiness
        m365_recommendations.append(new_recommendation(
            'M365', 'Copilot readiness portal export', readiness['summary'] if readiness['available'] else readiness['error'],
            'Use this report to plan a pilot; readiness flags do not measure actual Copilot usage.' if readiness['available'] else 'Supply a valid Microsoft 365 admin center Copilot Readiness export.',
            priority='Low', status='Reference' if readiness['available'] else 'Not Assessed',
            disposition='Reference' if readiness['available'] else 'Coverage',
            category='Tenant Finding' if readiness['available'] else CATEGORY_SCAN_COVERAGE,
            finding_key='m365.portal_copilot_readiness', evidence_key='copilot_readiness_detail',
            impact_area='Adoption & value', confidence='High' if readiness['available'] else 'Unknown',
        ))
        m365_recommendations[-1].update(SourceType='portal_export', SourceFile=readiness.get('source_file'),
            ObservationDate=readiness.get('report_date'), EvidenceScope='Exported user rows', EvidenceComplete=False)
    if offline_copilot_dashboard_export:
        from .ai_usage import load_copilot_dashboard_export
        if not m365_info.get('_client'):
            m365_info['_client'] = SimpleNamespace()
        m365_info['_client'].copilot_dashboard = load_copilot_dashboard_export(offline_copilot_dashboard_export)
    if offline_power_platform_inventory:
        from .power_platform_inventory import load_power_platform_inventory
        power_platform_info = dict(power_platform_info)
        power_platform_info['_client'] = load_power_platform_inventory(offline_power_platform_inventory)
    from .ai_usage import _set_freshness
    for name in ('copilot_usage', 'm365_app_readiness', 'copilot_dashboard', 'shadow_ai_usage'):
        evidence = getattr(m365_info.get('_client'), name, None)
        if isinstance(evidence, dict) and evidence.get('available'):
            _set_freshness(evidence, 30 if name == 'copilot_dashboard' else 7, evaluation_date=evaluation_date)
    for warning in routed['warnings']:
        if warning in routed.get('reference_warnings', []):
            continue
        m365_recommendations.append(new_recommendation(
            'Assessment', 'Unrecognized portal export', warning,
            'Supply a supported report type or retain this file for manual review.',
            status='Not Assessed', category=CATEGORY_SCAN_COVERAGE, disposition='Coverage',
            finding_key='portal.unrecognized.' + warning.split(':')[0], confidence='Unknown',
        ))
    if collection_context and collection_context.get('freshness') in {'missing', 'stale'}:
        missing = collection_context['freshness'] == 'missing'
        missing_observation = (
            'No full tenant collection was supplied. Historical assessment context and saved Purview configuration are included where provided; '
            'identity, tenant-wide configuration and security controls have not been recollected.'
            if collection_context.get('historical_sources') else
            'Only portal exports were supplied. Identity, tenant configuration, security and policy controls were not collected in this run.'
        )
        m365_recommendations.append(new_recommendation(
            'Assessment', 'Offline tenant evidence coverage' if missing else 'Saved tenant collection freshness',
            missing_observation if missing else
            f"The saved tenant collection is {collection_context['age_days']} days old. Rebuilding the report does not refresh that evidence.",
            'Ask the tenant administrators to confirm the outstanding controls for the intended pilot population and record the evidence date.',
            status='Not Assessed', priority='High', category=CATEGORY_SCAN_COVERAGE,
            disposition='Coverage', finding_key='offline.tenant_coverage', confidence='Unknown',
        ))
    
    from .data_exposure_assessment import build_data_exposure_assessment
    data_exposure_info = build_data_exposure_assessment(
        sam_report_paths=sam_report_paths,
        dspm_report_paths=dspm_report_paths,
        enabled=data_exposure_enabled,
        evaluation_date=evaluation_date,
        sam_max_age_days=(collection_context or {}).get('assessment_settings', {}).get('sam_report_max_age_days'),
        dspm_max_age_days=(collection_context or {}).get('assessment_settings', {}).get('dspm_report_max_age_days'),
        lifecycle_max_age_days=(collection_context or {}).get('assessment_settings', {}).get('lifecycle_report_max_age_days'),
        lifecycle_report_dates=(collection_context or {}).get('assessment_settings', {}).get('lifecycle_report_dates'),
    )
    # Exported Data access governance reports answer the report families the
    # SharePoint collector could not read itself.
    from .sharepoint_governance import reconcile_dag_coverage
    m365_recommendations = reconcile_dag_coverage(
        m365_recommendations, ((data_exposure_info.get('sources') or {}).get('sam') or {}).get('reports'))
    if data_exposure_info.get('operator_messages'):
        from .console_reporting import detail
        for message in data_exposure_info['operator_messages']:
            detail(message)

    # Collect all recommendations
    from .saved_control_checks import assess_purview_configuration, assess_defender_configuration, qualify_saved_recommendations
    entra_info = dict(entra_info)
    entra_info['recommendations'] = qualify_saved_recommendations(entra_info.get('recommendations', []), 'Entra', entra_info.get('_client'))
    purview_info = dict(purview_info)
    purview_info['recommendations'] = list(purview_info.get('recommendations', [])) + assess_purview_configuration(
        purview_info.get('_client'), (collection_context or {}).get('collected_at'))
    purview_info['recommendations'] = qualify_saved_recommendations(purview_info['recommendations'], 'Purview', purview_info.get('_client'))
    defender_info = dict(defender_info)
    defender_info['recommendations'] = qualify_saved_recommendations(defender_info.get('recommendations', []), 'Defender', defender_info.get('_client'))
    defender_info['recommendations'] += assess_defender_configuration(defender_info.get('_client'), (collection_context or {}).get('collected_at'))
    # Methodology 3.0: judge each required control on tenant-wide configuration,
    # so the pilot decision never depends on a supplied pilot roster.
    from .tenant_baseline import assess_tenant_baseline
    _m365_baseline_client = m365_info.get('_client') if isinstance(m365_info, dict) else None
    _collected_at = (collection_context or {}).get('collected_at') or ''
    baseline_rows = assess_tenant_baseline(
        m365_client=_m365_baseline_client, entra_client=entra_info.get('_client'),
        purview_client=purview_info.get('_client'), collected_at=_collected_at)
    entra_info['recommendations'] += [row for row in baseline_rows if row['Service'] == 'Entra']
    purview_info['recommendations'] += [row for row in baseline_rows if row['Service'] == 'Purview']
    m365_recommendations = list(m365_recommendations) + [row for row in baseline_rows if row['Service'] == 'M365']
    all_recommendations = collect_all_recommendations(
        m365_recommendations, entra_info, purview_info, 
        defender_info, power_platform_info, copilot_studio_info,
        data_exposure_info,
    )
    report_tenant_name = resolve_report_tenant_name(tenant_name, entra_info)
    
    # Print and export recommendations
    if all_recommendations:
        evidence_bundle = build_evidence_bundle(
            all_recommendations,
            m365_result,
            entra_info,
            purview_info,
            defender_info,
            power_platform_info,
            copilot_studio_info,
            data_exposure_info,
            collection_context=collection_context,
        )
        evidence_bundle['prior_report'] = prior_report or {}
        evidence_bundle['portal_review'] = portal_review_data
        all_recommendations, control_results = evaluate_controls(evidence_bundle['recommendations'])
        evidence_bundle['recommendations'] = all_recommendations
        evidence_bundle['control_results'] = control_results

        recommendation_by_id = {row.get('RecommendationId'): row for row in all_recommendations}
        for row in evidence_bundle.get('evidence_index', []):
            source = recommendation_by_id.get(row.get('RecommendationId'), {})
            row['Control ID'] = source.get('ControlId', '')
            row['Methodology Version'] = source.get('MethodologyVersion', '')
            row['Finding Fingerprint'] = source.get('FindingFingerprint', '')

        profile = load_assessment_profile(assessment_profile)
        providers = load_provider_evidence(provider_evidence,
            max_age_days=(collection_context or {}).get('assessment_settings', {}).get('provider_evidence_max_age_days'),
            evaluation_date=evaluation_date)
        evidence_bundle['assessment_profile'] = profile
        evidence_bundle['provider_evidence'] = providers

        m365_client = m365_info.get('_client') if isinstance(m365_info, dict) else None
        entra_client = entra_info.get('_client') if isinstance(entra_info, dict) else None
        source_statuses = {}
        progress = (collection_context or {}).get('collection_progress', {})
        for name, state in progress.get('services', {}).items():
            if state.get('status') not in {'completed', 'not_selected'}:
                source_statuses[f'pipeline_{name}'] = {
                    'availability_status': state.get('availability_status', 'unavailable'),
                    'available': False, 'reason': state.get('reason', 'Collection did not finish.'),
                }
        if entra_client:
            source_statuses.update(getattr(entra_client, 'collection_status', {}) or {})
        defender_client = defender_info.get('_client') if isinstance(defender_info, dict) else None
        purview_client = purview_info.get('_client') if isinstance(purview_info, dict) else None
        if defender_client:
            source_statuses.update({
                f'defender_{key}': value
                for key, value in (getattr(defender_client, 'collection_status', {}) or {}).items()
            })
        if purview_client:
            source_statuses.update({
                f'purview_{key}': value
                for key, value in (getattr(purview_client, 'collection_status', {}) or {}).items()
            })
        elif purview_info.get('availability_status') == 'not_requested':
            source_statuses['purview'] = {
                'availability_status': 'not_requested', 'available': False,
                'reason': purview_info.get('reason', ''), 'records_collected': '',
            }
        if m365_client:
            source_statuses.update({f'm365_{key}': value for key, value in (getattr(m365_client, 'collection_status', {}) or {}).items()})
            sharepoint_governance = getattr(m365_client, 'sharepoint_governance', {}) or {}
            if sharepoint_governance and not sharepoint_governance.get('available'):
                source_statuses['sharepoint_governance'] = {
                    'availability_status': sharepoint_governance.get('availability_status') or 'unavailable',
                    'available': False, 'reason': sharepoint_governance.get('reason') or sharepoint_governance.get('error') or 'SharePoint governance collection did not return usable evidence.',
                    'records_collected': '', 'truncated': False,
                }
            source_statuses.update({
                key: value for key, value in (sharepoint_governance.get('collection_status', {}) or {}).items()
            })
            for name in ('copilot_usage', 'm365_app_readiness', 'copilot_dashboard', 'shadow_ai_usage'):
                evidence = getattr(m365_client, name, None)
                if isinstance(evidence, dict):
                    source_statuses[name] = evidence
            sharepoint_payload = getattr(m365_client, 'sharepoint_governance', {}) or {}
            for name, state in (sharepoint_payload.get('collection_status', {}) or {}).items():
                if isinstance(state, dict):
                    source_statuses[name] = state
        for source_name, source in (data_exposure_info.get('sources', {}) or {}).items():
            if isinstance(source, dict):
                source_statuses[f'data_exposure_{source_name}'] = {
                    'availability_status': source.get('status', 'available' if source.get('files_loaded') else 'not_requested'),
                    'records_collected': source.get('records_read', 0),
                    'pages_collected': '',
                    'truncated': bool(data_exposure_info.get('evidence_truncated')),
                    'reason': source.get('reason', ''),
                }
        for result in connection_results or []:
            source_statuses[f"connection_{result.get('collector_id', 'unknown')}"] = {
                'availability_status': 'available' if result.get('status') in {'Ready', 'Ready with gaps'} else
                    'not_requested' if result.get('status') == 'Optional source not selected' else
                    'deferred' if result.get('status') in {'Sign-in required', 'Interactive sign-in required', 'Browser sign-in will follow'} else 'unavailable',
                'records_collected': '',
                'pages_collected': '',
                'truncated': False,
                'reason': 'At original collection time: ' + result.get('reason', ''),
                'maturity': result.get('maturity', ''),
                'evidence_purpose': result.get('evidence_purpose', ''),
            }
        # Record which identity and auth path produced each dataset. Older
        # collections without a saved plan show the identity as unrecorded.
        from .auth_plan import annotate_source_statuses
        annotate_source_statuses(source_statuses, (collection_context or {}).get('auth_plan') or {})
        evidence_bundle['source_statuses'] = source_statuses
        evidence_bundle['auth_plan'] = (collection_context or {}).get('auth_plan') or {}
        evidence_bundle['import_receipt'] = [
            {'Source': report.get('source_file'), 'Status': report.get('status'),
             'Original Date': report.get('report_date'), 'Scope': report.get('workload'),
             'Reason': report.get('scope_note') or ('No data rows; does not establish assurance.' if report.get('status') == 'empty' else '')}
            for scan in data_exposure_info.get('sources', {}).values() for report in scan.get('reports', [])
        ]
        evidence_bundle['import_receipt'].extend(identity_receipt)
        evidence_bundle['import_receipt'].extend(portal_review_data['receipt'])
        for scan in data_exposure_info.get('sources', {}).values():
            evidence_bundle['import_receipt'].extend({'Source': 'Supplied ' + scan.get('kind', '') + ' report',
                'Status': 'rejected', 'Original Date': '', 'Scope': '', 'Reason': error} for error in scan.get('errors', []))
        evidence_bundle['import_receipt'].extend({'Source': warning.split(':')[0], 'Status': 'reference_only' if warning in routed.get('reference_warnings', []) else 'unsupported',
            'Original Date': '', 'Scope': '', 'Reason': warning} for warning in routed['warnings'])
        for path in readiness_paths:
            evidence_bundle['import_receipt'].append({'Source': readiness.get('source_file'),
                'Status': 'accepted' if readiness.get('available') else 'rejected',
                'Original Date': readiness.get('report_date'), 'Scope': 'Exported user rows',
                'Reason': readiness.get('error') or '; '.join(readiness.get('warnings', []))})
        for label, evidence in (('Prior assessment', prior_report),):
            if evidence:
                evidence_bundle['import_receipt'].append({'Source': evidence.get('source_file'),
                    'Status': 'historical', 'Original Date': evidence.get('generated_at'),
                    'Scope': label, 'Reason': 'Original conclusions retained for confirmation; source methodology preserved.'})
        evidence_bundle['collection_context'] = collection_context or {}
        evidence_bundle['expected_tenant_id'] = expected_tenant_id
        evidence_bundle['observations'] = data_exposure_info.get('observations', [])
        from .assessment_catalog import collect_assessment_sources
        evidence_bundle['assessment_sources'] = collect_assessment_sources(
            [m365_client, entra_client, defender_client, purview_client],
            collected_at=(collection_context or {}).get('collected_at', ''), data_exposure=data_exposure_info, profile=profile,
            recommendations=all_recommendations)
        from .assessment_result import build_assessment_result
        from .expanded_findings import expanded_findings
        all_recommendations.extend(expanded_findings(evidence_bundle, profile=profile,
            evaluation_date=evaluation_date,tenant_id=expected_tenant_id))
        assessment_result = build_assessment_result(all_recommendations, evidence_bundle,
            evaluation_date=evaluation_date, expected_tenant_id=expected_tenant_id)
        evidence_bundle['assessment_result'] = assessment_result
        from .copilot_admin_review import build_admin_review
        evidence_bundle['copilot_admin_review'] = build_admin_review(m365_client, purview_client, collection_context)
        from .investigation_details import prepare_investigation_details
        prepare_investigation_details(evidence_bundle, assessment_result)
        all_recommendations = assessment_result['recommendations']
        evidence_bundle['recommendations'] = all_recommendations
        control_results = assessment_result.get('control_results', control_results)
        evidence_bundle['control_results'] = control_results
        conclusions = assess_provider_and_use_cases(profile, providers, assessment_result['decision'])
        evidence_bundle['conclusions'] = conclusions
        evidence_bundle['run_manifest'] = build_run_manifest(
            report_tenant_name,
            {
                'SAM reports': sam_report_paths,
                'DSPM reports': dspm_report_paths,
                'Copilot dashboard': getattr(m365_client, 'copilot_dashboard', {}).get('filename', '') if m365_client else '',
                'Power Platform inventory': getattr(
                    power_platform_info.get('_client') if isinstance(power_platform_info, dict) else None,
                    'power_platform_inventory', {},
                ).get('filename', '') if isinstance(power_platform_info, dict) else '',
                'Assessment profile': assessment_profile,
                'Provider evidence': provider_evidence,
                'Baseline': baseline,
                'Copilot readiness export': readiness_paths,
                'Saved collection': (collection_context or {}).get('source_file'),
                'Prior assessment': (prior_report or {}).get('source_file'),
                'Portal review manifest': portal_review,
            },
            enabled_collectors or [],
            source_statuses,
        )
        evidence_bundle['run_manifest']['rows'].extend([
            {'Item': 'Tenant ID', 'Value': expected_tenant_id or 'Not established'},
            {'Item': 'Evaluation Date', 'Value': evaluation_date},
            {'Item': 'Permission profile', 'Value': (collection_context or {}).get('permission_profile') or 'unrecorded'},
            {'Item': 'Collection progress', 'Value': progress.get('status', 'unrecorded')},
            {'Item': 'Evidence Schema Version', 'Value': assessment_result.get('evidence_schema_version')},
        ])
        if progress:
            evidence_bundle['run_manifest']['rows'].extend([
                {'Item': 'Collection checkpoint updated at', 'Value': progress.get('updated_at', '')},
                {'Item': 'Finished service pipelines', 'Value': ', '.join(
                    name for name, state in progress.get('services', {}).items() if state.get('status') == 'completed') or 'None'},
                {'Item': 'Unfinished service pipelines', 'Value': ', '.join(
                    name for name, state in progress.get('services', {}).items()
                    if state.get('status') not in {'completed', 'not_selected', 'not_requested'}) or 'None'},
            ])
        if collection_context and collection_context.get('mode') == 'offline':
            evidence_bundle['run_manifest']['rows'].extend([
                {'Item': 'Report build mode', 'Value': 'Offline - no tenant connection'},
                {'Item': 'Tenant evidence collected at', 'Value': collection_context.get('collected_at') or 'Not collected; portal exports only'},
                {'Item': 'Saved collection freshness', 'Value': collection_context.get('freshness', 'Unknown')},
            ])
            for source in collection_context.get('historical_sources', []):
                evidence_bundle['run_manifest']['rows'].append({
                    'Item': source['source_type'],
                    'Value': f"{source['source_file']}; original source date: {source.get('reported_at') or 'Unknown'}",
                })
        evidence_bundle['baseline_comparison'] = compare_baseline(all_recommendations, control_results, baseline)
        evidence_bundle['integrity'] = run_integrity_checks(all_recommendations, evidence_bundle)

        from pathlib import Path
        from .app_builder_export import write_app_builder_export
        from .dashboard_export import build_dashboard_export, write_dashboard_export
        from .dashboard_package import write_dashboard_archive, write_dashboard_package
        from .export_recommendations import build_report_filename
        from .finding_evidence import build_finding_evidence
        from .html_evidence_pages import write_html_evidence_pages
        from .technical_guidance import attach_technical_guidance
        from .workbook_evidence import add_evidence_sheets, sync_workbook_locations
        from .export_paths import (new_assessment_directory, new_deliverables_directory, report_directory, report_file_stem,
                                  SUMMARY_STEM, EVIDENCE_FOLDER, APP_BUILDER_FOLDER, JSON_FOLDER, JSON_ARCHIVE_STEM)
        from .workbook_layout import technical_workbook_path
        extra_exports = set(extra_exports or ())
        include_evidence_pages = 'evidence-pages' in extra_exports
        include_app_builder = 'app-builder' in extra_exports
        include_dashboard_json = 'dashboard-json' in extra_exports
        if output_dir is None:
            package_directory = new_assessment_directory(customer_name=customer_name,
                tenant_name=report_tenant_name, tenant_id=expected_tenant_id)
            output_dir = new_deliverables_directory(package_directory)
        reports_root = report_directory(output_dir)
        filename_context = dict(customer_name=customer_name, tenant_name=report_tenant_name,
                                tenant_id=expected_tenant_id)
        base_stem = Path(build_report_filename('json', **filename_context)).stem
        summary_stem = report_file_stem(SUMMARY_STEM, **filename_context)
        archive_stem = report_file_stem(JSON_ARCHIVE_STEM, **filename_context)
        suffix, number = '', 1
        while True:
            report_stem = base_stem + suffix
            summary_name = summary_stem + suffix + '.html'
            evidence_folder_name = EVIDENCE_FOLDER + suffix
            app_builder_folder_name = APP_BUILDER_FOLDER + suffix
            json_folder = reports_root / (JSON_FOLDER + suffix)
            archive_name = archive_stem + suffix + '.zip'
            destinations = [
                reports_root / f'{report_stem}.html', reports_root / f'{report_stem}.xlsx', reports_root / f'{report_stem}.csv',
                technical_workbook_path(reports_root / f'{report_stem}.xlsx'),
                reports_root / summary_name]
            if include_evidence_pages:
                destinations.append(reports_root / evidence_folder_name)
            if include_app_builder:
                destinations.append(reports_root / app_builder_folder_name)
            if include_dashboard_json:
                destinations.extend((json_folder, reports_root / archive_name))
            if not any(path.exists() for path in destinations):
                break
            number += 1
            suffix = f' ({number})'
        # The shared finding-evidence model is built once, before any deliverable,
        # so App Builder, workbook and HTML show the same rows, IDs and counts.
        dashboard = attach_technical_guidance(build_dashboard_export(assessment_result, evidence_bundle,
            tenant_name=report_tenant_name, generated_at=evidence_bundle['run_manifest']['generated_at']))
        finding_model = build_finding_evidence(dashboard)
        evidence_bundle['finding_evidence'] = finding_model
        if include_evidence_pages:
            evidence_bundle['html_evidence_folder'] = evidence_folder_name
        app_builder_files = {}
        if include_app_builder:
            app_builder = write_app_builder_export(
                finding_model, reports_root / app_builder_folder_name,
                deliverables={'html': f'{report_stem}.html', 'summary_html': summary_name,
                              'workbook': f'{report_stem}.xlsx' if report_format in {'excel', 'both'} else None,
                              'technical_workbook': technical_workbook_path(reports_root / f'{report_stem}.xlsx').name if report_format in {'excel', 'both'} else None,
                              'evidence_pages': f'{evidence_folder_name}/index.html' if include_evidence_pages else None,
                              'dashboard_json': f'{json_folder.name}/index.json' if include_dashboard_json else None})
            evidence_bundle['app_builder_folder'] = app_builder['folder']
            evidence_bundle['app_builder_guide'] = app_builder['guide']
            app_builder_files = {key: value['required'] + value['context']
                                 for key, value in app_builder['finding_files'].items()}
        add_evidence_sheets(evidence_bundle, assessment_result, finding_model,
                            html_folder=evidence_folder_name if include_evidence_pages else None,
                            app_builder_files=app_builder_files)
        csv_path, excel_path = export_tabular_reports(
            all_recommendations,
            report_tenant_name,
            report_format,
            evidence_bundle=evidence_bundle,
            stem=report_stem,
            output_dir=reports_root,
        )
        sync_workbook_locations(assessment_result, finding_model)
        # The dashboard projection precedes layout so evidence IDs are shared.
        # Add presentation destinations afterwards without rebuilding records or
        # changing the original investigation/raw/lineage range fields.
        workbook_rows = {row.get('RecommendationId'): row for row in assessment_result['recommendations']}
        layout_fields = ('AssessmentEvidenceRange', 'AssessmentEvidenceRanges', 'AssessmentEvidenceCount',
                         'AssessmentAffected', 'TechnicalEvidenceRanges')
        for rows in [dashboard.get('recommendations') or [],
                     *(dashboard.get('assessment_result', {}).get(key) or [] for key in
                       ('recommendations', 'actions', 'customer_findings'))]:
            for row in rows:
                actual = workbook_rows.get(row.get('RecommendationId')) or {}
                row.update({key: actual[key] for key in layout_fields if key in actual})
        technical_excel_path = evidence_bundle.get('technical_excel_path') if excel_path else None
        if include_evidence_pages:
            evidence_pages = write_html_evidence_pages(
                finding_model, reports_root / evidence_folder_name, report_name=f'{report_stem}.html',
                workbook_name=Path(excel_path).name if excel_path else None,
                technical_workbook_name=Path(technical_excel_path).name if technical_excel_path else None,
                app_builder_files=app_builder_files)
            evidence_bundle['html_evidence_folder_path'] = evidence_pages['folder']
            evidence_bundle['html_evidence_index'] = evidence_pages['index']
        html_path = export_to_html(
            all_recommendations,
            filename=f'{report_stem}.html',
            tenant_name=report_tenant_name,
            evidence_bundle=evidence_bundle,
            excel_path=excel_path,
            technical_excel_path=technical_excel_path,
            output_dir=reports_root,
            summary_filename=summary_name,
        )
        dashboard['deliverables'] = {key: Path(path).name if path else None for key, path in
                                    {'html': html_path, 'summary_html': evidence_bundle.get('summary_html_path'),
                                     'workbook': excel_path, 'technical_workbook': technical_excel_path, 'csv': csv_path}.items()}
        if include_evidence_pages:
            dashboard['deliverables']['evidence_pages'] = f'{evidence_folder_name}/index.html'
        if include_app_builder:
            dashboard['deliverables']['app_builder'] = f'{app_builder_folder_name}/00-upload-guide.md'
        dashboard['deliverable_path_base'] = 'report_directory'
        if include_dashboard_json:
            package_dashboard = dict(dashboard)
            package_dashboard['deliverables'] = {key: '../' + name if name else None
                                                 for key, name in dashboard['deliverables'].items()}
            package_dashboard['deliverable_path_base'] = 'index_directory'
            json_path = write_dashboard_package(json_folder, package_dashboard)
            json_archive_path = write_dashboard_archive(
                json_path, reports_root / archive_name)
            evidence_bundle['dashboard_json_path'] = json_path
            evidence_bundle['dashboard_json_folder'] = str(json_folder)
            evidence_bundle['dashboard_json_archive_path'] = json_archive_path
        if snapshot_json:
            # A caller-selected snapshot can live anywhere. Its links are based
            # on its own location, rather than assuming a flat Reports folder.
            import os
            snapshot_dashboard = dict(dashboard)
            snapshot_parent = Path(snapshot_json).resolve().parent
            def snapshot_link(name):
                if not name:
                    return None
                target = (reports_root / name).resolve()
                try:
                    return Path(os.path.relpath(target, snapshot_parent)).as_posix()
                except ValueError:  # Different Windows drives have no relative path.
                    return target.as_uri()
            snapshot_dashboard['deliverables'] = {key: snapshot_link(name)
                for key, name in dashboard['deliverables'].items()}
            snapshot_dashboard['deliverable_path_base'] = 'index_directory'
            snapshot_path = write_dashboard_export(snapshot_json, snapshot_dashboard)
            from .console_reporting import detail_path
            detail_path('Snapshot JSON', snapshot_path)
        print_recommendations_summary(
            all_recommendations,
            csv_path,
            excel_path,
            html_path,
            tenant_name=report_tenant_name,
            evidence_bundle=evidence_bundle,
        )
        if open_html_report and html_path:
            # The one-page summary is the entry point; it links to the full report.
            opened = open_html_report_file(evidence_bundle.get('summary_html_path') or html_path)
            if opened:
                print("HTML report opened in your default browser.")
    else:
        print_recommendations_summary(
            all_recommendations,
            tenant_name=report_tenant_name,
            evidence_bundle=evidence_bundle,
        )
    
    return {'report_directory': str(reports_root) if all_recommendations else None,
            'html_path': html_path if all_recommendations else None,
            'summary_html_path': (evidence_bundle or {}).get('summary_html_path') if all_recommendations else None,
            'excel_path': excel_path if all_recommendations else None,
            'technical_excel_path': technical_excel_path if all_recommendations else None,
            'csv_path': csv_path if all_recommendations else None,
            'json_path': (evidence_bundle or {}).get('dashboard_json_path'),
            'json_folder_path': (evidence_bundle or {}).get('dashboard_json_folder'),
            'json_archive_path': (evidence_bundle or {}).get('dashboard_json_archive_path'),
            'html_evidence_folder_path': (evidence_bundle or {}).get('html_evidence_folder_path'),
            'app_builder_folder_path': (evidence_bundle or {}).get('app_builder_folder'),
            'evidence_bundle': evidence_bundle}
