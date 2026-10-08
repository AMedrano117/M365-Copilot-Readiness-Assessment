"""
Module for exporting recommendations to CSV, Excel, and HTML.
"""
import csv
import json
import os
import re
import webbrowser
from datetime import datetime
from html import escape
from pathlib import Path
from .auth_plan import collected_with_label, next_better_option
from .copilot_readiness_import import FLAG_COLUMNS


PREFERRED_STATUS_ORDER = [
    "Critical",
    "Action Required",
    "Attention Required",
    "Warning",
    "PendingInput",
    "PendingActivation",
    "Not Assessed",
    "Success",
    "Unknown",
]
EXCEL_MAX_DATA_ROWS = 1_048_575

from .new_recommendation import (  # noqa: F401  (re-exported)
    NOT_ASSESSED_STATUS,
    CATEGORY_SCAN_COVERAGE,
)
from .assessment_model import (
    DISPOSITION_ACTION,
    DISPOSITION_ASSURANCE,
    DISPOSITION_COVERAGE,
    DISPOSITION_OPPORTUNITY,
    enrich_assessment_records,
    summarize_readiness,
)

RECOMMENDATION_EXPORT_FIELDS = [
    "RecommendationId",
    "Service",
    "Disposition",
    "ReadinessStage",
    "ImpactArea",
    "AIApplicability",
    "Category",
    "Feature",
    "AlsoLicensedVia",
    "Status",
    "Priority",
    "Observation",
    "Recommendation",
    "LinkText",
    "LinkUrl",
    "EvidenceAvailable",
    "EvidenceSheet",
    "EvidenceSummary",
    "EvidenceBasis",
    "Confidence",
]


def summarize_finding(observation, max_length=110):
    """Condense an observation into a scannable card heading.

    Cards were titled by licence, so a reader scanning the Entra section saw "Microsoft Entra ID
    P1" six times over six unrelated findings (Conditional Access coverage, MFA enrolment,
    passwordless adoption, group-based licensing...). The licence is still shown, as a subtitle;
    the heading now says what was actually found.
    """
    text = str(observation or "").strip()
    if not text:
        return ""

    # Several observations open with a headline followed by a blank line and a breakdown
    # ("Copilot Security Posture: NOT READY\n\n1 Critical Gaps: ..."). Keep the headline.
    first_block = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    if first_block.strip():
        text = first_block

    # Drop markdown emphasis and collapse whitespace so headings stay on one line.
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    # First sentence, avoiding common abbreviations and decimals that contain a period.
    match = re.search(r"(?<![A-Z0-9])\.(?=\s+[A-Z(])", text)
    if match:
        text = text[:match.start()].strip()

    if len(text) > max_length:
        cut = text[:max_length].rsplit(" ", 1)[0].rstrip(" ,;:-")
        text = f"{cut}…"
    return text


def customer_coverage_label(record):
    """Give decision-limiting evidence gaps a plain-language customer label."""
    service = str(record.get("Service", "") or "")
    feature = str(record.get("Feature", "") or record.get("Service", "") or "Required evidence")
    text = f"{service} {feature}".lower()
    if "sharepoint sharing and oversharing" in text:
        return "SharePoint sharing settings and DAG status"
    if "intune" in text or "managed device" in text:
        return "Endpoint access-control model"
    if "data governance evidence" in text:
        return "Purview DLP and governance configuration"
    if service == "Data Exposure" and "sharepoint" in text:
        return "SharePoint Data Access Governance report"
    if service == "Data Exposure" and ("sensitive-data" in text or "dspm" in text):
        return "Purview DSPM data-risk assessment"
    return feature


def sanitize_filename_component(value):
    """Convert labels into short readable filename components."""
    from .export_paths import customer_folder_name
    return customer_folder_name(value) if value else ''


def build_report_filename(extension, tenant_name=None, *, customer_name=None, tenant_id=None):
    """Include a short customer label; the assessment date stays in the folder."""
    from .export_paths import REPORT_STEM, report_file_stem
    stem = report_file_stem(REPORT_STEM, customer_name=customer_name,
                            tenant_name=tenant_name, tenant_id=tenant_id)
    return f"{stem}.{extension}"


def _default_report_filename(folder, extension, tenant_name=None):
    """Preserve earlier direct exports when a caller does not provide a name."""
    name = Path(build_report_filename(extension, tenant_name))
    candidate, number = name.name, 2
    from .workbook_layout import technical_workbook_path
    while (folder / candidate).exists() or (extension == 'xlsx' and technical_workbook_path(folder / candidate).exists()):
        candidate = f'{name.stem} ({number}){name.suffix}'
        number += 1
    return candidate


def sort_values_with_preferences(values, preferred_order):
    """Sort values using a preferred order first, then alphabetically."""
    preferred_positions = {value.lower(): index for index, value in enumerate(preferred_order)}
    unique_values = []
    seen = set()

    for value in values:
        normalized = str(value or "").strip() or "Unknown"
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_values.append(normalized)

    return sorted(
        unique_values,
        key=lambda value: (
            preferred_positions.get(value.lower(), len(preferred_positions)),
            value.lower(),
        ),
    )


def _autosize_columns(ws, headers):
    for index, header in enumerate(headers, start=1):
        max_length = len(str(header or ""))
        column_letter = ws.cell(row=1, column=index).column_letter
        for cell in ws[column_letter]:
            cell_value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, min(len(cell_value), 80))
        ws.column_dimensions[column_letter].width = min(max_length + 2, 60)


def _apply_excel_sheet_formatting(ws, headers, header_fill, header_font, wrap_alignment):
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = wrap_alignment

    ws.freeze_panes = "A2"
    if ws.max_row > 1 and ws.max_column > 0:
        ws.auto_filter.ref = ws.dimensions

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = wrap_alignment

    _autosize_columns(ws, headers)


def _safe_table_name(name):
    sanitized = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "Table"))
    sanitized = sanitized.strip("_") or "Table"
    if sanitized[0].isdigit():
        sanitized = f"T_{sanitized}"
    return sanitized[:200]


