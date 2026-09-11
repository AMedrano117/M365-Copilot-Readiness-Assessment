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
                                      connection_results=None):
    """Process all service information and generate recommendations."""
    # Unpack M365 results
    (m365_info, m365_recommendations) = m365_result
    
    print("\n" + "="*80)
    
    from .data_exposure_assessment import build_data_exposure_assessment
    data_exposure_info = build_data_exposure_assessment(
        sam_report_paths=sam_report_paths,
        dspm_report_paths=dspm_report_paths,
        enabled=data_exposure_enabled,
    )
    if data_exposure_info.get('operator_messages'):
        from .spinner import get_timestamp
        for message in data_exposure_info['operator_messages']:
            print(f"[{get_timestamp()}] ℹ️  {message}")

    # Collect all recommendations
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
        providers = load_provider_evidence(provider_evidence)
        foundation = summarize_readiness(all_recommendations)
        conclusions = assess_provider_and_use_cases(profile, providers, foundation['decision'])
        evidence_bundle['assessment_profile'] = profile
        evidence_bundle['provider_evidence'] = providers
        evidence_bundle['conclusions'] = conclusions

        m365_client = m365_info.get('_client') if isinstance(m365_info, dict) else None
        entra_client = entra_info.get('_client') if isinstance(entra_info, dict) else None
        source_statuses = {}
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
        if m365_client:
            source_statuses.update({f'm365_{key}': value for key, value in (getattr(m365_client, 'collection_status', {}) or {}).items()})
            sharepoint_governance = getattr(m365_client, 'sharepoint_governance', {}) or {}
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
        from .connection_validation import connection_status_to_availability
        for result in connection_results or []:
            source_statuses[f"connection_{result.get('collector_id', 'unknown')}"] = {
                'availability_status': connection_status_to_availability(result.get('status')),
                'records_collected': '',
                'pages_collected': '',
                'truncated': False,
                'reason': result.get('reason', ''),
                'maturity': result.get('maturity', ''),
                'evidence_purpose': result.get('evidence_purpose', ''),
            }
        evidence_bundle['source_statuses'] = source_statuses
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
            },
            enabled_collectors or [],
            source_statuses,
        )
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
            })
            print(f"Snapshot JSON: {snapshot_path}")
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
    
    print("\n" + "="*80)
