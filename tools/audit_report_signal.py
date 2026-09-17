"""Measure how much decision signal an existing HTML assessment contains.

This supports regression review of historical reports without requiring tenant access. It parses
the report's embedded cards, applies the current disposition model, and prints summary counts.
"""

import argparse
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Core.assessment_model import enrich_assessment_records


CARD_PATTERN = re.compile(
    r'<article class="recommendation-card"(?P<attrs>.*?)>(?P<body>.*?)</article>',
    re.IGNORECASE | re.DOTALL,
)


def _text(value):
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _attribute(attrs, name):
    match = re.search(rf'data-{re.escape(name)}="([^"]*)"', attrs, re.IGNORECASE)
    return html.unescape(match.group(1)).strip() if match else ""


def parse_report(path):
    source = Path(path).read_text(encoding="utf-8")
    if 'id="action-plan"' in source:
        # The current report renders actions, including evidence checks, and links
        # them to identifiers in the evidence table rather than hidden legacy cards.
        identifiers = {
            match.group(1): _text(match.group(2)) for match in re.finditer(
                r'<tr id="(evidence-[^"]+)">\s*<td>(.*?)</td>', source, re.I | re.S,
            )
        }
        records = []
        for match in re.finditer(r'<article class="action"(?P<attrs>.*?)>(?P<body>.*?)</article>', source, re.I | re.S):
            attrs, body = match.group('attrs'), match.group('body')
            title = re.search(r'<h3>(.*?)</h3>', body, re.I | re.S)
            evidence = re.search(r'href="#(evidence-[^"]+)"', body, re.I)
            kind = re.search(r'<span class="action-kind">(.*?)</span>', body, re.I | re.S)
            def paragraph(label):
                found = re.search(r'<p><strong>' + re.escape(label) + r'</strong>(.*?)</p>', body, re.I | re.S)
                return _text(found.group(1)) if found else ''
            action_type = _text(kind.group(1)) if kind else ''
            records.append({
                'RecommendationId': identifiers.get(evidence.group(1), '') if evidence else '',
                'Feature': _text(title.group(1)) if title else '',
                'Priority': _attribute(attrs, 'priority').title(),
                'Observation': paragraph('What we found.'),
                'Recommendation': paragraph('What to do.'),
                'Disposition': 'Coverage' if action_type == 'Evidence' else 'Action',
                'ActionType': action_type,
            })
        return records
    records = []
    for match in CARD_PATTERN.finditer(source):
        attrs, body = match.group("attrs"), match.group("body")
        card_id = re.search(r'<div class="card-meta">(.*?)</div>', body, re.I | re.S)
        title = re.search(r"<h3>(.*?)</h3>", body, re.I | re.S)
        sections = {
            _text(section.group(1)): _text(section.group(2))
            for section in re.finditer(
                r"<section>.*?<h4>(.*?)</h4>.*?<p>(.*?)</p>.*?</section>",
                body,
                re.I | re.S,
            )
        }
        status = _attribute(attrs, "status")
        records.append({
            "RecommendationId": _text(card_id.group(1)) if card_id else "",
            "Service": _attribute(attrs, "service"),
            "Feature": _text(title.group(1)) if title else "",
            "Status": status,
            "SourceStatus": status,
            "Priority": _attribute(attrs, "priority").replace("Unknown", ""),
            "Observation": sections.get("Observation", ""),
            "Recommendation": "" if sections.get("Recommendation") == "Not provided" else sections.get("Recommendation", ""),
            "Category": "Tenant Finding",
        })
    return enrich_assessment_records(records)


