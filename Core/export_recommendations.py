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

from .new_recommendation import (  # noqa: F401  (re-exported)
    NOT_ASSESSED_STATUS,
    CATEGORY_SCAN_COVERAGE,
)
from .assessment_model import (
    CROSS_PLATFORM_MANUAL_CHECKS,
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


def sanitize_filename_component(value):
    """Convert tenant names into filesystem-friendly filename segments."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    safe = safe.strip("._-").lower()
    return safe[:80]


def build_report_filename(extension, tenant_name=None):
    """Build a timestamped report filename, optionally including tenant name."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tenant_slug = sanitize_filename_component(tenant_name)
    if tenant_slug:
        return f"m365_recommendations_{tenant_slug}_{timestamp}.{extension}"
    return f"m365_recommendations_{timestamp}.{extension}"


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
    table = Table(displayName=_safe_table_name(table_name), ref=ws.dimensions)
    style = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    table.tableStyleInfo = style
    ws.add_table(table)

def export_to_csv(recommendations, filename=None, tenant_name=None):
    """
    Export recommendations to CSV file
    
    Args:
        recommendations: List of recommendation dictionaries
        filename: Output filename (optional, generates timestamp-based name if not provided)
    
    Returns:
        str: Path to created CSV file
    """
    recommendations = enrich_assessment_records(recommendations)

    # Create Reports folder if it doesn't exist
    recommendations_dir = Path("Reports")
    recommendations_dir.mkdir(exist_ok=True)
    
    if not filename:
        filename = build_report_filename("csv", tenant_name=tenant_name)
    
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

def export_to_json(recommendations, filename=None, tenant_name=None):
    """
    Export recommendations to JSON file
    
    Args:
        recommendations: List of recommendation dictionaries
        filename: Output filename (optional, generates timestamp-based name if not provided)
    
    Returns:
        str: Path to created JSON file
    """
    recommendations = enrich_assessment_records(recommendations)

    # Create Reports folder if it doesn't exist
    recommendations_dir = Path("Reports")
    recommendations_dir.mkdir(exist_ok=True)
    
    if not filename:
        filename = build_report_filename("json", tenant_name=tenant_name)
    
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

    headers = list(rows[0].keys())
    ws.append(headers)
    for row in rows:
        ws.append([row.get(header, "") for header in headers])

    _apply_excel_sheet_formatting(ws, headers, header_fill, header_font, wrap_alignment)
    _add_excel_table(ws, table_name or ws.title)


def export_to_excel(recommendations, filename=None, tenant_name=None, evidence_bundle=None):
    """
    Export recommendations to Excel file
    Requires openpyxl: pip install openpyxl
    
    Args:
        recommendations: List of recommendation dictionaries
        filename: Output filename (optional, generates timestamp-based name if not provided)
    
    Returns:
        str: Path to created Excel file
    """
    recommendations = enrich_assessment_records(recommendations)

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        print("Warning: openpyxl not installed. Install it with: pip install openpyxl")
        print("Falling back to CSV export...")
        return export_to_csv(recommendations, filename, tenant_name=tenant_name)
    
    # Create Reports folder if it doesn't exist
    recommendations_dir = Path("Reports")
    recommendations_dir.mkdir(exist_ok=True)
    
    if not filename:
        filename = build_report_filename("xlsx", tenant_name=tenant_name)
    
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
        "Priority": rec.get("Priority", ""),
        "What We Found": rec.get("Observation", ""),
        "Recommended Action": rec.get("Recommendation", ""),
        "Owner": "",
        "Target Date": "",
        "Completion Evidence": "",
    } for rec in recommendations if rec.get("Disposition") == DISPOSITION_ACTION]
    _append_dict_rows_to_sheet(ws, action_rows or [{
        "Priority": "", "What We Found": "No deployment actions were identified from the evidence collected.",
        "Recommended Action": "Continue monitoring the tenant as conditions and intended AI use cases change.",
        "Owner": "", "Target Date": "", "Completion Evidence": "",
    }], header_fill, header_font, wrap_alignment, table_name="ActionPlan")

    if evidence_bundle:
        # License availability belongs in Service Plan Inventory and the compatibility
        # Recommendations register. It is not source evidence for a control decision.
        evidence_index = [
            row for row in evidence_bundle.get('evidence_index', [])
            if str(row.get('Evidence Basis', row.get('EvidenceBasis', ''))).lower() != 'license signal'
        ]
        if evidence_index:
            index_sheet = wb.create_sheet("Evidence Index")
            _append_dict_rows_to_sheet(index_sheet, evidence_index, header_fill, header_font, wrap_alignment, table_name="EvidenceIndex")

        coverage_rows = []
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
            if key not in action_evidence or not sheet.get('rows'):
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
    ]
    ws.append(summary_headers)
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
        ]
        ws.append(row)

        # Color code priority
        priority = rec.get("Priority", "")
        if priority in priority_colors:
            row_num = ws.max_row
            priority_cell = ws.cell(row=row_num, column=14)  # Priority column
            priority_cell.fill = PatternFill(start_color=priority_colors[priority], 
                                            end_color=priority_colors[priority], 
                                            fill_type="solid")

    _apply_excel_sheet_formatting(ws, summary_headers, header_fill, header_font, wrap_alignment)
    _add_excel_table(ws, "Recommendations")

    # Create control, decision, reproducibility, and remaining evidence sheets.
    if evidence_bundle:
        integrity_issues = evidence_bundle.get('integrity', {}).get('issues', [])
        integrity_rows = (
            [{"Status": "Failed", "Issue": issue} for issue in integrity_issues]
            if integrity_issues
            else [{"Status": "Passed", "Issue": "No pre-export integrity issues were detected."}]
        )
        for title, rows, table_name in (
            ("Control Results", evidence_bundle.get('control_results', []), "ControlResults"),
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

    # Save workbook
    wb.save(filepath)
    return str(filepath)

def export_to_html(recommendations, filename=None, tenant_name=None, evidence_bundle=None, excel_path=None):
    """
    Export recommendations to a readable standalone HTML report.
    
    Args:
        recommendations: List of recommendation dictionaries
        filename: Output filename (optional, generates timestamp-based name if not provided)
    
    Returns:
        str: Path to created HTML file
    """
    recommendations = enrich_assessment_records(recommendations)

    recommendations_dir = Path("Reports")
    recommendations_dir.mkdir(exist_ok=True)
    
    if not filename:
        filename = build_report_filename("html", tenant_name=tenant_name)
    
    if not filename.endswith('.html'):
        filename += '.html'
    
    filepath = recommendations_dir / filename
    
    if not recommendations:
        print("No recommendations to export.")
        return None
    
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tenant_label = str(tenant_name or "Tenant name unavailable")
    workbook_label = Path(excel_path).name if excel_path else ""
    # Keep unlike things in unlike lanes.  A healthy licensed feature is assurance evidence,
    # not a finding; an adoption idea is not a security gap; and unread data is a coverage limit.
    findings = [r for r in recommendations if r.get("Disposition") == DISPOSITION_ACTION]
    opportunities = [r for r in recommendations if r.get("Disposition") == DISPOSITION_OPPORTUNITY]
    assurances = [
        r for r in recommendations
        if r.get("Disposition") == DISPOSITION_ASSURANCE
        and str(r.get("EvidenceBasis", "")).lower() not in {"license signal", "not verified"}
        and r.get("EvidenceAvailable") == "Yes"
    ]
    coverage_items = [r for r in recommendations if r.get("Disposition") == DISPOSITION_COVERAGE]
    readiness = summarize_readiness(recommendations)

    high_priority = [r for r in findings if r.get("Priority") == "High"]
    medium_priority = [r for r in findings if r.get("Priority") == "Medium"]
    low_priority = [r for r in findings if r.get("Priority") == "Low"]
    not_assessed = coverage_items

    services = {}
    for rec in findings:
        service = rec.get("Service", "Unknown")
        if service not in services:
            services[service] = []
        services[service].append(rec)

    status_values = sort_values_with_preferences(
        [rec.get("Status", "Unknown") for rec in findings],
        PREFERRED_STATUS_ORDER,
    )
    
    def priority_class(priority):
        return {
            "High": "priority-high",
            "Medium": "priority-medium",
            "Low": "priority-low",
        }.get(priority, "priority-unknown")
    
    def status_class(status):
        normalized = (status or "").lower().replace(" ", "-")
        if normalized in {"success", "warning", "attention-required", "action-required", "critical"}:
            return f"status-{normalized}"
        if normalized in {"missing", "pendinginput", "pendingactivation"}:
            return "status-warning"
        if normalized in {"not-assessed", "permission-required", "missing-prerequisite"}:
            return "status-not-assessed"
        return "status-default"
    
    def paragraphize(text):
        safe = escape(naturalize_count_text(text))
        if not safe:
            return "<span class=\"muted\">Not provided</span>"
        # Several recommendation modules use **bold** for emphasis. That markdown is meaningful
        # in the CSV and Excel exports, so it stays in the source text and is rendered here
        # instead - otherwise the asterisks reach the reader verbatim. Applied after escaping,
        # so the inserted tags are the only markup in the result.
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe, flags=re.DOTALL)
        return "<br>".join(safe.splitlines())

    def naturalize_count_text(text):
        """Render legacy count placeholders as ordinary English in the HTML report."""
        # Zero is collected evidence, not a missing value. Preserve it so tables never
        # turn a real count of zero into "Not provided".
        raw = "" if text is None else str(text)

        def replace_placeholder(match):
            stem, ending = match.groups()
            nearby = raw[max(0, match.start() - 100):match.start()]
            # The last count in the current phrase normally belongs to this noun, including
            # constructions such as "48 active role assignment schedules".
            phrase = re.split(r"[.;:]|\b(?:and|but|including)\b", nearby, flags=re.IGNORECASE)[-1]
            counts = re.findall(r"\b\d[\d,]*\b", phrase)
            count = int(counts[-1].replace(",", "")) if counts else None
            singular = f"{stem}y" if ending == "ies" else stem
            plural = f"{stem}ies" if ending == "ies" else f"{stem}s"
            return singular if count == 1 else plural

        return re.sub(r"\b([A-Za-z]+)\((s|ies)\)", replace_placeholder, raw)

    def count_label(count, singular, plural=None):
        return singular if count == 1 else (plural or f"{singular}s")

    def short_text(text):
        return escape(str(text or ""))

    def slugify(text):
        normalized = re.sub(r'[^a-z0-9]+', '-', str(text or "").lower()).strip('-')
        return normalized or "section"

    def render_appendix_preview(section):
        preview_rows = section.get("preview_rows", [])
        if not preview_rows:
            return ""

        preview_columns = []
        for column in ["RecommendationId", "Flagged By"]:
            if any(column in row for row in preview_rows):
                preview_columns.append(column)

        for column in section.get("preview_columns", []):
            if any(column in row for row in preview_rows) and column not in preview_columns:
                preview_columns.append(column)

        if not preview_columns:
            return ""

        header_html = "".join(f"<th>{escape(column)}</th>" for column in preview_columns)
        row_html = []
        for row in preview_rows:
            cells = "".join(
                f"<td>{paragraphize(row.get(column, ''))}</td>"
                for column in preview_columns
            )
            row_html.append(f"<tr>{cells}</tr>")

        return (
            "<div class=\"appendix-preview\">"
            "<div class=\"preview-label\">Workbook excerpt</div>"
            "<table class=\"preview-table\">"
            f"<thead><tr>{header_html}</tr></thead>"
            f"<tbody>{''.join(row_html)}</tbody>"
            "</table>"
            "</div>"
        )

    def render_compact_table(rows, columns):
        if not rows:
            return '<p class="muted">None identified.</p>'
        header_html = "".join(f"<th>{escape(label)}</th>" for label, _ in columns)
        body_rows = []
        for row in rows:
            cells = "".join(
                f"<td>{paragraphize(row.get(key, ''))}</td>"
                for _, key in columns
            )
            body_rows.append(f"<tr>{cells}</tr>")
        return (
            '<div class="appendix-preview"><table class="preview-table">'
            f"<thead><tr>{header_html}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody></table></div>"
        )

    ai_usage = evidence_bundle.get("ai_usage", {}) if evidence_bundle else {}
    copilot_usage = ai_usage.get("copilot_usage", {}) or {}
    app_readiness = ai_usage.get("m365_app_readiness", {}) or {}
    dashboard_usage = ai_usage.get("copilot_dashboard", {}) or {}
    shadow_ai_usage = ai_usage.get("shadow_ai_usage", {}) or {}
    license_summary = ai_usage.get("license_summary", {}) or {}
    power_inventory = evidence_bundle.get("power_platform_inventory", {}) if evidence_bundle else {}

    def display_value(value, suffix=""):
        if value is None or value == "":
            return "Not assessed"
        return f"{value}{suffix}"

    def explain_report_periods(value):
        """Translate Graph period codes before they reach customer-facing HTML."""
        text = str(value or "")
        for code, label in (
            ("D180", "Last 180 days"),
            ("D90", "Last 90 days"),
            ("D30", "Last 30 days"),
            ("D28", "Last 28 days"),
            ("D7", "Last 7 days"),
        ):
            text = text.replace(code, label)
        return text

    usage_periods = copilot_usage.get("periods", {}) or {}
    selected_period = copilot_usage.get("selected_period") or next(
        (period for period in ("D28", "D7", "D90", "D180") if period in usage_periods), ""
    )
    selected_usage = usage_periods.get(selected_period, {})
    period_label = explain_report_periods(selected_period).lower() if selected_period else "available period"
    license_coverage = license_summary.get("copilot_license_coverage")
    trend_rows = copilot_usage.get("trend", []) or []
    trend_direction = "Not assessed"
    if len(trend_rows) >= 2:
        first_active = trend_rows[0].get("active_users")
        last_active = trend_rows[-1].get("active_users")
        if first_active is not None and last_active is not None:
            change = last_active - first_active
            trend_direction = "Up" if change > 0 else "Down" if change < 0 else "Flat"

    usage_cards = [
        ("License coverage", display_value(license_coverage, "%") if license_coverage is not None else "Not calculated",
         "Licensed Copilot users as a share of estimated eligible users."),
        (f"Active users — {period_label}", display_value(selected_usage.get("active_users")) if selected_usage else "Not assessed",
         "Users who performed at least one Copilot action."),
        (f"Activation rate — {period_label}", display_value(selected_usage.get("active_rate"), "%") if selected_usage else "Not assessed",
         "Active users as a percentage of enabled users."),
        (f"Prompts — {period_label}", display_value(selected_usage.get("total_prompts")) if selected_usage and selected_usage.get("total_prompts") is not None else "Not provided",
         "Total prompts submitted during the reporting window."),
        ("Daily usage trend", trend_direction if trend_direction != "Not assessed" else "Not provided",
         "Change in daily active users."),
    ]
    usage_card_html = "".join(
        f'<article class="summary-card"><div class="label">{escape(label)}</div>'
        f'<div class="value usage-value">{escape(str(value))}</div>'
        f'<div class="decision-effect">{escape(detail)}</div></article>'
        for label, value, detail in usage_cards
    )

    app_rows = []
    for name, metrics in sorted((selected_usage.get("apps", {}) or {}).items()):
        app_rows.append({
            "Application": name,
            "Enabled": metrics.get("enabled_users", ""),
            "Active": metrics.get("active_users", ""),
            "Active rate": "N/A" if metrics.get("active_rate") is None else f"{metrics.get('active_rate')}%",
        })

    shadow_rows = [{
        "Application": app.get("display_name", "Unknown"),
        "Risk score": app.get("risk_rating", "Unknown"),
        "Active users": app.get("active_users", "Unknown"),
        "Traffic bytes": app.get("traffic_bytes", "Unknown"),
        "Last seen": app.get("last_seen", "Unknown"),
    } for app in (shadow_ai_usage.get("applications", []) or [])]

    source_rows = []
    for label, evidence in (
        ("Microsoft 365 Copilot usage", copilot_usage),
        ("Microsoft 365 Apps readiness", app_readiness),
        ("Copilot Dashboard export", dashboard_usage),
        ("Shadow AI discovery", shadow_ai_usage),
        ("Power Platform inventory", power_inventory),
    ):
        source_rows.append({
            "Source": label,
            "Status": str(evidence.get("availability_status", "available" if evidence.get("available") else "not_requested")).replace("_", " ").title(),
            "As of": evidence.get("refresh_date", "") or "Not provided",
            "Freshness": evidence.get("freshness", "Unknown"),
            "Reason": explain_report_periods(evidence.get("reason", "")),
        })

    adoption_html = f"""
    <section class="scope-panel" id="ai-adoption-usage">
      <h2>AI Adoption &amp; Usage</h2>
      <p>License coverage shows deployment reach. Active users, prompts, and app use show adoption
      and engagement. These metrics are reported separately from security readiness.</p>
      <div class="summary-grid usage-grid">{usage_card_html}</div>
      {render_compact_table(app_rows, [
          ("Application", "Application"), ("Enabled", "Enabled"),
          ("Active", "Active"), ("Active rate", "Active rate"),
      ]) if app_rows else '<p class="muted">Microsoft did not return application-level Copilot activity.</p>'}
      {render_compact_table(shadow_rows, [
          ("Observed external AI", "Application"), ("Risk score", "Risk score"),
          ("Active users", "Active users"), ("Traffic bytes", "Traffic bytes"),
          ("Last seen", "Last seen"),
      ]) if shadow_rows else ''}
      <details>
        <summary>Usage evidence coverage and freshness</summary>
        {render_compact_table(source_rows, [
            ("Source", "Source"), ("Status", "Status"), ("As of", "As of"),
            ("Freshness", "Freshness"), ("Reason", "Reason"),
        ])}
      </details>
    </section>
    """

    opportunity_rows = []
    if selected_usage and selected_usage.get("enabled_users") == 0:
        opportunity_rows.append({
            "Service": "M365",
            "Feature": "Controlled Copilot pilot",
            "Observation": f"The Copilot usage report returned successfully with no enabled users during the {period_label}.",
            "Why": "This is a valid pre-deployment state, so readiness should be tested with a bounded use case rather than judged as low adoption.",
            "Hypothesis": "A narrow cohort whose daily work uses the selected Microsoft 365 apps will produce enough evidence to decide whether the use case should expand.",
            "Recommendation": "Choose a narrow pilot cohort, record a customer-owned quality, cycle-time, or risk baseline, and set the review date and expand/stop threshold before assigning licenses.",
        })
    elif selected_usage:
        unused = selected_usage.get("unused_licenses", 0) or 0
        if unused:
            opportunity_rows.append({
                "Service": "M365", "Feature": "Activate or reassign unused Copilot licenses",
                "Observation": f"{unused} of {selected_usage.get('enabled_users', 0)} enabled users had no intentional Copilot activity during the {period_label}.",
                "Why": "Enabled seats without activity may indicate an enablement, training, or role-fit problem; they do not prove that the product lacks value.",
                "Hypothesis": "Role-specific onboarding and a named task will increase intentional use among currently inactive enabled users.",
                "Recommendation": "Measure active-user rate and the customer-selected task outcome at a defined review date. Expand if both meet the agreed threshold; otherwise adjust the pilot or reassign licenses.",
            })
        if selected_usage.get("active_users", 0):
            active_count = selected_usage.get("active_users", 0)
            active_verb = "was" if active_count == 1 else "were"
            prompt_signal = (
                f" with {selected_usage.get('total_prompts')} prompts submitted"
                if selected_usage.get("total_prompts") is not None else
                "; prompt volume was not supplied by the provider"
            )
            opportunity_rows.append({
                "Service": "M365", "Feature": "Validate sustained engagement",
                "Observation": f"{active_count} {count_label(active_count, 'user')} {active_verb} active during the {period_label}{prompt_signal}.",
                "Why": "Active use confirms initial engagement, but activity volume alone does not establish sustained use or a better business outcome.",
                "Hypothesis": "Users will return when Copilot is attached to a repeatable task with a clear benefit and adequate training.",
                "Recommendation": "Review the usage periods actually returned, collect user feedback, and compare the named task's quality and cycle-time measures with baseline before the expand/stop decision.",
            })
    else:
        period_reason = (copilot_usage.get("period_status", {}).get("D28", {}) or {}).get("reason")
        opportunity_rows.append({
            "Service": "M365", "Feature": "Establish an actual Copilot usage baseline",
            "Observation": f"Copilot activity could not be measured: {explain_report_periods(period_reason or copilot_usage.get('reason') or 'no report data returned')}.",
            "Why": "License assignment cannot show whether people are using Copilot or whether a pilot is producing value.",
            "Hypothesis": "A successfully collected usage baseline will distinguish non-deployment, enablement gaps, and actual engagement.",
            "Recommendation": "Resolve report access or confirm non-deployment, then define the customer-owned engagement and outcome thresholds used for the expand/stop decision.",
        })
    if app_readiness.get("available"):
        opportunity_rows.append({
            "Service": "M365", "Feature": "Select evidence-based pilot cohorts",
            "Observation": app_readiness.get("readiness_signal") or "Microsoft 365 Apps user and platform activity was successfully collected for the last 30 days.",
            "Why": "Existing app use identifies where an in-app AI pilot can fit normal work; it does not by itself prove AI value.",
            "Hypothesis": "A cohort already active in the application required by the use case will encounter fewer adoption barriers.",
            "Recommendation": "Select an app-aligned cohort, measure the use case against its baseline, and expand only if the customer-defined outcome and risk thresholds are met.",
        })
    if dashboard_usage.get("available"):
        opportunity_rows.append({
            "Service": "Viva Insights", "Feature": "Measure returning use and task-level behavior",
            "Observation": f"The supplied Copilot Dashboard export contains {dashboard_usage.get('records', 0)} records.",
            "Why": "Returning-user and action metrics distinguish sustained engagement from one-time experimentation, but still do not prove a business outcome.",
            "Hypothesis": "Use cases with repeat engagement will also show stronger customer-selected quality or cycle-time results.",
            "Recommendation": "Compare returning use and detailed actions with the named outcome and risk baseline, then apply the customer-approved expand/stop threshold.",
        })
    opportunity_rows = opportunity_rows[:5]

    opportunity_html = ""
    if opportunity_rows:
        opportunity_cards = "".join(f"""
          <article class="opportunity-card">
            <h3>{escape(str(row.get('Feature', '') or 'Pilot opportunity'))}</h3>
            <dl>
              <div><dt>Measured tenant signal</dt><dd>{paragraphize(row.get('Observation', ''))}</dd></div>
              <div><dt>Why it matters</dt><dd>{paragraphize(row.get('Why', ''))}</dd></div>
              <div><dt>Pilot hypothesis</dt><dd>{paragraphize(row.get('Hypothesis', ''))}</dd></div>
              <div><dt>Measurement and decision</dt><dd>{paragraphize(row.get('Recommendation', ''))}</dd></div>
            </dl>
          </article>
        """ for row in opportunity_rows)
        opportunity_html = f"""
        <details class="secondary-panel" id="opportunities">
          <summary>Prioritized adoption &amp; value opportunities ({len(opportunity_rows)})</summary>
          <p>These suggestions use tenant activity and licensing to guide pilot selection. Validate
          each with a business owner, a measurable outcome, and an expand/stop decision. They are
          separate from security readiness.</p>
          <div class="opportunity-grid">{opportunity_cards}</div>
        </details>
        """

    action_plan_rows = [{
        "Priority": row.get("Priority", ""),
        "Finding": row.get("Observation", ""),
        "Action": row.get("Recommendation", ""),
    } for row in findings]
    action_plan_html = f"""
    <section class="scope-panel" id="action-plan">
      <h2>Action plan</h2>
      <p>Address these items before expanding AI access. Technical references and supporting evidence are available in the engineering appendix and workbook.</p>
      {render_compact_table(action_plan_rows, [
          ("Priority", "Priority"), ("What we found", "Finding"),
          ("What to do", "Action"),
      ])}
    </section>
    """

    strength_benefits = {
        "Identity & access": "This helps protect Microsoft 365 data when people or AI-connected applications sign in.",
        "Apps, connectors & agents": "This helps limit which applications, agents, and connectors can reach tenant data.",
        "Data exposure & grounding": "This reduces the chance that AI surfaces content more broadly than intended.",
        "Data protection & governance": "This helps preserve data-handling rules when AI works with tenant content.",
        "Endpoint, browser & network": "This helps control how organizational data can move into AI services.",
        "Threat & incident posture": "This improves the tenant's ability to detect and respond to AI-related security events.",
    }
    assurance_rows = [{
        "Area": item.get("ImpactArea", "") or item.get("Service", "Tenant control"),
        "Strength": item.get("Observation", "") or item.get("Feature", "Verified tenant control"),
        "Benefit": strength_benefits.get(
            item.get("ImpactArea", ""),
            "This provides a verified foundation that can be retained as AI access expands.",
        ),
    } for item in assurances]
    assurance_html = ""
    if assurances:
        assurance_html = f"""
        <section class="scope-panel" id="assurances">
          <h2>What the tenant is doing well</h2>
          <p>These are working controls verified from tenant evidence—not simply features included with a license.</p>
          {render_compact_table(assurance_rows, [
              ("Area", "Area"), ("What is working", "Strength"),
              ("Why it helps AI readiness", "Benefit"),
          ])}
        </section>
        """

    conclusions = evidence_bundle.get("conclusions", {}) if evidence_bundle else {}
    provider_rows = conclusions.get("providers", []) or []
    use_case_rows = conclusions.get("use_cases", []) or []
    provider_conclusion = conclusions.get("provider_conclusion", "Not assessed")
    use_case_conclusion = conclusions.get("use_case_conclusion", "Not assessed")
    manual_check_html = render_compact_table(
        CROSS_PLATFORM_MANUAL_CHECKS,
        [("Provider-side check", "control"), ("Evidence to collect", "verify"), ("Risk if missing", "why")],
    )
    scope_html = f"""
    <section class="scope-panel" id="scope-boundary">
      <h2>Three readiness conclusions</h2>
      <p>The foundation, provider, and use-case decisions answer different questions and do not substitute for one another.</p>
      <div class="scope-boundary-grid">
        <article>
          <h3>Microsoft 365 foundation</h3>
          <p><strong>{escape(readiness['decision'])}</strong><br>{escape(readiness['rationale'])}</p>
        </article>
        <article>
          <h3>Provider and tier approval</h3>
          <p><strong>{escape(provider_conclusion)}</strong><br>A current provider register is required for each named product and subscription tier.</p>
        </article>
        <article>
          <h3>Proposed use cases</h3>
          <p><strong>{escape(use_case_conclusion)}</strong><br>Each use case requires an owner, intended users, data scope, action boundaries, approvals, and outcome measures.</p>
        </article>
      </div>
      {render_compact_table(provider_rows, [("Provider", "Provider"), ("Product", "Product"), ("Tier", "Tier"), ("Status", "Approval Status"), ("Reason", "Reason")]) if provider_rows else ''}
      {render_compact_table(use_case_rows, [("Use case", "Use Case"), ("Owner", "Business Owner"), ("Provider", "Provider"), ("Readiness", "Readiness"), ("Reason", "Reason")]) if use_case_rows else ''}
      <details>
        <summary>External AI approval checklist—complete once per product and subscription tier ({len(CROSS_PLATFORM_MANUAL_CHECKS)})</summary>
        <p class="checklist-intro">For each row, record the product and tier reviewed, an owner,
        the evidence location, review date, and a Pass / Fail / Not verified result.</p>
        {manual_check_html}
      </details>
      <p class="reference-row">References:
        <a href="https://learn.microsoft.com/microsoft-365/copilot/secure-govern-copilot-foundational-deployment-guidance" target="_blank" rel="noopener noreferrer">Microsoft secure data foundation</a> ·
        <a href="https://learn.microsoft.com/purview/ai-other-apps" target="_blank" rel="noopener noreferrer">Purview for other AI apps</a> ·
        <a href="https://learn.microsoft.com/entra/identity/conditional-access/concept-conditional-access-cloud-apps" target="_blank" rel="noopener noreferrer">Conditional Access target resources</a>
      </p>
    </section>
    """
    
    service_sections = []
    service_nav_items = []
    for service, recs in sorted(services.items()):
        service_slug = slugify(service)
        service_nav_items.append(
            f'<a class="toc-link" href="#service-{service_slug}">{escape(service)} <span>{len(recs)}</span></a>'
        )
        cards = []
        for rec in recs:
            priority = rec.get("Priority", "Unknown")
            status = rec.get("Status", "Unknown")
            feature = escape(str(rec.get("Feature", "Unknown")))
            link_text = escape(str(rec.get("LinkText", "") or "Learn more"))
            link_url = str(rec.get("LinkUrl", "") or "").strip()
            priority_value = str(priority or "Unknown").strip()
            status_value = str(status or "Unknown").strip()
            recommendation_id = str(rec.get("RecommendationId", "") or "")
            evidence_available = str(rec.get("EvidenceAvailable", "No") or "No")
            evidence_sheet = str(rec.get("EvidenceSheet", "") or "")
            evidence_summary = str(rec.get("EvidenceSummary", "") or "")
            search_blob = " ".join([
                recommendation_id,
                str(service or ""),
                str(rec.get("Feature", "") or ""),
                str(rec.get("Status", "") or ""),
                str(rec.get("Priority", "") or ""),
                str(rec.get("Observation", "") or ""),
                str(rec.get("Recommendation", "") or ""),
                str(rec.get("ImpactArea", "") or ""),
                str(rec.get("AIApplicability", "") or ""),
                evidence_sheet,
                evidence_summary,
            ]).lower()
            link_html = ""
            if link_url:
                safe_url = escape(link_url, quote=True)
                link_html = f'<p class="link-row"><a href="{safe_url}" target="_blank" rel="noopener noreferrer">{link_text}</a></p>'

            evidence_html = ""
            if evidence_available.lower() == "yes" and evidence_sheet:
                tab_label = "tabs" if ";" in evidence_sheet or "," in evidence_sheet else "tab"
                workbook_reference = (
                    f"See <strong>{escape(workbook_label)}</strong>, {tab_label}: <strong>{escape(evidence_sheet)}</strong>."
                    if workbook_label
                    else f"See workbook {tab_label}: <strong>{escape(evidence_sheet)}</strong>."
                )
                evidence_html = f"""
                <section class="evidence-callout">
                  <h4>Engineer Follow-Up</h4>
                  <p>{paragraphize(evidence_summary)}</p>
                  <p class="muted">{workbook_reference}</p>
                </section>
                """
            
            raw_feature = str(rec.get("Feature", "") or "")
            heading_text = naturalize_count_text(summarize_finding(rec.get("Observation", "")) or raw_feature)
            card_heading = escape(heading_text)
            # Only show the licence subtitle when the heading does not already name it.
            feature_subtitle = ""
            if raw_feature and not heading_text.lower().startswith(raw_feature.lower()[:28]):
                feature_subtitle = f'<div class="card-subtitle">{feature}</div>'

            also_licensed = str(rec.get("AlsoLicensedVia", "") or "").strip()
            also_html = ""
            if also_licensed:
                also_html = f"""
                <p class="muted also-licensed">Same condition is also covered by:
                {escape(also_licensed)}</p>
                """

            cards.append(f"""
            <article class="recommendation-card"
              data-service="{escape(str(service), quote=True)}"
              data-priority="{escape(priority_value, quote=True)}"
              data-status="{escape(status_value, quote=True)}"
              data-impact="{escape(str(rec.get('ImpactArea', '') or ''), quote=True)}"
              data-search="{escape(search_blob, quote=True)}">
              <div class="card-header">
                <div>
                  <div class="card-meta">{escape(recommendation_id) if recommendation_id else 'Recommendation'}</div>
                  <h3>{card_heading}</h3>
                  {feature_subtitle}
                </div>
                <div class="badge-row">
                  <span class="badge {priority_class(priority)}">{escape(str(priority))}</span>
                  <span class="badge {status_class(status)}">{escape(str(status))}</span>
                </div>
              </div>
              <div class="assessment-meta">
                <span>{escape(str(rec.get('ReadinessStage', '') or 'Planned improvement'))}</span>
                <span>{escape(str(rec.get('ImpactArea', '') or 'Platform capability'))}</span>
                <span>{escape(str(rec.get('AIApplicability', '') or 'Microsoft 365 data estate'))}</span>
                <span>{escape(str(rec.get('EvidenceBasis', '') or 'Tenant observation'))} / {escape(str(rec.get('Confidence', '') or 'Medium'))} confidence</span>
              </div>
              <div class="card-body">
                <section>
                  <h4>Observation</h4>
                  <p>{paragraphize(rec.get("Observation", ""))}</p>
                </section>
                <section>
                  <h4>Recommendation</h4>
                  <p>{paragraphize(rec.get("Recommendation", ""))}</p>
                  {also_html}
                </section>
                {evidence_html}
                {link_html}
              </div>
            </article>
            """)
        
        service_sections.append(f"""
        <section class="service-section" id="service-{service_slug}" data-service="{escape(str(service), quote=True)}">
          <div class="service-header">
            <div class="service-heading">
              <button class="service-toggle" type="button" aria-expanded="true" aria-controls="service-content-{service_slug}">
                <span class="toggle-icon" aria-hidden="true">▾</span>
                <span class="service-title">{escape(service)}</span>
              </button>
              <span class="service-count" data-default-count="{len(recs)}">{len(recs)} {count_label(len(recs), "recommendation")}</span>
            </div>
          </div>
          <div class="service-content" id="service-content-{service_slug}">
            <div class="recommendation-grid">
              {''.join(cards)}
            </div>
          </div>
        </section>
        """)

    # Scan coverage: what this run could not inspect, and why. Rendered separately from tenant
    # findings so the customer is not asked to action the assessment's own configuration.
    coverage_html = ""
    if coverage_items:
        coverage_rows = []
        for item in coverage_items:
            link = ""
            url = str(item.get("LinkUrl", "") or "").strip()
            if url:
                link = (f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">'
                        f'{escape(str(item.get("LinkText", "") or "Learn more"))}</a>')
            coverage_rows.append(f"""
              <tr>
                <td>{escape(str(item.get("Service", "") or ""))}</td>
                <td>{escape(str(item.get("Feature", "") or ""))}</td>
                <td>{paragraphize(item.get("Observation", ""))}</td>
                <td>{paragraphize(item.get("Recommendation", ""))}</td>
                <td>{link}</td>
              </tr>""")
        coverage_html = f"""
        <section class="coverage-panel">
          <details id="scan-coverage">
            <summary>Scan Coverage ({len(coverage_items)}) — evidence gaps and collection steps</summary>
          <p class="coverage-intro">These areas were not assessed and are excluded from the
          security findings. Complete the listed steps and rerun to close the gaps.</p>
          <div class="appendix-preview">
            <table class="preview-table">
              <thead><tr><th>Service</th><th>Area</th><th>What could not be assessed</th><th>How to resolve</th><th>Reference</th></tr></thead>
              <tbody>{''.join(coverage_rows)}</tbody>
            </table>
          </div>
          </details>
        </section>
        """

    appendix_html = ""
    appendix_sections = list(evidence_bundle.get("appendix_sections", [])) if evidence_bundle else []
    if opportunities:
        opportunity_inventory_rows = [{
            "Service": item.get("Service", ""),
            "Capability": item.get("Feature", ""),
            "Status": item.get("Status", ""),
            "Treatment": "Supporting inventory only; validate against a named use case before promotion.",
        } for item in opportunities]
        appendix_sections.append({
            "key": "opportunity_register",
            "title": "Appendix: Full Service-Plan Opportunity Register",
            "workbook_tab": "Recommendations",
            "summary": f"{len(opportunities)} optional service-plan and enablement {count_label(len(opportunities), 'idea')} for engineering review.",
            "details": [
                "These are possibilities based on licensing or workload context.",
                "Move an item into the customer plan after assigning an owner, baseline, and expand/stop decision.",
            ],
            "preview_columns": ["Service", "Capability", "Status", "Treatment"],
            "preview_rows": opportunity_inventory_rows[:10],
        })
    if appendix_sections:
        appendix_blocks = []
        for section in appendix_sections:
            detail_items = "".join(
                f"<li>{paragraphize(detail)}</li>"
                for detail in section.get("details", [])
            )
            workbook_context = (
                f"{escape(section.get('workbook_tab', ''))} in {escape(workbook_label)}"
                if workbook_label
                else escape(section.get('workbook_tab', ''))
            )
            appendix_blocks.append(f"""
            <section class="appendix-section" id="appendix-{slugify(section.get('key', 'detail'))}">
              <div class="appendix-header">
                <div>
                  <div class="appendix-kicker">Engineer Appendix</div>
                  <h3>{escape(section.get('title', 'Appendix Detail'))}</h3>
                </div>
                <div class="tab-pill">Workbook tab: {workbook_context}</div>
              </div>
              <p class="appendix-summary">{paragraphize(section.get('summary', ''))}</p>
              <ul class="appendix-details">
                {detail_items}
              </ul>
              {render_appendix_preview(section)}
            </section>
            """)

        appendix_html = f"""
        <details class="appendix-panel" id="engineer-appendix">
          <summary class="appendix-intro">
            <span class="appendix-title">Engineer Follow-Up Appendix</span>
            <span class="appendix-summary-copy">{len(appendix_sections)} {count_label(len(appendix_sections), 'evidence section')}.
            Expand for object and configuration details.</span>
          </summary>
          <div class="appendix-content">
            {''.join(appendix_blocks)}
          </div>
        </details>
        """

    integrity = evidence_bundle.get("integrity", {}) if evidence_bundle else {}
    integrity_banner = ""
    if integrity and not integrity.get("valid", True):
        integrity_banner = f"""
        <section class="coverage-panel validation-banner">
          <h2>{escape(integrity.get('banner', 'Validation incomplete—do not use for deployment approval'))}</h2>
          <p class="coverage-intro">The diagnostic report was generated, but {len(integrity.get('issues', []))} integrity issue(s) require review. See the workbook Integrity Checks tab.</p>
        </details>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Enterprise AI Readiness Assessment - Microsoft 365 Data Estate</title>
  <style>
    :root {{
      --bg: #f4f6f8;
      --surface: #ffffff;
      --surface-alt: #eef3f8;
      --text: #17202a;
      --muted: #5b6875;
      --border: #d6dee8;
      --accent: #005a9c;
      --high: #c62828;
      --high-bg: #fdecec;
      --medium: #a15c00;
      --medium-bg: #fff4dd;
      --low: #1f6f50;
      --low-bg: #e7f6ef;
      --success: #0f6cbd;
      --success-bg: #e8f2fb;
      --warning: #8a5a00;
      --warning-bg: #fff4dd;
      --critical: #8b1e3f;
      --critical-bg: #fdebf1;
      --attention: #6b4eff;
      --attention-bg: #f1edff;
      --unassessed: #4a5568;
      --unassessed-bg: #eceff3;
      --shadow: 0 12px 30px rgba(23, 32, 42, 0.08);
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      background: linear-gradient(180deg, #eaf2fb 0%, var(--bg) 35%, #f7f9fb 100%);
      color: var(--text);
      line-height: 1.5;
    }}

    .page {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}

    .hero {{
      background: linear-gradient(135deg, #0d3b66 0%, #005a9c 55%, #2f80ed 100%);
      color: white;
      border-radius: 24px;
      padding: 32px;
      box-shadow: var(--shadow);
    }}

    .hero h1 {{
      margin: 0 0 8px;
      font-size: clamp(2rem, 4vw, 3rem);
      line-height: 1.1;
    }}

    .hero p {{
      margin: 0;
      max-width: 760px;
      color: rgba(255, 255, 255, 0.88);
    }}

    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin: 24px 0 32px;
    }}

    .summary-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 20px;
      box-shadow: var(--shadow);
    }}

    .summary-card .label {{
      color: var(--muted);
      font-size: 0.92rem;
      margin-bottom: 8px;
    }}

    .summary-card .value {{
      font-size: 2rem;
      font-weight: 700;
      line-height: 1;
    }}

    .summary-card .usage-value {{
      font-size: 1.45rem;
      overflow-wrap: anywhere;
    }}

    .usage-grid {{
      margin-bottom: 18px;
    }}

    .opportunity-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 16px;
      margin-top: 18px;
    }}

    .opportunity-card {{
      border: 1px solid var(--border);
      border-radius: 14px;
      background: var(--surface-alt);
      padding: 18px;
    }}

    .opportunity-card h3 {{
      margin: 0 0 14px;
      font-size: 1.05rem;
    }}

    .opportunity-card dl,
    .opportunity-card dd {{
      margin: 0;
    }}

    .opportunity-card dl > div + div {{
      margin-top: 12px;
    }}

    .opportunity-card dt {{
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      margin-bottom: 3px;
      text-transform: uppercase;
    }}

    .summary-card .decision-effect {{
      margin-top: 9px;
      color: var(--muted);
      font-size: 0.78rem;
      line-height: 1.3;
    }}

    .service-section {{
      margin-top: 28px;
      scroll-margin-top: 140px;
    }}

    .service-header {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
      margin-bottom: 14px;
    }}

    .service-heading {{
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}

    .service-toggle {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      border: none;
      background: transparent;
      color: var(--text);
      padding: 0;
      cursor: pointer;
      font: inherit;
    }}

    .service-toggle:hover {{
      text-decoration: underline;
    }}

    .toggle-icon {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      background: var(--surface-alt);
      transition: transform 0.2s ease;
    }}

    .service-section.is-collapsed .toggle-icon {{
      transform: rotate(-90deg);
    }}

    .service-title {{
      margin: 0;
      font-size: 1.5rem;
      font-weight: 700;
    }}

    .service-header span {{
      color: var(--muted);
      font-size: 0.95rem;
    }}

    .recommendation-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 18px;
    }}

    .recommendation-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 20px;
      box-shadow: var(--shadow);
    }}

    .controls-panel {{
      position: sticky;
      top: 12px;
      z-index: 10;
      background: rgba(255, 255, 255, 0.9);
      backdrop-filter: blur(10px);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 18px;
      box-shadow: var(--shadow);
      margin-bottom: 24px;
    }}

    .controls-grid {{
      display: grid;
      grid-template-columns: 2fr 1fr 1fr 1fr minmax(220px, auto);
      gap: 12px;
      align-items: end;
    }}

    .control-group {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}

    .control-group label {{
      font-size: 0.84rem;
      color: var(--muted);
      font-weight: 600;
    }}

    .control-group input,
    .control-group select,
    .control-button {{
      width: 100%;
      min-height: 44px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      padding: 10px 12px;
      font: inherit;
    }}

    .control-button {{
      cursor: pointer;
      font-weight: 700;
      background: var(--surface-alt);
    }}

    .control-actions {{
      display: flex;
      gap: 10px;
    }}

    .control-actions .control-button {{
      flex: 1 1 auto;
    }}

    .control-button:hover {{
      background: #dde8f3;
    }}

    .filter-meta {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-top: 14px;
      color: var(--muted);
      font-size: 0.92rem;
    }}

    .toc-panel {{
      margin-bottom: 24px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 18px;
      box-shadow: var(--shadow);
    }}

    .toc-panel h2 {{
      margin: 0 0 12px;
      font-size: 1rem;
    }}

    .toc-grid {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}

    .toc-link {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      padding: 8px 12px;
      background: var(--surface-alt);
      color: var(--text);
      font-size: 0.9rem;
      font-weight: 600;
    }}

    .toc-link span {{
      color: var(--muted);
      font-size: 0.82rem;
    }}

    .empty-state {{
      display: none;
      margin-top: 20px;
      padding: 24px;
      background: var(--surface);
      border: 1px dashed var(--border);
      border-radius: 18px;
      text-align: center;
      color: var(--muted);
      box-shadow: var(--shadow);
    }}

    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 18px;
    }}

    .card-header h3 {{
      margin: 0;
      font-size: 1.1rem;
      line-height: 1.3;
    }}

    .badge-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 0.8rem;
      font-weight: 700;
      white-space: nowrap;
    }}

    .priority-high {{
      background: var(--high-bg);
      color: var(--high);
    }}

    .priority-medium {{
      background: var(--medium-bg);
      color: var(--medium);
    }}

    .priority-low {{
      background: var(--low-bg);
      color: var(--low);
    }}

    .priority-unknown {{
      background: var(--surface-alt);
      color: var(--muted);
    }}

    .status-success {{
      background: var(--success-bg);
      color: var(--success);
    }}

    .status-warning {{
      background: var(--warning-bg);
      color: var(--warning);
    }}

    .status-critical {{
      background: var(--critical-bg);
      color: var(--critical);
    }}

    .status-attention-required {{
      background: var(--attention-bg);
      color: var(--attention);
    }}

    .status-action-required {{
      background: var(--critical-bg);
      color: var(--critical);
    }}

    .card-subtitle {{
      margin-top: 4px;
      font-size: 0.88rem;
      color: var(--muted);
      font-weight: 600;
    }}

    .also-licensed {{
      margin-top: 10px !important;
      font-size: 0.86rem;
    }}

    .status-not-assessed {{
      background: var(--unassessed-bg);
      color: var(--unassessed);
      border: 1px dashed var(--unassessed);
    }}

    .status-default {{
      background: var(--surface-alt);
      color: var(--muted);
    }}

    .card-body section + section {{
      margin-top: 16px;
    }}

    .assessment-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: -4px 0 16px;
    }}

    .assessment-meta span {{
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--surface-alt);
      color: var(--muted);
      padding: 4px 8px;
      font-size: 0.76rem;
      font-weight: 600;
    }}

    .card-body h4 {{
      margin: 0 0 6px;
      font-size: 0.84rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }}

    .card-body p {{
      margin: 0;
    }}

    .link-row {{
      margin-top: 18px !important;
    }}

    .card-meta {{
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 6px;
      font-weight: 700;
    }}

    .evidence-callout {{
      border: 1px solid #c8d8eb;
      background: #f3f8fd;
      border-radius: 14px;
      padding: 14px 16px;
      margin-top: 18px;
    }}

    .coverage-panel {{
      margin-top: 32px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-left: 4px solid var(--unassessed);
      border-radius: 20px;
      padding: 24px;
      box-shadow: var(--shadow);
    }}

    .decision-panel,
    .scope-panel,
    .secondary-panel {{
      margin-top: 24px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 22px;
      box-shadow: var(--shadow);
    }}

    .decision-panel {{
      border-left: 5px solid var(--accent);
    }}

    .decision-panel h2,
    .scope-panel h2 {{
      margin: 0 0 8px;
    }}

    .decision-panel p,
    .scope-panel > p,
    .secondary-panel > p {{
      margin: 0;
      color: var(--muted);
    }}

    details > summary {{
      cursor: pointer;
      font-weight: 700;
    }}

    .scope-panel details {{
      margin-top: 16px;
    }}

    .analysis-impact-note,
    .scope-decision-note {{
      margin: 16px 0;
      padding: 14px 16px;
      border-left: 4px solid var(--accent);
      border-radius: 10px;
      background: var(--surface-alt);
      color: var(--text);
      line-height: 1.55;
    }}

    .scope-boundary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
      margin-top: 16px;
    }}

    .scope-boundary-grid article {{
      padding: 16px;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: var(--surface-alt);
    }}

    .scope-boundary-grid h3 {{
      margin: 0 0 8px;
      font-size: 1rem;
    }}

    .scope-boundary-grid p,
    .checklist-intro {{
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
    }}

    .checklist-intro {{
      padding-top: 10px;
    }}

    .coverage-panel h2 {{
      margin: 0 0 8px;
    }}

    .coverage-intro {{
      margin: 14px 0 16px;
      color: var(--muted);
      max-width: 900px;
    }}

    .appendix-panel {{
      margin-top: 32px;
    }}

    .appendix-intro {{
      display: flex;
      flex-direction: column;
      gap: 7px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 24px;
      box-shadow: var(--shadow);
    }}

    .appendix-intro:focus-visible {{
      outline: 3px solid rgba(0, 90, 156, 0.3);
      outline-offset: 3px;
    }}

    .appendix-title {{
      color: var(--text);
      font-size: 1.5rem;
      line-height: 1.2;
    }}

    .appendix-summary-copy {{
      color: var(--muted);
      font-weight: 400;
      line-height: 1.5;
    }}

    .appendix-content {{
      display: grid;
      gap: 18px;
      margin-top: 18px;
    }}

    .appendix-section {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 24px;
      box-shadow: var(--shadow);
    }}

    .appendix-header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 12px;
    }}

    .appendix-header h3 {{
      margin: 0;
    }}

    .appendix-kicker {{
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 0.75rem;
      font-weight: 700;
      color: var(--muted);
      margin-bottom: 6px;
    }}

    .tab-pill {{
      background: var(--surface-alt);
      color: var(--accent);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 0.88rem;
      font-weight: 600;
      white-space: nowrap;
    }}

    .appendix-summary {{
      margin: 0 0 12px;
    }}

    .appendix-details {{
      margin: 0 0 16px 18px;
      padding: 0;
      color: var(--muted);
    }}

    .appendix-details li + li {{
      margin-top: 6px;
    }}

    .appendix-preview {{
      margin-top: 16px;
      overflow-x: auto;
    }}

    .preview-label {{
      font-size: 0.82rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 8px;
    }}

    .preview-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
      min-width: 680px;
    }}

    .preview-table th,
    .preview-table td {{
      border: 1px solid var(--border);
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
    }}

    .preview-table th {{
      background: var(--surface-alt);
      color: var(--text);
    }}

    a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
    }}

    a:hover {{
      text-decoration: underline;
    }}

    .muted {{
      color: var(--muted);
    }}

    @media (max-width: 720px) {{
      .hero {{
        padding: 24px;
      }}

      .controls-grid {{
        grid-template-columns: 1fr;
      }}

      .card-header {{
        flex-direction: column;
      }}

      .badge-row {{
        justify-content: flex-start;
      }}

      .filter-meta {{
        flex-direction: column;
      }}

      .appendix-header {{
        flex-direction: column;
      }}

      .tab-pill {{
        white-space: normal;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>Enterprise AI Readiness Assessment</h1>
      <p>Microsoft 365 data estate · Tenant: {escape(tenant_label)}<br>Generated on {escape(generated_at)}</p>
    </section>

    {integrity_banner}

    <section class="decision-panel">
      <h2>{escape(readiness['decision'])}</h2>
      <p>{escape(readiness['rationale'])} This decision covers Microsoft 365 tenant controls;
      external AI services require separate review.</p>
    </section>

    {scope_html}
    {assurance_html}
    {action_plan_html}

    <section class="summary-grid">
      <article class="summary-card">
        <div class="label">Actions</div>
        <div class="value">{len(findings)}</div>
      </article>
      <article class="summary-card">
        <div class="label">High Priority</div>
        <div class="value">{len(high_priority)}</div>
      </article>
      <article class="summary-card">
        <div class="label">Medium Priority</div>
        <div class="value">{len(medium_priority)}</div>
      </article>
      <article class="summary-card">
        <div class="label">Low Priority</div>
        <div class="value">{len(low_priority)}</div>
      </article>
      <article class="summary-card">
        <div class="label">Coverage Gaps</div>
        <div class="value">{len(not_assessed)}</div>
      </article>
      <article class="summary-card">
        <div class="label">Adoption &amp; Value Opportunities</div>
        <div class="value">{len(opportunity_rows)}</div>
        <div class="decision-effect">Separate from security readiness</div>
      </article>
      <article class="summary-card">
        <div class="label">Verified Strengths</div>
        <div class="value">{len(assurances)}</div>
      </article>
    </section>

    {adoption_html}

    <section class="controls-panel">
      <div class="controls-grid">
        <div class="control-group">
          <label for="searchInput">Search</label>
          <input id="searchInput" type="search" placeholder="Search feature, service, observation, or recommendation">
        </div>
        <div class="control-group">
          <label for="serviceFilter">Service</label>
          <select id="serviceFilter">
            <option value="">All services</option>
            {''.join(f'<option value="{escape(service, quote=True)}">{escape(service)}</option>' for service in sorted(services))}
          </select>
        </div>
        <div class="control-group">
          <label for="priorityFilter">Priority</label>
          <select id="priorityFilter">
            <option value="">All priorities</option>
            <option value="High">High</option>
            <option value="Medium">Medium</option>
            <option value="Low">Low</option>
            <option value="Unknown">Unknown</option>
          </select>
        </div>
        <div class="control-group">
          <label for="statusFilter">Status</label>
          <select id="statusFilter">
            <option value="">All statuses</option>
            {''.join(f'<option value="{escape(status, quote=True)}">{escape(status)}</option>' for status in status_values)}
          </select>
        </div>
        <div class="control-group">
          <label>&nbsp;</label>
          <div class="control-actions">
            <button id="resetFilters" class="control-button" type="button">Reset</button>
            <button id="expandAll" class="control-button" type="button">Expand All</button>
            <button id="collapseAll" class="control-button" type="button">Collapse All</button>
          </div>
        </div>
      </div>
      <div class="filter-meta">
        <div id="resultsCount">Showing {len(findings)} of {len(findings)} actions</div>
      </div>
    </section>

    <section class="toc-panel">
      <h2>Actions by Service</h2>
      <div class="toc-grid">
        {''.join(service_nav_items)}
      </div>
    </section>

    {''.join(service_sections)}
    {opportunity_html}
    {coverage_html}
    {appendix_html}
    <section id="emptyState" class="empty-state">
      No actions match the current filters. Try clearing or broadening your search.
    </section>
  </div>
  <script>
    (() => {{
      const searchInput = document.getElementById('searchInput');
      const serviceFilter = document.getElementById('serviceFilter');
      const priorityFilter = document.getElementById('priorityFilter');
      const statusFilter = document.getElementById('statusFilter');
      const resetButton = document.getElementById('resetFilters');
      const expandAllButton = document.getElementById('expandAll');
      const collapseAllButton = document.getElementById('collapseAll');
      const resultsCount = document.getElementById('resultsCount');
      const emptyState = document.getElementById('emptyState');
      const cards = Array.from(document.querySelectorAll('.recommendation-card'));
      const sections = Array.from(document.querySelectorAll('.service-section'));
      const tocLinks = Array.from(document.querySelectorAll('.toc-link'));

      function setSectionCollapsed(section, collapsed) {{
        section.classList.toggle('is-collapsed', collapsed);
        const content = section.querySelector('.service-content');
        const toggle = section.querySelector('.service-toggle');
        if (content) {{
          content.hidden = collapsed;
        }}
        if (toggle) {{
          toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        }}
      }}

      function applyFilters() {{
        const searchTerm = searchInput.value.trim().toLowerCase();
        const serviceValue = serviceFilter.value;
        const priorityValue = priorityFilter.value;
        const statusValue = statusFilter.value;

        let visibleCards = 0;

        cards.forEach(card => {{
          const matchesSearch = !searchTerm || (card.dataset.search || '').includes(searchTerm);
          const matchesService = !serviceValue || card.dataset.service === serviceValue;
          const matchesPriority = !priorityValue || card.dataset.priority === priorityValue;
          const matchesStatus = !statusValue || card.dataset.status === statusValue;
          const visible = matchesSearch && matchesService && matchesPriority && matchesStatus;
          card.hidden = !visible;
          if (visible) visibleCards += 1;
        }});

        sections.forEach(section => {{
          const visibleInSection = section.querySelectorAll('.recommendation-card:not([hidden])').length;
          section.hidden = !visibleInSection;
          const countNode = section.querySelector('.service-count');
          if (countNode) {{
            const defaultCount = countNode.dataset.defaultCount || visibleInSection;
            const noun = visibleInSection === 1 ? 'recommendation' : 'recommendations';
            countNode.textContent = visibleInSection === Number(defaultCount)
              ? `${{defaultCount}} ${{noun}}`
              : `${{visibleInSection}} matching ${{noun}}`;
          }}
        }});

        resultsCount.textContent = `Showing ${{visibleCards}} of {len(findings)} actions`;
        emptyState.style.display = visibleCards ? 'none' : 'block';
      }}

      [searchInput, serviceFilter, priorityFilter, statusFilter].forEach(control => {{
        control.addEventListener('input', applyFilters);
        control.addEventListener('change', applyFilters);
      }});

      resetButton.addEventListener('click', () => {{
        searchInput.value = '';
        serviceFilter.value = '';
        priorityFilter.value = '';
        statusFilter.value = '';
        applyFilters();
      }});

      sections.forEach(section => {{
        const toggle = section.querySelector('.service-toggle');
        if (!toggle) {{
          return;
        }}
        toggle.addEventListener('click', () => {{
          const collapsed = section.classList.contains('is-collapsed');
          setSectionCollapsed(section, !collapsed);
        }});
      }});

      expandAllButton.addEventListener('click', () => {{
        sections.forEach(section => setSectionCollapsed(section, false));
      }});

      collapseAllButton.addEventListener('click', () => {{
        sections.forEach(section => {{
          if (!section.hidden) {{
            setSectionCollapsed(section, true);
          }}
        }});
      }});

      tocLinks.forEach(link => {{
        link.addEventListener('click', () => {{
          const targetId = link.getAttribute('href');
          if (!targetId) {{
            return;
          }}
          const targetSection = document.querySelector(targetId);
          if (targetSection) {{
            setSectionCollapsed(targetSection, false);
          }}
        }});
      }});

      applyFilters();
    }})();
  </script>
</body>
</html>
"""
    
    with open(filepath, 'w', encoding='utf-8') as htmlfile:
        htmlfile.write(html)
    
    return str(filepath)

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

