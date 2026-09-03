"""Measure how much decision signal an existing HTML assessment contains.

This supports regression review of historical reports without requiring tenant access. It parses
the report's embedded cards, applies the current disposition model, and prints summary counts.
"""

import argparse
import html
import json
import re
from collections import Counter
from pathlib import Path

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


def summarize(records):
    disposition_counts = Counter(record["Disposition"] for record in records)
    action_priorities = Counter(
        record.get("Priority") or "None"
        for record in records
        if record["Disposition"] == "Action"
    )
    return {
        "total_cards": len(records),
        "dispositions": dict(sorted(disposition_counts.items())),
        "action_priorities": dict(sorted(action_priorities.items())),
        "primary_action_ratio": round(
            disposition_counts.get("Action", 0) / len(records), 3
        ) if records else 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--actions", action="store_true", help="List classified primary actions")
    args = parser.parse_args()
    records = parse_report(args.report)
    print(json.dumps(summarize(records), indent=2))
    if args.actions:
        for record in records:
            if record["Disposition"] == "Action":
                print("\t".join(str(record.get(key, "")) for key in (
                    "RecommendationId", "Service", "Priority", "Status", "Feature", "Observation"
                )))


if __name__ == "__main__":
    main()
