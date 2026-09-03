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
    return all_recommendations


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
                                      dspm_report_paths=None, data_exposure_enabled=True):
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
        all_recommendations = evidence_bundle['recommendations']
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
            tenant_name=report_tenant_name
        )
        if open_html_report and html_path:
            opened = open_html_report_file(html_path)
            if opened:
                print("HTML report opened in your default browser.")
    else:
        print_recommendations_summary(all_recommendations, tenant_name=report_tenant_name)
    
    print("\n" + "="*80)