def _add_excel_table(ws, table_name):
    if ws.max_row <= 1 or ws.max_column <= 0:
        return

    from openpyxl.worksheet.table import Table, TableStyleInfo

    # A table supplies its own AutoFilter. Keeping a worksheet AutoFilter over the
    # same range creates duplicate filter definitions that openpyxl tolerates but
    # some desktop Excel builds report as a workbook repair/corruption condition.
    ws.auto_filter.ref = None
    base_name = _safe_table_name(table_name)
    used_names = {name.casefold() for sheet in ws.parent for name in sheet.tables}
    unique_name = base_name
    suffix = 2
    while unique_name.casefold() in used_names:
        unique_name = f"{base_name}_{suffix}"
        suffix += 1
    table = Table(displayName=unique_name, ref=ws.dimensions)
    style = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    table.tableStyleInfo = style
    ws.add_table(table)


_OPTIONAL_EXCEL_TEXT = {
    'observation', 'recommendation', 'supporting observation', 'qualification',
    'reason', 'completion evidence', 'description', 'details', 'evidence summary',
    'evidencesummary', 'what we found', 'recommended action', 'extracted text',
    'summary', 'rationale', 'limitations', 'value', 'investigation qualification',
}
_NARRATIVE_EXCEL_SHEETS = {
    'Action Plan', 'Recommendations', 'Evidence Index', 'Assessment Summary',
    'Domain Results', 'Run Manifest', 'Evidence Observations', 'Collection Coverage',
    'PDF Extracted Text', 'PDF Highlights',
}


def _utf16_chunks(value, limit=32000):
    encoded = value.encode('utf-16-le')
    while encoded:
        boundary = min(len(encoded), limit * 2)
        if boundary < len(encoded) and 0xD800 <= int.from_bytes(encoded[boundary - 2:boundary], 'little') <= 0xDBFF:
            boundary -= 2
        yield encoded[:boundary].decode('utf-16-le')
        encoded = encoded[boundary:]


def _excel_safe_rows(rows, sheet_title):
    """Preserve every long field through adjacent continuation columns."""
    output = []
    for original in rows:
        row = {}
        extra_columns = {}
        continued = []
        for header, value in original.items():
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if not isinstance(value, str) or len(value.encode('utf-16-le')) // 2 <= 32767:
                row[header] = value
                continue
            chunks = list(_utf16_chunks(value))
            row[header] = chunks[0]
            for index, chunk in enumerate(chunks[1:], 2):
                continuation = f'{header} (continued {index})'
                while continuation in original or continuation in extra_columns:
                    continuation += '_'
                extra_columns[continuation] = chunk
            continued.append(str(header))
        row.update(extra_columns)
        if continued:
            note = 'Full values continue in adjacent numbered columns: ' + '; '.join(continued)
            row['Excel Export Note'] = (str(row.get('Excel Export Note') or '') + '\n' + note).strip()
        output.append(row)
    return output

def export_to_csv(recommendations, filename=None, tenant_name=None, output_dir=None):
    """
    Export recommendations to CSV file
    
    Args:
        recommendations: List of recommendation dictionaries
        filename: Output filename (optional, generates timestamp-based name if not provided)
    
    Returns:
        str: Path to created CSV file
    """
    recommendations = enrich_assessment_records(recommendations)

    from .export_paths import report_directory
    recommendations_dir = report_directory(output_dir, tenant_name=tenant_name)
    
    if not filename:
        filename = _default_report_filename(recommendations_dir, 'csv', tenant_name)
    
    if not filename.endswith('.csv'):
        filename += '.csv'
    
    # Build full path
    filepath = recommendations_dir / filename
    
    if not recommendations:
        print("No recommendations to export.")
        return None

    # Define CSV headers
    fieldnames = RECOMMENDATION_EXPORT_FIELDS

    with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
        # Recommendation dicts carry internal fields (EvidenceKey) that are not exported.
        # Without extrasaction='ignore' DictWriter raises on every row.
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(recommendations)
    
    return str(filepath)

def export_to_json(recommendations, filename=None, tenant_name=None, output_dir=None):
    """
    Export recommendations to JSON file
    
    Args:
        recommendations: List of recommendation dictionaries
        filename: Output filename (optional, generates timestamp-based name if not provided)
    
    Returns:
        str: Path to created JSON file
    """
    recommendations = enrich_assessment_records(recommendations)

    from .export_paths import report_directory
    recommendations_dir = report_directory(output_dir, tenant_name=tenant_name)
    
    if not filename:
        filename = _default_report_filename(recommendations_dir, 'json', tenant_name)
    
    if not filename.endswith('.json'):
        filename += '.json'
    
    # Build full path
    filepath = recommendations_dir / filename
    
    if not recommendations:
        print("No recommendations to export.")
        return None
    
    with open(filepath, 'w', encoding='utf-8') as jsonfile:
        json.dump(recommendations, jsonfile, indent=2, ensure_ascii=False)
    
    print(f"Recommendations exported to JSON: {filepath}")
    return str(filepath)

def _append_dict_rows_to_sheet(ws, rows, header_fill, header_font, wrap_alignment, table_name=None):
    if not rows:
        return
    if len(rows)>EXCEL_MAX_DATA_ROWS:
        base=ws.title
        used={title.casefold() for title in ws.parent.sheetnames}
        for offset in range(0,len(rows),EXCEL_MAX_DATA_ROWS):
            number=offset//EXCEL_MAX_DATA_ROWS+1
            part=ws
            if number>1:
                suffix=number
                title=base[:31-len(f' {suffix}')]+f' {suffix}'
                while title.casefold() in used:
                    suffix+=1
                    title=base[:31-len(f' {suffix}')]+f' {suffix}'
                used.add(title.casefold())
                part=ws.parent.create_sheet(title)
            _append_dict_rows_to_sheet(part,rows[offset:offset+EXCEL_MAX_DATA_ROWS],header_fill,header_font,wrap_alignment,
                table_name=(table_name or base)+f'Part{number}')
        return

    rows = _excel_safe_rows(rows, ws.title)
    headers = list(dict.fromkeys(header for row in rows for header in row))
    ws.append(headers)
    for cell in ws[1]:
        if isinstance(cell.value, str):
            cell.data_type = "s"
    for row_number, row in enumerate(rows, start=2):
        values = [json.dumps(row.get(header), ensure_ascii=False, sort_keys=True) if isinstance(row.get(header), (dict, list, tuple)) else row.get(header, "") for header in headers]
        ws.append(values)
        # Evidence can include user-supplied CSV text. Preserve it as text rather
        # than allowing an imported value to become an executable Excel formula.
        for column, value in enumerate(values, start=1):
            if isinstance(value, str) and value.startswith("="):
                ws.cell(row=row_number, column=column).data_type = "s"

    _apply_excel_sheet_formatting(ws, headers, header_fill, header_font, wrap_alignment)
    _add_excel_table(ws, table_name or ws.title)