def print_recommendations_summary(recommendations, csv_path=None, excel_path=None, html_path=None, tenant_name=None):
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
    
    print("\n" + "="*80)
    print("RECOMMENDATIONS SUMMARY")
    print("="*80)
    if tenant_name:
        print(f"Tenant: {tenant_name}")
    
    recommendations = enrich_assessment_records(recommendations)
    readiness = summarize_readiness(recommendations)
    findings = [r for r in recommendations if r.get("Disposition") == DISPOSITION_ACTION]
    opportunities = [r for r in recommendations if r.get("Disposition") == DISPOSITION_OPPORTUNITY]
    assurances = [
        r for r in recommendations
        if r.get("Disposition") == DISPOSITION_ASSURANCE
        and str(r.get("EvidenceBasis", "")).lower() not in {"license signal", "not verified"}
        and r.get("EvidenceAvailable") == "Yes"
    ]
    coverage_items = [r for r in recommendations if r.get("Disposition") == DISPOSITION_COVERAGE]

    # Group by priority
    high_priority = [r for r in findings if r.get("Priority") == "High"]
    medium_priority = [r for r in findings if r.get("Priority") == "Medium"]
    low_priority = [r for r in findings if r.get("Priority") == "Low"]
    print(f"\nDeployment decision: {readiness['decision']}")
    print(f"  {readiness['rationale']}")
    print(f"\nActions: {len(findings)}")
    print(f"  🔴 High Priority:   {len(high_priority)}")
    print(f"  🟡 Medium Priority: {len(medium_priority)}")
    print(f"  🟢 Low Priority:    {len(low_priority)}")
    print(f"  🔵 Service-plan/context items (workbook): {len(opportunities)}")
    print(f"  ✓ Verified strengths: {len(assurances)}")
    if coverage_items:
        print(f"  ⚪ Coverage gaps:   {len(coverage_items)} (unverified, not clean)")

    # Group by service
    services = {}
    for rec in findings:
        service = rec.get("Service", "Unknown")
        if service not in services:
            services[service] = []
        services[service].append(rec)

    print(f"\nActions by Service:")
    for service, recs in sorted(services.items()):
        action_label = "action" if len(recs) == 1 else "actions"
        print(f"  • {service}: {len(recs)} {action_label}")
    
    if excel_path:
        print(f"\nRecommendations exported to Excel: {excel_path}")
    if csv_path:
        print(f"Recommendations exported to CSV: {csv_path}")
    if html_path:
        print(f"Recommendations exported to HTML: {html_path}")
    print()
