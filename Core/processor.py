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
    write_snapshot,
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
            'risky users detected in the tenant' in str(row.get('Observation', '') or '').lower()
            or 'no risky users detected' in str(row.get('Observation', '') or '').lower()
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


def export_tabular_reports(all_recommendations, report_tenant_name, report_format, evidence_bundle=None):
    """Export recommendations in the requested tabular format with Excel-first fallback."""
    csv_path = None
    excel_path = None

    if report_format == 'csv':
        csv_path = export_to_csv(all_recommendations, tenant_name=report_tenant_name)
        return csv_path, excel_path

    if report_format in {'excel', 'both'}:
        try:
            preferred_path = export_to_excel(
                all_recommendations,
                tenant_name=report_tenant_name,
                evidence_bundle=evidence_bundle,
            )
        except Exception as exc:
            print(f"Warning: Excel export failed ({exc}). Falling back to CSV export...")
            preferred_path = export_to_csv(all_recommendations, tenant_name=report_tenant_name)

        export_kind, resolved_path = classify_export_path(preferred_path)
        if export_kind == 'excel':
            excel_path = resolved_path
        elif export_kind == 'csv':
            csv_path = resolved_path

    if report_format == 'both' and not csv_path:
        csv_path = export_to_csv(all_recommendations, tenant_name=report_tenant_name)

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
                                      offline_power_platform_inventory=None, include_user_usage_detail=False,
                                      prior_report=None, portal_review=None):
    """Process all service information and generate recommendations."""
    # Unpack M365 results
    (m365_info, m365_recommendations) = m365_result
    m365_info = dict(m365_info) if isinstance(m365_info, dict) else {}
    m365_recommendations = list(m365_recommendations or [])
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
                'availability_status': 'available' if result.get('status') == 'Ready' else
                    'not_requested' if result.get('status') == 'Optional source not selected' else
                    'deferred' if result.get('status') in {'Sign-in required', 'Interactive sign-in required', 'Browser sign-in will follow'} else 'unavailable',
                'records_collected': '',
                'pages_collected': '',
                'truncated': False,
                'reason': 'At original collection time: ' + result.get('reason', ''),
                'maturity': result.get('maturity', ''),
                'evidence_purpose': result.get('evidence_purpose', ''),
            }
        evidence_bundle['source_statuses'] = source_statuses
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
        from .assessment_result import build_assessment_result
        assessment_result = build_assessment_result(all_recommendations, evidence_bundle,
            evaluation_date=evaluation_date, expected_tenant_id=expected_tenant_id)
        evidence_bundle['assessment_result'] = assessment_result
        from .copilot_admin_review import build_admin_review
        evidence_bundle['copilot_admin_review'] = build_admin_review(m365_client, purview_client, collection_context)
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

        if snapshot_json:
            snapshot_path = write_snapshot(snapshot_json, {
                'methodology_version': evidence_bundle['run_manifest']['methodology_version'],
                'assessment_version': evidence_bundle['run_manifest']['assessment_version'],
                'tenant': report_tenant_name,
                'generated_at': evidence_bundle['run_manifest']['generated_at'],
                'conclusions': conclusions,
                'control_results': control_results,
                'recommendations': all_recommendations,
                'collection_coverage': source_statuses,
                'integrity': evidence_bundle['integrity'],
                'assessment_result': assessment_result,
                'tenant_id': expected_tenant_id,
                'evaluation_date': evaluation_date,
            })
            from .console_reporting import detail_path
            detail_path('Snapshot JSON', snapshot_path)
        csv_path, excel_path = export_tabular_reports(
            all_recommendations,
            report_tenant_name,
            report_format,
            evidence_bundle=evidence_bundle,
        )
        html_path = export_to_html(
            all_recommendations,
            tenant_name=report_tenant_name,
            evidence_bundle=evidence_bundle,
            excel_path=excel_path,
        )
        print_recommendations_summary(
            all_recommendations,
            csv_path,
            excel_path,
            html_path,
            tenant_name=report_tenant_name,
            evidence_bundle=evidence_bundle,
        )
        if open_html_report and html_path:
            opened = open_html_report_file(html_path)
            if opened:
                print("HTML report opened in your default browser.")
    else:
        print_recommendations_summary(
            all_recommendations,
            tenant_name=report_tenant_name,
            evidence_bundle=evidence_bundle,
        )
    
    return {'html_path': html_path if all_recommendations else None,
            'excel_path': excel_path if all_recommendations else None,
            'csv_path': csv_path if all_recommendations else None,
            'evidence_bundle': evidence_bundle}