def _rollout_progress_rows(result):
    return [
        {'Stage': stage.get('label'), 'Stage status': stage.get('status'),
         'Requirement': requirement.get('title'), 'Status': requirement.get('status'),
         'Reason': requirement.get('reason'),
         'Action IDs': '; '.join(str(value) for value in requirement.get('action_ids', [])),
         'Control ID': requirement.get('control_id', ''),
         'Responsible role': requirement.get('owner_role', '')}
        for stage in (result.get('rollout_progress') or {}).get('stages', [])
        for requirement in stage.get('requirements', [])
    ]


def _readiness_review_rows(result):
    review = result.get('readiness_review') or {}
    rows = []
    for name, label in (('pilot_plan', 'Pilot plan'), ('pilot_outcomes', 'Pilot outcomes'),
                        ('expansion_approval', 'Expansion approval')):
        value = review.get(name)
        if isinstance(value, dict) and value:
            rows.append({'Review type': label, **value})
    for name, label in (('control_reviews', 'Control review'), ('pilot_conditions', 'Pilot condition')):
        rows.extend({'Review type': label, **value} for value in review.get(name, []) if isinstance(value, dict))
    if review.get('errors'):
        rows.append({'Review type': 'Validation', 'Status': review.get('status'), 'Errors': review['errors']})
    return rows


def _prior_sheet_title(workbook, original_title):
    """Keep historical tabs identifiable while satisfying Excel's naming rules."""
    clean_title = re.sub(r"[\\/*?:\[\]]", "_", str(original_title or "Evidence")).strip(" '")
    base_title = f"Prior {clean_title or 'Evidence'}"
    used_titles = {title.casefold() for title in workbook.sheetnames}
    title = base_title[:31]
    suffix = 2
    while title.casefold() in used_titles:
        ending = f" ({suffix})"
        title = f"{base_title[:31 - len(ending)]}{ending}"
        suffix += 1
    return title


def _append_prior_report_workbook(workbook, prior, header_fill, header_font, wrap_alignment):
    """Preserve earlier report evidence separately from this build's decisions."""
    if not prior.get("available"):
        return
    source_title = _prior_sheet_title(workbook, "Report Sources")
    source_sheet = workbook.create_sheet(source_title)
    source_rows = []
    prior_sheets = dict(prior.get("sheets") or {})
    existing = {str(title).casefold() for title in prior_sheets}
    if "recommendations" not in existing and prior.get("recommendations"):
        prior_sheets["Recommendations"] = {"rows": prior["recommendations"]}
    if "collection coverage" not in existing and prior.get("collection_coverage"):
        prior_sheets["Collection Coverage"] = {"rows": prior["collection_coverage"]}
    for original_title, original_sheet in prior_sheets.items():
        title = _prior_sheet_title(workbook, original_title)
        sheet = workbook.create_sheet(title)
        _append_dict_rows_to_sheet(
            sheet, original_sheet.get("rows") or [], header_fill, header_font,
            wrap_alignment, table_name=title,
        )
        source_rows.append({
            "Source File": prior.get("source_file", ""),
            "Original Report Date": prior.get("generated_at") or "Not established",
            "Original Sheet": original_title,
            "New Workbook Tab": title,
            "Evidence Context": "Historical report; tenant controls were not recollected.",
        })
    if not source_rows:
        source_rows.append({
            "Source File": prior.get("source_file", ""),
            "Original Report Date": prior.get("generated_at") or "Not established",
            "Original Sheet": "", "New Workbook Tab": "",
            "Evidence Context": "Historical report contains no importable rows.",
        })
    _append_dict_rows_to_sheet(
        source_sheet, source_rows, header_fill, header_font, wrap_alignment,
        table_name=source_title,
    )


def _adoption_guidance_rows(result):
    """30/60/90-day guidance; advisory only and never a control result."""
    from .adoption_guidance import build_adoption_guidance, guidance_rows
    numbers = {row.get('RecommendationId'): index for index, row in enumerate(result.get('actions') or [], 1)}
    return guidance_rows(build_adoption_guidance(result, numbers))


