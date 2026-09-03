"""Evidence-backed SharePoint and Purview data exposure assessment.

Microsoft's authoritative oversharing scans are asynchronous administrative reports rather
than ordinary Microsoft Graph inventory endpoints.  This module intentionally consumes their
exports instead of inferring exposure from site or file counts.  A missing export becomes a
coverage item; it never becomes a clean bill of health.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .new_recommendation import (
    CATEGORY_SCAN_COVERAGE,
    NOT_ASSESSED_STATUS,
    new_recommendation,
)


SUPPORTED_SUFFIXES = {".csv", ".tsv", ".json", ".xlsx"}
MAX_EVIDENCE_ROWS = 25000
DEFAULT_SAM_MAX_AGE_DAYS = 35
DEFAULT_DSPM_MAX_AGE_DAYS = 8


def _normalized_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _number(value):
    if isinstance(value, bool):
        return int(value)
    text = _text(value).replace(",", "")
    if not text:
        return 0
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0
    try:
        return int(float(match.group(0)))
    except ValueError:
        return 0


def _positive_int_env(name, default):
    try:
        value = int(os.environ.get(name, default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _parse_date(value):
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    for pattern in ("%m/%d/%Y", "%m/%d/%Y %H:%M:%S", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _is_true(value):
    return _text(value).lower() in {"1", "true", "yes", "y", "enabled", "active"}


def _value(record, *aliases):
    normalized = {_normalized_key(key): value for key, value in (record or {}).items()}
    for alias in aliases:
        key = _normalized_key(alias)
        if key in normalized:
            return normalized[key]
    return ""


def _has_column(record, *aliases):
    keys = {_normalized_key(key) for key in (record or {}).keys()}
    return any(_normalized_key(alias) in keys for alias in aliases)


def _matches_authoritative_schema(record, source_kind):
    """Reject arbitrary spreadsheets that happen to be passed to an exposure option."""
    if source_kind == "sam":
        aliases = (
            "Anyone link count", "Anonymous link count", "Everyone permission count",
            "EEEU permission count", "Everyone except external users count",
            "Organization link count", "External user count", "External sharing link count",
            "Link Type", "Sharing Link Type", "Permission Recipient", "Primary Admin",
            "Number of users with permissions", "Permissioned users", "ExternalSharing",
        )
    else:
        aliases = (
            "Potentially overshared items", "Overshared item count", "Sensitive data detected",
            "Sensitive item count", "Unlabeled sensitive item count", "Is unlabeled sensitive",
            "Link Type", "Sharing Link Type", "Access", "Exposure", "Sensitivity Label",
        )
    return _has_column(record, *aliases)


def _split_env_paths(name):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    # Windows paths commonly contain a colon, so use the platform path separator only.
    return [item.strip() for item in raw.split(os.pathsep) if item.strip()]


def _expand_paths(paths):
    expanded = []
    seen = set()
    for raw_path in paths or []:
        path = Path(raw_path).expanduser()
        candidates = (
            sorted(item for item in path.iterdir() if item.suffix.lower() in SUPPORTED_SUFFIXES)
            if path.is_dir()
            else [path]
        )
        for candidate in candidates:
            identity = str(candidate.resolve()).lower() if candidate.exists() else str(candidate).lower()
            if identity not in seen:
                seen.add(identity)
                expanded.append(candidate)
    return expanded


def _iter_json_records(payload, sheet_name=""):
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                row = dict(item)
                if sheet_name:
                    row.setdefault("_source_sheet", sheet_name)
                yield row
        return
    if not isinstance(payload, dict):
        return
    for key in ("rows", "items", "value", "results", "data"):
        nested = payload.get(key)
        if isinstance(nested, list):
            yield from _iter_json_records(nested, sheet_name=sheet_name or key)
            return
    # A dictionary of named report arrays is also a common export envelope.
    yielded = False
    for key, nested in payload.items():
        if isinstance(nested, list) and any(isinstance(item, dict) for item in nested):
            yielded = True
            yield from _iter_json_records(nested, sheet_name=key)
    if not yielded:
        row = dict(payload)
        if sheet_name:
            row.setdefault("_source_sheet", sheet_name)
        yield row


def _read_records(path):
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            yield from csv.DictReader(handle, delimiter=delimiter)
        return
    if suffix == ".json":
        with path.open("r", encoding="utf-8-sig") as handle:
            yield from _iter_json_records(json.load(handle))
        return
    if suffix == ".xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for worksheet in workbook.worksheets:
                rows = worksheet.iter_rows(values_only=True)
                headers = next(rows, None)
                if not headers:
                    continue
                headers = [_text(header) or f"Column {index + 1}" for index, header in enumerate(headers)]
                for values in rows:
                    row = dict(zip(headers, values))
                    row["_source_sheet"] = worksheet.title
                    yield row
        finally:
            workbook.close()
        return
    raise ValueError(f"Unsupported report type: {suffix or 'no extension'}")


def _record_identity(record):
    site_url = _text(_value(record, "Site URL", "SiteUrl", "Site Address", "Location URL"))
    item_url = _text(_value(record, "Item URL", "File URL", "Object URL", "Item Path", "Path"))
    site_name = _text(_value(record, "Site Name", "SiteName", "Location Name", "Name"))
    return site_url, item_url, site_name


def _classify_record(record, source_kind):
    """Return normalized risk signals backed by explicit report fields."""
    site_url, item_url, site_name = _record_identity(record)
    label = _text(_value(record, "Sensitivity Label", "SensitivityLabel", "Label Name", "Label"))
    owner = _text(_value(record, "Primary Admin", "PrimaryAdmin", "Site Owner", "Owner", "Owner Email"))
    workload = _text(_value(record, "Workload", "Data Source", "Service")) or "SharePoint"

    counts = {
        "Anyone links": _number(_value(record, "Anyone link count", "AnyoneLinkCount", "Anonymous link count", "AnonymousLinkCount")),
        "Everyone permissions": _number(_value(record, "Everyone permission count", "EveryonePermissionCount")),
        "EEEU permissions": _number(_value(record, "EEEU permission count", "EEEUPermissionCount", "Everyone except external users count")),
        "Organization links": _number(_value(record, "Organization link count", "People in your organization link count", "Company link count")),
        "External exposure": _number(_value(record, "External user count", "ExternalUserCount", "External sharing link count", "Externally shared item count", "Shared externally count")),
        "Potentially overshared items": _number(_value(record, "Potentially overshared items", "PotentiallyOversharedItems", "Overshared item count")),
        "Sensitive items": _number(_value(record, "Sensitive data detected", "Sensitive item count", "SensitiveItemCount", "Sensitive data count")),
        "Unlabeled sensitive items": _number(_value(record, "Unlabeled sensitive item count", "UnlabeledSensitiveItemCount", "Unlabeled item count")),
    }

    link_type = _text(_value(record, "Link Type", "Sharing Link Type", "SharingLinkType", "Access Type"))
    permission_recipient = _text(_value(record, "Permission Recipient", "Principal", "Group Name", "Shared With"))
    access_text = " ".join([link_type, permission_recipient, _text(_value(record, "Access", "Exposure", "Sharing Type"))]).lower()

    if any(marker in access_text for marker in ("anonymous", "anyone")):
        counts["Anyone links"] = max(counts["Anyone links"], 1)
    if "everyone except external" in access_text or "eeeu" in access_text:
        counts["EEEU permissions"] = max(counts["EEEU permissions"], 1)
    elif re.search(r"\beveryone\b", access_text):
        counts["Everyone permissions"] = max(counts["Everyone permissions"], 1)
    if any(marker in access_text for marker in ("external", "guest")):
        counts["External exposure"] = max(counts["External exposure"], 1)
    if any(marker in access_text for marker in ("organization", "company-wide", "company wide")):
        counts["Organization links"] = max(counts["Organization links"], 1)

    explicit_sensitive = counts["Sensitive items"] > 0 or bool(label and label.lower() not in {"none", "unlabeled", "not labeled", "n/a"})
    unlabeled_sensitive = counts["Unlabeled sensitive items"] > 0 or _is_true(
        _value(record, "Is unlabeled sensitive", "Unlabeled sensitive", "Sensitive and unlabeled")
    )
    if unlabeled_sensitive:
        counts["Unlabeled sensitive items"] = max(counts["Unlabeled sensitive items"], 1)

    ownerless = False
    if source_kind == "sam" and _has_column(record, "Primary Admin", "PrimaryAdmin", "Site Owner", "Owner"):
        ownerless = not owner

    # Sensitive data is context that raises the severity of an exposure; its mere existence is
    # not a failure. Keep it out of the risk totals unless it is explicitly unlabeled.
    signals = {
        name: count for name, count in counts.items()
        if count > 0 and name != "Sensitive items"
    }
    if ownerless:
        signals["Ownerless site"] = 1

    if not signals:
        return None

    broad = sum(signals.get(key, 0) for key in (
        "Anyone links", "Everyone permissions", "EEEU permissions", "Organization links",
        "External exposure", "Potentially overshared items",
    ))
    severity = "High" if explicit_sensitive and (broad or unlabeled_sensitive) else "Medium"
    if set(signals) == {"Ownerless site"}:
        severity = "Medium"

    return {
        "Source": "SharePoint Advanced Management" if source_kind == "sam" else "Microsoft Purview DSPM",
        "Source File": _text(record.get("_source_file")),
        "Source Sheet": _text(record.get("_source_sheet")),
        "Workload": workload,
        "Site Name": site_name,
        "Site URL": site_url,
        "Item URL": item_url,
        "Owner": owner,
        "Sensitivity Label": label,
        "Risk Signals": "; ".join(signals.keys()),
        "Signal Count": sum(signals.values()),
        "Severity": severity,
        "RecommendationId": "",
        "Flagged By": "",
        "_signals": signals,
    }


def _scan_reports(paths, source_kind):
    result = {
        "kind": source_kind,
        "files_requested": len(paths),
        "files_parsed": 0,
        "files_loaded": 0,
        "records_read": 0,
        "errors": [],
        "risk_rows": [],
        "evidence_truncated": False,
        "signals": defaultdict(int),
        "signal_maxima": {},
        "affected_sites": set(),
        "report_dates": [],
        "evidence_seen": set(),
    }
    for path in paths:
        if not path.exists():
            result["errors"].append(f"{path}: file not found")
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            result["errors"].append(f"{path}: unsupported file type")
            continue
        try:
            file_recognized = False
            for raw_record in _read_records(path):
                result["records_read"] += 1
                record = dict(raw_record or {})
                record["_source_file"] = path.name
                if not _matches_authoritative_schema(record, source_kind):
                    continue
                file_recognized = True
                report_date = _parse_date(_value(
                    record,
                    "Report Date", "Scan Date", "Assessment Date", "Completed Date",
                    "Completion Date", "Data As Of", "Generated Date", "CreatedDateTime",
                    "Report End Time", "ReportEndTime", "TriggeredDateTime",
                ))
                if report_date:
                    result["report_dates"].append(report_date)
                risk = _classify_record(record, source_kind)
                if not risk:
                    continue
                risk_signals = risk.pop("_signals")
                identity = risk.get("Item URL") or risk.get("Site URL") or risk.get("Site Name")
                for name, count in risk_signals.items():
                    # The same site can appear in a baseline and a detailed SAM export. Use the
                    # largest reported count for that site/signal instead of double-counting it.
                    # Rows without an identity remain independent observations.
                    signal_identity = identity.lower() if identity else f"{path.name}:{result['records_read']}"
                    signal_key = (name, signal_identity)
                    result["signal_maxima"][signal_key] = max(
                        int(result["signal_maxima"].get(signal_key, 0) or 0), count
                    )
                site_identity = risk.get("Site URL") or risk.get("Site Name")
                if site_identity:
                    result["affected_sites"].add(site_identity)
                evidence_key = (
                    (identity or f"{path.name}:{result['records_read']}").lower(),
                    risk.get("Risk Signals", "").lower(),
                )
                if evidence_key not in result["evidence_seen"]:
                    result["evidence_seen"].add(evidence_key)
                    if len(result["risk_rows"]) < MAX_EVIDENCE_ROWS:
                        result["risk_rows"].append(risk)
                    else:
                        result["evidence_truncated"] = True
            result["files_parsed"] += 1
            if file_recognized:
                result["files_loaded"] += 1
            else:
                result["errors"].append(
                    f"{path}: no recognized {source_kind.upper()} exposure columns or data rows"
                )
        except Exception as exc:
            result["errors"].append(f"{path}: {type(exc).__name__}: {exc}")
    for (signal_name, _), count in result["signal_maxima"].items():
        result["signals"][signal_name] += count
    result["signals"] = dict(result["signals"])
    result["affected_sites"] = sorted(result["affected_sites"], key=str.lower)
    result["latest_report_date"] = max(result["report_dates"]) if result["report_dates"] else None
    max_age_days = _positive_int_env(
        "SAM_REPORT_MAX_AGE_DAYS" if source_kind == "sam" else "DSPM_REPORT_MAX_AGE_DAYS",
        DEFAULT_SAM_MAX_AGE_DAYS if source_kind == "sam" else DEFAULT_DSPM_MAX_AGE_DAYS,
    )
    result["max_age_days"] = max_age_days
    if not result["files_loaded"]:
        result["freshness"] = "missing"
        result["age_days"] = None
    elif not result["latest_report_date"]:
        result["freshness"] = "unknown"
        result["age_days"] = None
    else:
        age_days = max((datetime.now(timezone.utc) - result["latest_report_date"]).days, 0)
        result["age_days"] = age_days
        result["freshness"] = "fresh" if age_days <= max_age_days else "stale"
    # datetime values are useful while evaluating but should not leak into JSON/export callers.
    result["latest_report_date"] = (
        result["latest_report_date"].isoformat() if result["latest_report_date"] else ""
    )
    result.pop("report_dates", None)
    result.pop("signal_maxima", None)
    result.pop("evidence_seen", None)
    return result


def _coverage_finding(feature, source_name, cli_option, env_name, documentation_url, instructions, errors=None):
    error_text = f" {len(errors)} supplied file(s) could not be read." if errors else ""
    return new_recommendation(
        "Data Exposure",
        feature,
        f"{source_name} evidence was not available, so this assessment did not verify this exposure domain.{error_text}",
        f"{instructions} Export the completed report, then rerun with {cli_option} PATH or the {env_name} environment variable. Enabling this capability is optional, but exposure remains unverified until recent evidence is supplied.",
        "Collection guidance",
        documentation_url,
        priority="High",
        status=NOT_ASSESSED_STATUS,
        category=CATEGORY_SCAN_COVERAGE,
        finding_key=f"data_exposure.coverage.{feature.lower().replace(' ', '_')}",
        disposition="Coverage",
        impact_area="Content access & grounding",
        ai_applicability="All AI using M365 data",
        evidence_basis="Not verified",
        confidence="Unknown",
    )


def _freshness_finding(feature, source_name, scan, cli_option, env_name, documentation_url, run_instructions):
    freshness = scan["freshness"]
    if freshness == "fresh":
        return None
    if freshness == "stale":
        observation = (
            f"The newest supplied {source_name} result is {scan['age_days']} day(s) old, "
            f"which exceeds the {scan['max_age_days']}-day freshness threshold."
        )
    else:
        observation = (
            f"The supplied {source_name} export did not contain a recognized report or scan date, "
            "so its freshness could not be verified."
        )
    return new_recommendation(
        "Data Exposure",
        feature,
        observation,
        f"{run_instructions} Export the completed result and rerun with {cli_option} PATH or {env_name}. The freshness threshold can be changed with {'SAM_REPORT_MAX_AGE_DAYS' if scan['kind'] == 'sam' else 'DSPM_REPORT_MAX_AGE_DAYS'}.",
        "Scan guidance",
        documentation_url,
        priority="High",
        status=NOT_ASSESSED_STATUS,
        category=CATEGORY_SCAN_COVERAGE,
        finding_key=f"data_exposure.freshness.{scan['kind']}",
        disposition="Coverage",
        impact_area="Content access & grounding",
        ai_applicability="All AI using M365 data",
        evidence_basis="Stale tenant evidence" if freshness == "stale" else "Freshness not verified",
        confidence="Low",
    )


def _risk_finding(feature, observation, recommendation, priority, key, url, confidence="High"):
    return new_recommendation(
        "Data Exposure",
        feature,
        observation,
        recommendation,
        "Remediation guidance",
        url,
        priority=priority,
        status="Action Required" if priority == "High" else "Attention Required",
        finding_key=f"data_exposure.{key}",
        evidence_key="data_exposure_detail",
        evidence_summary="See Data Exposure Detail for the exact sites or items and the report signals that produced this finding.",
        disposition="Action",
        impact_area="Content access & grounding",
        ai_applicability="All AI using M365 data",
        evidence_basis="Tenant evidence",
        confidence=confidence,
    )


def _findings_from_scan(scan):
    signals = scan["signals"]
    site_count = len(scan["affected_sites"])
    location_text = f" across {site_count} identified site(s)" if site_count else ""
    findings = []
    confidence = "High" if scan.get("freshness") == "fresh" else "Medium"

    anyone = signals.get("Anyone links", 0)
    if anyone:
        findings.append(_risk_finding(
            "Anonymous sharing exposure",
            f"Authoritative oversharing reports identified {anyone:,} Anyone or anonymous sharing exposure(s){location_text}.",
            "Validate business need, remove anonymous links that are not explicitly required, replace them with least-privilege links, and use sensitivity labels or DLP for sensitive content.",
            "High", "anonymous_links", "https://learn.microsoft.com/en-us/sharepoint/data-access-governance-reports", confidence,
        ))

    broad_internal = sum(signals.get(key, 0) for key in (
        "Everyone permissions", "EEEU permissions", "Organization links"
    ))
    if broad_internal:
        findings.append(_risk_finding(
            "Broad internal access",
            f"Authoritative oversharing reports identified {broad_internal:,} organization-wide, Everyone, or EEEU exposure(s){location_text}.",
            "Have site owners validate the audience, remove broad permissions that are not required, and initiate site access reviews for the affected sites.",
            "High", "broad_internal_access", "https://learn.microsoft.com/en-us/sharepoint/site-access-review", confidence,
        ))

    external = signals.get("External exposure", 0)
    if external:
        findings.append(_risk_finding(
            "External sharing exposure",
            f"Authoritative oversharing reports identified {external:,} external-user or external-link exposure(s){location_text}.",
            "Review the external recipients and link purpose, remove expired or unjustified access, and apply expiration and least-privilege sharing defaults.",
            "High", "external_exposure", "https://learn.microsoft.com/en-us/sharepoint/turn-external-sharing-on-or-off", confidence,
        ))

    potential = signals.get("Potentially overshared items", 0)
    if potential:
        findings.append(_risk_finding(
            "Potentially overshared content",
            f"Purview DSPM identified {potential:,} potentially overshared item(s){location_text}.",
            "Use the DSPM item-level results to resolve false positives, notify owners, apply labels, or remove excessive sharing links. Prioritize sensitive and unlabeled content first.",
            "High", "potentially_overshared", "https://learn.microsoft.com/en-us/purview/data-security-posture-management-oversharing", confidence,
        ))

    unlabeled = signals.get("Unlabeled sensitive items", 0)
    if unlabeled:
        findings.append(_risk_finding(
            "Sensitive content without labels",
            f"Purview DSPM identified {unlabeled:,} sensitive item(s) without an adequate sensitivity label{location_text}.",
            "Validate the classifications and deploy an appropriate sensitivity label or auto-labeling policy before expanding AI access to the affected content.",
            "High", "unlabeled_sensitive", "https://learn.microsoft.com/en-us/purview/apply-sensitivity-label-automatically", confidence,
        ))

    ownerless = signals.get("Ownerless site", 0)
    if ownerless:
        findings.append(_risk_finding(
            "Ownerless SharePoint sites",
            f"SharePoint governance evidence identified {ownerless:,} site(s) without a recorded primary administrator or owner.",
            "Assign accountable owners and require them to attest that membership, sharing links, permissions, labels, and continued business need are correct.",
            "Medium", "ownerless_sites", "https://learn.microsoft.com/en-us/sharepoint/site-ownership-policy", confidence,
        ))

    return findings


def build_data_exposure_assessment(sam_report_paths=None, dspm_report_paths=None, enabled=True):
    """Load SAM/DSPM exports and create a dedicated, evidence-backed assessment result."""
    if not enabled and not sam_report_paths and not dspm_report_paths:
        return {"available": False, "recommendations": [], "evidence_rows": [], "sources": {}, "operator_messages": []}

    sam_paths = _expand_paths(sam_report_paths or _split_env_paths("SAM_DAG_REPORT_PATHS"))
    dspm_paths = _expand_paths(dspm_report_paths or _split_env_paths("DSPM_REPORT_PATHS"))
    sam = _scan_reports(sam_paths, "sam")
    dspm = _scan_reports(dspm_paths, "dspm")

    recommendations = []
    if not sam["files_loaded"]:
        recommendations.append(_coverage_finding(
            "SharePoint oversharing evidence",
            "SharePoint Advanced Management Data Access Governance",
            "--sam-report",
            "SAM_DAG_REPORT_PATHS",
            "https://learn.microsoft.com/en-us/sharepoint/data-access-governance-reports",
            "First check SharePoint admin center > Reports > Data access governance for a completed report within the accepted freshness window and export it if one exists. If no current report exists, create the site-permissions baseline plus sharing-link and EEEU reports and let Microsoft complete them. If Data Access Governance is unavailable, optionally confirm SharePoint Advanced Management licensing and assign the SharePoint Administrator or SharePoint Advanced Management Administrator role.",
            errors=sam["errors"],
        ))
    else:
        recommendations.extend(_findings_from_scan(sam))
        freshness = _freshness_finding(
            "SharePoint oversharing scan freshness",
            "SharePoint Data Access Governance",
            sam,
            "--sam-report",
            "SAM_DAG_REPORT_PATHS",
            "https://learn.microsoft.com/en-us/sharepoint/data-access-governance-reports",
            "In the SharePoint admin center, open Reports > Data access governance and create or rerun the required reports; allow Microsoft to finish them.",
        )
        if freshness:
            recommendations.append(freshness)

    if not dspm["files_loaded"]:
        recommendations.append(_coverage_finding(
            "Sensitive-data exposure evidence",
            "Microsoft Purview DSPM data-risk assessment",
            "--dspm-report",
            "DSPM_REPORT_PATHS",
            "https://learn.microsoft.com/en-us/purview/data-security-posture-management-oversharing",
            "First check Microsoft Purview > Data Security Posture Management > Discover > Data risk assessments for a recent completed default or custom assessment and export it if one exists. If no current result exists, let the default assessment complete or run a custom assessment for the required SharePoint sites. If DSPM is not configured, optionally complete its setup tasks for Audit and analytics first.",
            errors=dspm["errors"],
        ))
    else:
        recommendations.extend(_findings_from_scan(dspm))
        freshness = _freshness_finding(
            "Sensitive-data exposure scan freshness",
            "Purview DSPM data-risk assessment",
            dspm,
            "--dspm-report",
            "DSPM_REPORT_PATHS",
            "https://learn.microsoft.com/en-us/purview/data-security-posture-management-oversharing",
            "In the Microsoft Purview portal, open Data Security Posture Management > Discover > Data risk assessments and wait for the default or custom assessment to complete.",
        )
        if freshness:
            recommendations.append(freshness)

    total_signals = sum(sam["signals"].values()) + sum(dspm["signals"].values())
    if (sam["freshness"] == "fresh" and dspm["freshness"] == "fresh" and total_signals == 0):
        recommendations.append(new_recommendation(
            "Data Exposure",
            "Oversharing assessment",
            f"The supplied SAM and DSPM reports were parsed successfully ({sam['records_read'] + dspm['records_read']:,} record(s)); no explicit oversharing signal was present in their exported fields.",
            "",
            "Assessment guidance",
            "https://learn.microsoft.com/en-us/purview/data-security-posture-management-oversharing",
            status="Success",
            finding_key="data_exposure.assessed_no_signals",
            evidence_key="data_exposure_detail",
            evidence_summary="See Data Exposure Detail for source files, record counts, report dates, and freshness state.",
            disposition="Assurance",
            impact_area="Content access & grounding",
            ai_applicability="All AI using M365 data",
            evidence_basis="Tenant evidence",
            confidence="High",
        ))

    operator_messages = []
    for source_name, scan in (
        ("SharePoint Data Access Governance", sam),
        ("Purview DSPM data-risk assessment", dspm),
    ):
        if scan["freshness"] == "fresh":
            operator_messages.append(
                f"Using recent {source_name} evidence dated {scan['latest_report_date'][:10]} "
                f"({scan['records_read']:,} record(s))."
            )
        elif scan["freshness"] == "stale":
            operator_messages.append(
                f"{source_name} evidence is {scan['age_days']} day(s) old; a new Microsoft scan is needed. "
                "The report includes run and enablement guidance."
            )
        elif scan["freshness"] == "unknown":
            operator_messages.append(
                f"{source_name} evidence was parsed, but its scan date could not be verified; "
                "the report requests a dated export."
            )
        else:
            operator_messages.append(
                f"No usable {source_name} export was supplied; check for a recent completed scan before "
                "starting a new one. The report records this as Coverage and includes optional enablement, "
                "scan, and export instructions."
            )

    return {
        "available": bool(sam["files_loaded"] or dspm["files_loaded"]),
        "recommendations": recommendations,
        "evidence_rows": sam["risk_rows"] + dspm["risk_rows"],
        "sources": {"sam": sam, "dspm": dspm},
        "evidence_truncated": sam["evidence_truncated"] or dspm["evidence_truncated"],
        "operator_messages": operator_messages,
    }