def summarize(records, html_source="", workbook=None):
    disposition_counts = Counter(record["Disposition"] for record in records)
    action_priorities = Counter(
        record.get("Priority") or "None"
        for record in records
        if record["Disposition"] == "Action"
    )
    result = {
        "total_cards": len(records),
        "dispositions": dict(sorted(disposition_counts.items())),
        "action_priorities": dict(sorted(action_priorities.items())),
        "primary_action_ratio": round(
            disposition_counts.get("Action", 0) / len(records), 3
        ) if records else 0,
    }
    issues = []
    modern = 'id="action-plan"' in html_source
    action_dispositions = {'Action', 'Coverage'} if modern else {'Action'}
    if re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", html_source, re.I):
        issues.append("HTML contains a possible user identity (email address).")
    if "Validation incomplete—do not use for deployment approval" in html_source:
        issues.append("The report integrity gate is incomplete.")
    declared = re.search(r'<div class="label">Actions</div>\s*<div class="value">(\d+)</div>', html_source)
    if declared and int(declared.group(1)) != disposition_counts.get("Action", 0):
        issues.append("HTML action headline does not match rendered action cards.")
    if modern:
        declared = re.search(r'<dt>Open actions</dt>\s*<dd>(\d+) to resolve or verify</dd>', html_source)
        if not declared or int(declared.group(1)) != len(records):
            issues.append('HTML action headline does not match rendered action cards.')
        if any(not row.get('RecommendationId') for row in records):
            issues.append('A rendered HTML action has no linked evidence identifier.')
    if workbook:
        book = None
        try:
            from openpyxl import load_workbook
            book = load_workbook(workbook, read_only=True, data_only=True)
            required = {
                "Action Plan", "Evidence Index", "Collection Coverage", "Recommendations",
                "Control Results", "Run Manifest", "Integrity Checks",
            }
            missing = sorted(required - set(book.sheetnames))
            if missing:
                issues.append("Workbook is missing required tabs: " + ", ".join(missing))

            def rows_for(sheet_name):
                if sheet_name not in book.sheetnames:
                    return []
                values = book[sheet_name].iter_rows(values_only=True)
                headers = [str(value or "") for value in next(values, [])]
                return [dict(zip(headers, row)) for row in values]

            workbook_rows = rows_for("Recommendations")
            if "Recommendations" in book.sheetnames:
                html_ids = {row.get("RecommendationId") for row in records if row.get("RecommendationId")}
                workbook_action_ids = {str(row.get("RecommendationId") or "") for row in workbook_rows if row.get("Disposition") in action_dispositions}
                if html_ids != workbook_action_ids:
                    issues.append("HTML and workbook action identifiers do not match.")

            action_rows = rows_for("Action Plan")
            workbook_action_ids = {
                str(row.get("RecommendationId") or "")
                for row in workbook_rows if row.get("Disposition") in action_dispositions
            }
            id_column = next((key for key in ('RecommendationId', 'Recommendation ID')
                              if action_rows and key in action_rows[0]), None)
            if id_column:
                action_plan_ids = {
                    str(row.get(id_column) or "")
                    for row in action_rows if row.get(id_column)
                }
                if action_plan_ids != workbook_action_ids:
                    issues.append("Action Plan and Recommendations action identifiers do not match.")
            else:
                normalize = lambda value: re.sub(r"\s+", " ", str(value or "")).strip().lower()
                placeholder = (len(action_rows) == 1 and not workbook_action_ids
                    and action_rows[0].get('What We Found') == 'No deployment actions were identified from the evidence collected.')
                action_plan_findings = Counter(
                    normalize(row.get("What We Found")) for row in action_rows
                    if row.get("What We Found") and not placeholder
                )
                workbook_action_findings = Counter(
                    normalize(row.get("Observation")) for row in workbook_rows
                    if row.get("Disposition") in action_dispositions and row.get("Observation")
                )
                if action_plan_findings != workbook_action_findings:
                    issues.append("Action Plan findings do not match the Recommendations action register.")

            evidence_rows = rows_for("Evidence Index")
            evidence_ids = {str(row.get("RecommendationId") or "") for row in evidence_rows}
            missing_action_evidence = sorted(workbook_action_ids - evidence_ids)
            if missing_action_evidence:
                issues.append("Executive actions are missing Evidence Index rows: " + ", ".join(missing_action_evidence))

            control_ids = {str(row.get("Control ID") or "") for row in rows_for("Control Results")}
            for row in workbook_rows:
                if row.get("Disposition") not in action_dispositions:
                    continue
                recommendation_id = str(row.get("RecommendationId") or "Unidentified action")
                control_id = str(row.get("Control ID") or "")
                if not control_id or control_id not in control_ids:
                    issues.append(f"Action {recommendation_id} is not mapped to a Control Results row.")

            valid_states = {"available", "partial", "unavailable", "not_requested"}
            for row in rows_for("Collection Coverage"):
                state = str(row.get("State") or "").lower()
                metadata = (str(row.get('Source') or ''), state) in {
                    ('Offline report build', 'offline'),
                    ('Methodology compatibility migration', 'migrated'),
                }
                if state not in valid_states and not metadata:
                    issues.append(f"Collection source {row.get('Source')} has invalid state {row.get('State')!r}.")
                if bool(row.get("Truncated")) and state == "available":
                    issues.append(f"Truncated source {row.get('Source')} is incorrectly marked available.")

            for row in rows_for("Integrity Checks"):
                if str(row.get("Status") or "").lower() == "failed":
                    issues.append("Workbook integrity gate reports failure: " + str(row.get("Issue") or ""))

            for row in rows_for("Run Manifest"):
                item, value = str(row.get("Item") or ""), str(row.get("Value") or "")
                if item.lower().startswith("input:") and value.lower() not in {"", "not supplied"}:
                    if "/" in value or "\\" in value:
                        issues.append(f"Run Manifest input filename is not sanitized: {item}.")
                if any(secret_word in item.lower() for secret_word in ("client secret", "access token", "refresh token")):
                    issues.append(f"Run Manifest contains a credential field: {item}.")
        except Exception as exc:
            issues.append(f"Workbook could not be audited: {exc}")
        finally:
            if book is not None:
                book.close()
    result["audit_issues"] = issues
    result["valid"] = not issues
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--workbook", type=Path, help="Optional workbook for cross-output consistency checks")
    parser.add_argument("--actions", action="store_true", help="List classified primary actions")
    args = parser.parse_args()
    html_source = args.report.read_text(encoding="utf-8")
    records = parse_report(args.report)
    print(json.dumps(summarize(records, html_source=html_source, workbook=args.workbook), indent=2))
    if args.actions:
        for record in records:
            if record["Disposition"] == "Action":
                print("\t".join(str(record.get(key, "")) for key in (
                    "RecommendationId", "Service", "Priority", "Status", "Feature", "Observation"
                )))


if __name__ == "__main__":
    main()