def export_to_excel(recommendations, filename=None, tenant_name=None, evidence_bundle=None, output_dir=None):
    """
    Export recommendations to Excel file
    Requires openpyxl: pip install openpyxl
    
    Args:
        recommendations: List of recommendation dictionaries
        filename: Output filename (optional, generates timestamp-based name if not provided)
    
    Returns:
        str: Path to created Excel file
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        print("Warning: openpyxl not installed. Install it with: pip install openpyxl")
        print("Falling back to CSV export...")
        csv_filename = str(Path(filename).with_suffix('.csv')) if filename else None
        return export_to_csv(recommendations, csv_filename, tenant_name=tenant_name, output_dir=output_dir)

    from .assessment_result import build_assessment_result
    from .investigation_details import prepare_investigation_details
    from .customer_report import _heading
    from .workbook_evidence import technical_recommendation_rows
    evidence_bundle = evidence_bundle if evidence_bundle is not None else {}
    result = evidence_bundle.get('assessment_result')
    if result is None:
        result = build_assessment_result(recommendations, evidence_bundle)
        evidence_bundle['assessment_result'] = result
    from .assessment_references import require_valid_assessment
    require_valid_assessment(result)
    from .assessment_runs import add_run_manifest_context
    add_run_manifest_context(evidence_bundle, result)
    prepare_investigation_details(evidence_bundle, result)
    if not evidence_bundle.get('finding_evidence'):
        from .evidence_selection import build_evidence_selection
        from .finding_evidence import build_finding_evidence
        from .workbook_evidence import add_evidence_sheets
        model = build_finding_evidence(build_evidence_selection(result, evidence_bundle, tenant_name=tenant_name))
        evidence_bundle['finding_evidence'] = model
        add_evidence_sheets(evidence_bundle, result, model)
    from .raw_evidence import MAX_DATA_ROWS, split_evidence_sheets
    # Evidence sheets added after preparation are split with the same configured capacity.
    split_evidence_sheets(evidence_bundle, result, evidence_bundle.get('worksheet_row_capacity', MAX_DATA_ROWS))
    recommendations = result['recommendations']

    from .export_paths import report_directory
    recommendations_dir = report_directory(output_dir, tenant_name=tenant_name)
    
    if not filename:
        filename = _default_report_filename(recommendations_dir, 'xlsx', tenant_name)
    
    if not filename.endswith('.xlsx'):
        filename += '.xlsx'
    
    # Build full path
    filepath = recommendations_dir / filename
    
    if not recommendations:
        print("No recommendations to export.")
        return None
    
    # Create workbook. The first tabs answer "what should we do and why?"; the
    # full legacy register remains available under its established name.
    wb = Workbook()
    ws = wb.active
    ws.title = "Action Plan"

    # Style headers
    header_fill = PatternFill(start_color="0066CC", end_color="0066CC", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    wrap_alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)

    # Priority colors
    priority_colors = {
        "High": "FF6B6B",
        "Medium": "FFD93D",
        "Low": "95E1D3"
    }

    action_rows = [{
        "Action": index,
        "RecommendationId": rec.get("RecommendationId", ""),
        "Domain": rec.get("Domain", ""),
        "Type": rec.get("ActionType", ""),
        "Priority": rec.get("Priority", ""),
        "Finding": _heading(rec),
        "Evidence": rec.get('InvestigationRange') or rec.get('InvestigationStatus') or 'Supporting location not supplied',
        "What We Found": rec.get("Observation", ""),
        "Recommended Action": rec.get("Recommendation", ""),
        "Responsible Role": rec.get("OwnerRole", ""),
        "Rollout Stage": rec.get("ReadinessStage", ""),
        "Target Date": "",
        "Completion Evidence": rec.get("CompletionEvidence", ""),
        "Observed": rec.get("ObservationDate", ""),
        "Qualification": " ".join(str(value) for value in (rec.get("Qualification"), rec.get("InvestigationQualification")) if value),
        "Investigation Details": (rec.get("InvestigationSummary") or rec.get("InvestigationStatus", ""))
            if rec.get('InvestigationCount') else ': '.join(dict.fromkeys(str(value) for value in
                (rec.get('InvestigationStatus'), rec.get('InvestigationSummary')) if value)),
    } for index, rec in enumerate(result['actions'], 1)]
    _append_dict_rows_to_sheet(ws, action_rows or [{
        "Priority": "", "What We Found": "No deployment actions were identified from the evidence collected.",
        "Recommended Action": "Continue monitoring the tenant as conditions and intended AI use cases change.",
        "Owner": "", "Target Date": "", "Completion Evidence": "",
    }], header_fill, header_font, wrap_alignment, table_name="ActionPlan")

    if evidence_bundle:
        # License availability belongs in Service Plan Inventory and the compatibility
        # Recommendations register. It is not source evidence for a control decision.
        evidence_index = [{
            'RecommendationId': row.get('RecommendationId', ''),
            'Domain': row.get('Domain', ''), 'Finding': row.get('Feature', ''),
            'Original Date': row.get('ObservationDate', ''),
            'Evidence Basis': row.get('EvidenceBasis', ''), 'Confidence': row.get('Confidence', ''),
            'Qualification': ' '.join(str(value) for value in (row.get('Qualification'), row.get('InvestigationQualification')) if value),
            'Investigation Status': row.get('InvestigationStatus', ''),
            'Supporting Records': row.get('InvestigationCount', ''),
            'Workbook Tab': row.get('EvidenceSheet', ''),
            'Source File': row.get('SourceFile', ''),
            'Supporting Observation': row.get('Evidence') or row.get('Observation', ''),
        } for row in recommendations]
        evidence_index.extend([
            row for row in evidence_bundle.get('evidence_index', [])
            if str(row.get('Evidence Basis', row.get('EvidenceBasis', ''))).lower() != 'license signal'
            and row.get('RecommendationId') not in {r.get('RecommendationId') for r in recommendations}
        ])
        if evidence_index:
            index_sheet = wb.create_sheet("Evidence Index")
            _append_dict_rows_to_sheet(index_sheet, evidence_index, header_fill, header_font, wrap_alignment, table_name="EvidenceIndex")

        coverage_rows = []
        collection_context = evidence_bundle.get("collection_context", {}) or {}
        migration = collection_context.get('methodology_migration')
        if migration:
            coverage_rows.append({
                'Source': 'Methodology compatibility migration', 'State': 'migrated',
                'Records': '', 'Pages': '', 'Truncated': '',
                'Refresh Date': collection_context.get('collected_at', ''),
                'Freshness': '',
                'Reason': f"{migration['from']} -> {migration['to']}: {migration['reason']}",
                'Source File': collection_context.get('source_file', ''), 'Age (Days)': '',
            })
        if collection_context.get("mode") == "offline":
            coverage_rows.append({
                "Source": "Offline report build", "State": "offline",
                "Records": "", "Pages": "", "Truncated": "",
                "Refresh Date": collection_context.get("collected_at", ""),
                "Freshness": collection_context.get("freshness", "Unknown"),
                "Reason": collection_context.get("scope", "") or "Tenant controls were not collected during this report build.",
                "Source File": collection_context.get("source_file", ""),
                "Age (Days)": collection_context.get("age_days", ""),
            })
        for source, state in (evidence_bundle.get('source_statuses', {}) or {}).items():
            state = state if isinstance(state, dict) else {"availability_status": str(state)}
            coverage_rows.append({
                "Source": source,
                "State": state.get("availability_status", state.get("status", "unknown")),
                "Records": state.get("records_collected", state.get("record_count", "")),
                "Pages": state.get("pages_collected", ""),
                "Truncated": state.get("truncated", False),
                "Refresh Date": state.get("refresh_date", ""),
                "Freshness": state.get("freshness", ""),
                "Reason": state.get("reason", ""),
                "Collected With": collected_with_label(state) if not str(source).startswith(("connection_", "pipeline_")) else "",
                "Coverage": state.get("coverage", ""),
                "Evidence Quality": state.get("evidence_quality", ""),
                "How To Unlock": state.get("unlock", ""),
            })
        for dataset_id, entry in ((evidence_bundle.get('auth_plan') or {}).get('datasets') or {}).items():
            if entry.get("selected_path"):
                continue
            # Datasets no auth path could reach are listed with the unlock step.
            coverage_rows.append({
                "Source": f"plan_{dataset_id}", "State": "not_collected", "Records": "", "Pages": "",
                "Truncated": "", "Refresh Date": "", "Freshness": "",
                "Reason": "; ".join(item.get("reason", "") for item in entry.get("skipped", []) if item.get("reason"))[:500],
                "Collected With": "", "Coverage": "none", "Evidence Quality": "",
                "How To Unlock": next_better_option(entry),
            })
        for key, detail in (evidence_bundle.get('sheets') or {}).items():
            notes = [detail.get('reconciliation_note'), detail.get('unavailability_reason'), *(detail.get('details') or [])]
            if not any(notes):
                continue
            source_row = next(iter(detail.get('rows') or []), {})
            coverage_rows.append({
                'Source': detail.get('title', key),
                'State': 'available' if detail.get('rows') else 'unavailable',
                'Records': len(detail.get('rows') or []),
                'Pages': detail.get('pages_collected', source_row.get('Pages Collected', '')),
                'Truncated': detail.get('truncated', source_row.get('Truncated', '')),
                'Refresh Date': detail.get('collected_at') or source_row.get('Collected At') or collection_context.get('collected_at', ''),
                'Freshness': detail.get('freshness', ''),
                'Source File': detail.get('source_file') or source_row.get('Source File', ''),
                'Reason': '\n'.join(dict.fromkeys(str(note) for note in notes if note)),
            })
        if not coverage_rows:
            coverage_rows.append({
                "Source": "Collection metadata", "State": "unavailable", "Records": "",
                "Pages": "", "Truncated": "", "Refresh Date": "", "Freshness": "",
                "Reason": "This export call did not receive collection metadata.",
            })
        coverage_sheet = wb.create_sheet("Collection Coverage")
        _append_dict_rows_to_sheet(coverage_sheet, coverage_rows, header_fill, header_font, wrap_alignment, table_name="CollectionCoverage")

        action_evidence = set()
        for rec in recommendations:
            if rec.get("Disposition") == DISPOSITION_ACTION:
                action_evidence.update(key.strip().lower() for key in str(rec.get("EvidenceKey", "")).split(";") if key.strip())
                evidence_titles = {title.strip().lower() for title in str(rec.get("EvidenceSheet", "")).split(";") if title.strip()}
                for key, sheet in evidence_bundle.get('sheets', {}).items():
                    if str(sheet.get('title', '')).strip().lower() in evidence_titles:
                        action_evidence.add(key)
        for key, sheet in evidence_bundle.get('sheets', {}).items():
            if (key not in action_evidence and sheet.get('title') != 'Investigation Items'
                    and not key.startswith('declared_investigation.')) or not sheet.get('rows'):
                continue
            detail_sheet = wb.create_sheet(sheet.get('title', 'Evidence'))
            _append_dict_rows_to_sheet(detail_sheet, sheet['rows'], header_fill, header_font, wrap_alignment, table_name=sheet.get('title', 'Evidence'))

    ws = wb.create_sheet("Recommendations")
    # Add full compatibility register.
    summary_headers = [
        "RecommendationId",
        "Control ID",
        "Methodology Version",
        "Finding Fingerprint",
        "Service",
        "Disposition",
        "Readiness Stage",
        "Impact Area",
        "AI Applicability",
        "Category",
        "Feature",
        "Also Licensed Via",
        "Status",
        "Priority",
        "Observation",
        "Recommendation",
        "Link Text",
        "Link URL",
        "Evidence Available",
        "Evidence Sheet",
        "Evidence Summary",
        "Evidence Basis",
        "Confidence",
        "Investigation Status",
        "Supporting Records",
        "Investigation Qualification",
    ]
    summary_rows = []
    for rec in recommendations:
        row = [
            rec.get("RecommendationId", ""),
            rec.get("ControlId", ""),
            rec.get("MethodologyVersion", ""),
            rec.get("FindingFingerprint", ""),
            rec.get("Service", ""),
            rec.get("Disposition", ""),
            rec.get("ReadinessStage", ""),
            rec.get("ImpactArea", ""),
            rec.get("AIApplicability", ""),
            rec.get("Category", ""),
            rec.get("Feature", ""),
            rec.get("AlsoLicensedVia", ""),
            rec.get("Status", ""),
            rec.get("Priority", ""),
            rec.get("Observation", ""),
            rec.get("Recommendation", ""),
            rec.get("LinkText", ""),
            rec.get("LinkUrl", ""),
            rec.get("EvidenceAvailable", ""),
            rec.get("EvidenceSheet", ""),
            rec.get("EvidenceSummary", ""),
            rec.get("EvidenceBasis", ""),
            rec.get("Confidence", ""),
            rec.get('InvestigationStatus', ''),
            rec.get('InvestigationCount', ''),
            rec.get('InvestigationQualification', ''),
        ]
        summary_rows.append(dict(zip(summary_headers, row)))

    _append_dict_rows_to_sheet(ws, summary_rows, header_fill, header_font, wrap_alignment, table_name="Recommendations")
    for row_num, rec in enumerate(recommendations, 2):
        priority = rec.get("Priority", "")
        if priority in priority_colors:
            priority_cell = ws.cell(row=row_num, column=14)  # Priority column
            priority_cell.fill = PatternFill(start_color=priority_colors[priority], 
                                            end_color=priority_colors[priority], 
                                            fill_type="solid")

    # Create control, decision, reproducibility, and remaining evidence sheets.
    if evidence_bundle:
        integrity_issues = evidence_bundle.get('integrity', {}).get('issues', [])
        integrity_rows = (
            [{"Status": "Failed", "Issue": issue} for issue in integrity_issues]
            if integrity_issues
            else [{"Status": "Passed", "Issue": "No pre-export integrity issues were detected."}]
        )
        for title, rows, table_name in (
            ("Control Results", evidence_bundle.get('control_results') or result.get('control_results', []), "ControlResults"),
            ("Provider Register", evidence_bundle.get('provider_evidence', {}).get('rows', []), "ProviderRegister"),
            ("Use Case Readiness", evidence_bundle.get('conclusions', {}).get('use_cases', []), "UseCaseReadiness"),
            ("Improvement Tracking", evidence_bundle.get('baseline_comparison', {}).get('rows', []), "ImprovementTracking"),
            ("Run Manifest", evidence_bundle.get('run_manifest', {}).get('rows', []), "RunManifest"),
            ("Integrity Checks", integrity_rows, "IntegrityChecks"),
        ):
            if rows:
                extra_sheet = wb.create_sheet(title)
                _append_dict_rows_to_sheet(extra_sheet, rows, header_fill, header_font, wrap_alignment, table_name=table_name)

        existing_titles = set(wb.sheetnames)
        for key, sheet in evidence_bundle.get('sheets', {}).items():
            sheet_rows = sheet.get('rows', [])
            if not sheet_rows or sheet.get('title', 'Evidence') in existing_titles:
                continue
            detail_sheet = wb.create_sheet(sheet.get('title', 'Evidence'))
            _append_dict_rows_to_sheet(
                detail_sheet,
                sheet_rows,
                header_fill,
                header_font,
                wrap_alignment,
                table_name=sheet.get('title', 'Evidence'),
            )

    if evidence_bundle:
        _append_prior_report_workbook(
            wb, evidence_bundle.get("prior_report") or {}, header_fill,
            header_font, wrap_alignment,
        )

    from .portal_insights import workbook_highlights
    for title, rows in (
        ('Findings Register', [{'RecommendationId':row.get('RecommendationId'),
            'Finding': _heading(row),
            'Priority':row.get('Priority'), 'Disposition':row.get('Disposition'),
            'Readiness effect':row.get('PilotImpact'),
            'Supporting evidence':row.get('InvestigationRange') or row.get('InvestigationStatus'),
            'Raw source evidence':next(iter(row.get('RawEvidenceRanges',[])),''),
            'Assessment domain':row.get('AssessmentDomainId'), 'Control':row.get('ControlId'),
            'Original feature':row.get('Feature'), 'Observation':row.get('Observation'),
            'Responsible role':row.get('OwnerRole'),
            'Evidence level':row.get('EvidenceLevel'), 'Operational result':row.get('OperationalResult'),
            'Observed':row.get('ObservationDate'), 'Qualification':row.get('Qualification'),
            'Guidance verification':row.get('GuidanceVerification'), 'Guidance verified at':row.get('GuidanceVerifiedAt'),
            'Guidance scope':row.get('GuidanceVerificationScope'),
            'Supporting ranges':row.get('InvestigationRanges',[]),
            'Raw source ranges':row.get('RawEvidenceRanges',[]),
            'Detail qualification':row.get('InvestigationQualification'),
            'Raw qualification':row.get('RawEvidenceQualification'),
            **({'Evidence kind':row.get('EvidenceKind'), 'Evidence availability':row.get('EvidenceAvailability'),
                'Detail records':row.get('DetailRecords'), 'Record unit':row.get('RecordUnit'),
                'Affected entities':row.get('AffectedEntities'), 'Entity unit':row.get('EntityUnit'),
                'Supporting context records':row.get('ContextRecords'),
                'Evidence records':row.get('EvidenceRecordsRange') or row.get('InvestigationRange')
                    or next(iter(row.get('RawEvidenceRanges') or []), '') or row.get('EvidenceAvailability'),
                'Evidence record ranges':row.get('EvidenceRecordsRanges',[]),
                'Technical fix':(row.get('TechnicalGuidance') or {}).get('guidance_status'),
                'Technical evidence page':(evidence_bundle.get('technical_evidence_pages') or {}).get(row.get('RecommendationId'))}
               if 'EvidenceKind' in row else {})}
            for row in sorted(result['recommendations'],key=lambda item:(
                {'Action':0,'Coverage':1,'Assurance':2,'Opportunity':3,'Reference':4}.get(item.get('Disposition'),5),
                {'High':0,'Medium':1,'Low':2}.get(item.get('Priority'),3),_heading(item)))]),
        ('Technical Recommendations', technical_recommendation_rows(result, heading=_heading)
         if any('EvidenceKind' in row for row in result['recommendations']) else []),
        ('Assessment Coverage', result.get('domain_coverage',[])),
        ('Device Reconciliation', (result.get('device_reconciliation') or {}).get('records',[])),
        ('Device Coverage', [{key:value for key,value in (result.get('device_reconciliation') or {}).items() if key!='records'}]),
        ('Assessment Context', [{'Field':key,'Value':value} for key,value in result.get('assessment_context',{}).items()]),
        ('Executive Summary', [{'Item':key,'Value':value} for key,value in result.get('executive_summary',{}).items()]),
        ('Source Windows', result.get('source_windows',[])),
        ('Documentation Verification',result.get('documentation_verification',[])),
        ('Assessment Summary', [{'Item': 'Deployment decision', 'Value': result['decision']},
            {'Item': 'Rationale', 'Value': result['rationale']},
            {'Item': 'Evaluation date', 'Value': result.get('evaluation_date')},
            *[{'Item': key, 'Value': value} for key, value in result['counts'].items()]]),
        ('Domain Results', [{'Domain': row['title'], 'Status': row.get('status'),
            'Summary': row.get('summary'), 'Actions': row.get('action_count'),
            'Responsible Role': row.get('owner_role')} for row in result.get('assessment_domains',result['domains'])]),
        ('Evidence Observations', result.get('observations', result.get('evidence', []))),
        ('Adoption Metrics', result.get('adoption_metrics', [])),
        ('Import Receipt', evidence_bundle.get('import_receipt', [])),
        ('Portal Review', (evidence_bundle.get('portal_review') or {}).get('rows', [])),
        ('PDF Highlights', workbook_highlights(evidence_bundle.get('portal_review'))),
        ('PDF Extracted Text', [{'Source File': capture.get('source_name'), 'Page': page['page'],
                                'Method': page['method'], 'Extracted text': page['text'],
                                'Qualification': capture.get('qualification'), 'Capture ID': capture.get('id')}
                               for capture in (evidence_bundle.get('portal_review') or {}).get('captures', [])
                               for page in capture.get('extracted_pages', [])]),
        ('Copilot Admin Data', (evidence_bundle.get('copilot_admin_review') or {}).get('rows', [])),
        ('Copilot DLP Detail', ((evidence_bundle.get('copilot_admin_review') or {}).get('dlp') or {}).get('detail', [])),
        ('Portal Review Remaining', (evidence_bundle.get('copilot_admin_review') or {}).get('manual_checks', [])),
        ('Rollout Progress', _rollout_progress_rows(result)),
        ('Readiness Reviews', _readiness_review_rows(result)),
        ('Adoption Guidance', _adoption_guidance_rows(result)),
    ):
        if rows:
            sheet = wb.create_sheet(title)
            _append_dict_rows_to_sheet(sheet, rows, header_fill, header_font, wrap_alignment, table_name=title)

    # Every action has a navigable evidence reference, including historical checks.
    if 'Evidence Index' in wb:
        index_sheet = wb['Evidence Index']
        headers = {cell.value: cell.column for cell in index_sheet[1]}
        prior_tabs = {}
        if 'Prior Report Sources' in wb:
            mapping = list(wb['Prior Report Sources'].values)
            for values in mapping[1:]:
                row = dict(zip(mapping[0], values))
                prior_tabs[row.get('Original Sheet')] = row.get('New Workbook Tab')
        historical_ids = {r.get('RecommendationId') for r in recommendations if r.get('SourceType') == 'prior_assessment'}
        for i in range(2, index_sheet.max_row + 1):
            if index_sheet.cell(i, headers['RecommendationId']).value in historical_ids:
                cell = index_sheet.cell(i, headers['Workbook Tab'])
                original_titles = str(cell.value or '').split(';')
                targets = [prior_tabs[t.strip()] for t in original_titles if t.strip() in prior_tabs]
                cell.value = '; '.join(targets) or prior_tabs.get('Recommendations', '')
        for i in range(2, index_sheet.max_row + 1):
            cell = index_sheet.cell(i, headers['Workbook Tab'])
            candidates = str(cell.value or '').split(';')
            target = next((item.strip() for item in candidates if item.strip() in wb.sheetnames), None)
            if target:
                cell.hyperlink = "#'" + target.replace("'", "''") + "'!A1"
                cell.style = 'Hyperlink'

    # Specific investigations are a compact optional addition to the original
    # evidence register. A missing detail target never suppresses the workbook.
    from openpyxl.comments import Comment
    from openpyxl.utils.cell import range_boundaries
    exact_range = re.compile(r"^'((?:[^']|'')+)'!([A-Z]+[1-9][0-9]*:[A-Z]+[1-9][0-9]*)$")
    investigations = {row.get('RecommendationId'): row for row in recommendations}
    def valid_location(location):
        match = exact_range.fullmatch(str(location or ''))
        if not match:
            return False
        target_title = match.group(1).replace("''", "'")
        if target_title not in wb:
            return False
        first_column, first_row, last_column, last_row = range_boundaries(match.group(2))
        target_sheet = wb[target_title]
        return (first_row >= 2 and first_row <= last_row <= target_sheet.max_row
                and 1 <= first_column <= last_column <= target_sheet.max_column)

    for title, column_name in (('Action Plan', 'Investigation Details'), ('Action Plan', 'Evidence'),
                               ('Findings Register','Supporting evidence'), ('Findings Register','Raw source evidence'),
                               ('Evidence Index', 'Workbook Tab'), ('Recommendations', 'Evidence Sheet'),
                               ('Evidence Index', 'Supporting Records'), ('Recommendations', 'Supporting Records'),
                               ('Findings Register', 'Evidence records')):
        if title not in wb:
            continue
        sheet = wb[title]
        columns = {cell.value: cell.column for cell in sheet[1]}
        if column_name not in columns or 'RecommendationId' not in columns:
            continue
        for row_number in range(2, sheet.max_row + 1):
            rec = investigations.get(sheet.cell(row_number, columns['RecommendationId']).value)
            if not rec:
                continue
            cell = sheet.cell(row_number, columns[column_name])
            if title == 'Action Plan' and rec.get('InvestigationNote'):
                cell.comment = Comment(str(rec['InvestigationNote']), 'Assessment')
            if column_name == 'Evidence records':
                location = str(cell.value or '')
            else:
                location = str(next(iter(rec.get('RawEvidenceRanges',[])),'') if column_name=='Raw source evidence' else rec.get('InvestigationRange') or '')
            if not valid_location(location):
                continue
            if column_name in {'Workbook Tab', 'Evidence Sheet'}:
                cell.value = location
            cell.hyperlink = '#' + location
            cell.style = 'Hyperlink'

    # Technical fixes link back to their findings and out to the verified Microsoft documentation.
    if 'Technical Recommendations' in wb and 'Findings Register' in wb:
        fixes, register = wb['Technical Recommendations'], wb['Findings Register']
        fix_columns = {cell.value: cell.column for cell in fixes[1]}
        register_columns = {cell.value: cell.column for cell in register[1]}
        register_rows = {register.cell(number, register_columns['RecommendationId']).value: number
                         for number in range(2, register.max_row + 1)}
        fix_rows = {fixes.cell(number, fix_columns['RecommendationId']).value: number for number in range(2, fixes.max_row + 1)}
        for number in range(2, fixes.max_row + 1):
            identifier = fixes.cell(number, fix_columns['RecommendationId']).value
            if identifier in register_rows:
                cell = fixes.cell(number, fix_columns['RecommendationId'])
                cell.hyperlink = f"#'Findings Register'!A{register_rows[identifier]}"
                cell.style = 'Hyperlink'
            for index in range(1, 4):
                column = fix_columns.get(f'Documentation {index}')
                url = fixes.cell(number, column).value if column else None
                if isinstance(url, str) and url.startswith('https://'):
                    fixes.cell(number, column).hyperlink = url
                    fixes.cell(number, column).style = 'Hyperlink'
        if 'Technical fix' in register_columns:
            for identifier, number in register_rows.items():
                if identifier in fix_rows:
                    cell = register.cell(number, register_columns['Technical fix'])
                    cell.hyperlink = f"#'Technical Recommendations'!A{fix_rows[identifier]}"
                    cell.style = 'Hyperlink'

    # The compact worklist points to the actual records behind each item,
    # including separate grant rows for a single application.
    for sheet in wb:
        columns = {cell.value: cell.column for cell in sheet[1]}
        if 'Source Detail' not in columns:
            continue
        for row_number in range(2, sheet.max_row + 1):
            cell = sheet.cell(row_number, columns['Source Detail'])
            location = str(cell.value or '')
            if valid_location(location):
                cell.hyperlink = '#' + location
                cell.style = 'Hyperlink'

    # All imported strings, including register cells and headers, remain inert.
    for sheet in wb:
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'

    from .workbook_layout import split_workbook_layout, technical_workbook_path
    technical_path = technical_workbook_path(filepath)
    assessment, technical = split_workbook_layout(wb, result, evidence_bundle, filepath, technical_path,
                                                  tenant_name=tenant_name)
    assessment.save(filepath)
    technical.save(technical_path)
    return str(filepath)

def export_to_html(recommendations, filename=None, tenant_name=None, evidence_bundle=None, excel_path=None, output_dir=None, summary_filename=None, technical_excel_path=None):
    """Render the same assessed result used by the workbook and operator receipt."""
    from .assessment_result import build_assessment_result
    from .investigation_details import prepare_investigation_details
    from .customer_report import render_customer_report
    bundle = evidence_bundle if evidence_bundle is not None else {}
    result = bundle.get('assessment_result')
    if result is None:
        result = build_assessment_result(recommendations, bundle)
        bundle['assessment_result'] = result
    from .assessment_references import require_valid_assessment
    require_valid_assessment(result)
    from .assessment_runs import add_run_manifest_context
    add_run_manifest_context(bundle, result)
    prepare_investigation_details(bundle, result)
    from .export_paths import report_directory
    folder = report_directory(output_dir, tenant_name=tenant_name)
    name = filename or _default_report_filename(folder, 'html', tenant_name)
    if not name.endswith('.html'):
        name += '.html'
    path = folder / name
    summary_path = path.with_name(summary_filename or path.stem + ' Summary.html')
    technical_excel_path = technical_excel_path or (bundle.get('technical_excel_path') if excel_path else None)
    path.write_text(render_customer_report(result, bundle, tenant_name, excel_path, summary_path=summary_path,
                                          technical_workbook_path=technical_excel_path), encoding='utf-8')
    # A short pilot-readiness summary beside the full report; both come from the same result.
    from .pilot_summary import render_pilot_summary
    summary_path.write_text(render_pilot_summary(result, bundle, tenant_name, path, excel_path,
                                               technical_workbook_path=technical_excel_path), encoding='utf-8')
    bundle['summary_html_path'] = str(summary_path)
    return str(path)


def open_html_report(html_path):
    """
    Open the generated HTML report in the default browser.
    
    Args:
        html_path: Filesystem path to HTML report
    
    Returns:
        bool: True if browser launch was requested successfully
    """
    if not html_path:
        return False
    
    try:
        report_uri = Path(html_path).resolve().as_uri()
        return webbrowser.open(report_uri)
    except Exception as e:
        print(f"Warning: Unable to open HTML report automatically: {e}")
        return False

def print_recommendations_summary(
    recommendations,
    csv_path=None,
    excel_path=None,
    html_path=None,
    tenant_name=None,
    evidence_bundle=None,
):
    """
    Print a summary of recommendations grouped by service and priority
    
    Args:
        recommendations: List of recommendation dictionaries
        csv_path: Path to CSV export (optional)
        excel_path: Path to Excel export (optional)
        html_path: Path to HTML export (optional)
    """
    if not recommendations:
        print("\nℹ️  No recommendations generated")
        return
    
    from .console_reporting import detail, display_path, is_verbose, section, status, style, print_source_gaps
    section('ASSESSMENT')
    if tenant_name:
        print(style(tenant_name, 'important'))
    
    recommendations = enrich_assessment_records(recommendations)
    from .assessment_result import build_assessment_result
    readiness = (evidence_bundle or {}).get('assessment_result') or build_assessment_result(recommendations, evidence_bundle or {})
    findings = readiness['actions']
    opportunities = readiness['opportunities']
    assurances = readiness['strengths']
    coverage_items = readiness['coverage']

    # Group by priority
    high_priority = [r for r in findings if r.get("Priority") == "High"]
    medium_priority = [r for r in findings if r.get("Priority") == "Medium"]
    low_priority = [r for r in findings if r.get("Priority") == "Low"]
    tone = 'success' if readiness['decision'] == 'Ready for a controlled pilot' else 'error' if readiness['decision'] == 'Not ready for pilot' else 'warning'
    progress = readiness.get('rollout_progress') or {}
    if progress:
        status(f"Rollout stage: {progress['current_stage']}", tone)
        checks = progress.get('counts') or {}
        status(f"Required controls: {checks.get('established_controls', 0)} established | "
               f"{checks.get('observed_issues', 0)} with issues | {checks.get('unconfirmed_controls', 0)} unconfirmed")
    status(f"Deployment decision: {readiness['decision']}", tone)
    detail(readiness['rationale'])
    counts = readiness['counts']
    print(f"Actions: {len(findings)} — {counts['remediation']} remediation, {counts['confirmation']} confirmation, {counts['evidence_gaps']} evidence checks")
    if counts.get('critical'):
        status(f"Critical priority: {counts['critical']}", 'error')
    print('Priority: ' + style(f'{len(high_priority)} high', 'error' if high_priority else 'muted')
          + ' | ' + style(f'{len(medium_priority)} medium', 'warning' if medium_priority else 'muted')
          + ' | ' + style(f'{len(low_priority)} low', 'info'))
    detail(f"Service-plan/context items (workbook): {len(opportunities)}")
    strength_count = len(assurances)
    status(f"Supported observations: {strength_count}", 'success' if strength_count else 'muted')
    if coverage_items:
        detail(f"Coverage gaps: {len(coverage_items)} (included in the action total)")

    if is_verbose():
        section('Actions by assessment area')
    for domain in readiness['domains']:
        count = domain.get('action_count', 0)
        if count and is_verbose():
            action_label = "action" if count == 1 else "actions"
            print(f"  • {domain['title']}: {count} {action_label}")

    context = (evidence_bundle or {}).get('collection_context') or {}
    print_source_gaps((evidence_bundle or {}).get('source_statuses'),
                      mode=context.get('mode', 'live'), collected_at=context.get('collected_at'))
    captures = ((evidence_bundle or {}).get('portal_review') or {}).get('captures') or []
    if captures:
        pages = sum(max(len(capture.get('extracted_pages') or []), len(capture.get('previews') or []))
                    for capture in captures)
        status(f'Admin-center captures: {len(captures)} captures, {pages} pages. Open Admin pages in the HTML report.')
    if html_path or excel_path or csv_path:
        section('OUTPUT FILES')
        summary_path = (evidence_bundle or {}).get('summary_html_path')
        for label, path in (('Summary', summary_path), ('HTML', html_path), ('Assessment workbook', excel_path),
                            ('Technical evidence workbook', (evidence_bundle or {}).get('technical_excel_path')), ('CSV', csv_path),
                            ('Technical evidence pages', (evidence_bundle or {}).get('html_evidence_index'))):
            if path:
                print(f"{label}: {style(display_path(path), 'path')}")
