"""Offline evidence from the Microsoft 365 Copilot readiness user CSV export.

These are exported readiness and Microsoft 365 workload indicators, never measures
of actual Copilot use. All counts describe the rows in this export, which can be a
filtered subset of a tenant. User identities are returned only by explicit request.
"""

import csv
import io
import re
from collections import Counter
from datetime import date
from pathlib import Path


FLAG_COLUMNS = {
    "license_assigned": "Has Copilot license assigned",
    "suggested_candidate": "Suggested candidate for Copilot",
    "eligible_update_channel": "Uses eligible update channel",
    "teams_meetings": "Uses Teams meetings",
    "teams_chat": "Uses Teams chat",
    "outlook_email": "Uses Outlook email",
    "office_docs": "Uses Office docs",
}
KNOWN_COLUMNS = (
    "Report Refresh Date",
    "User Principal Name",
    "Report Period",
    *FLAG_COLUMNS.values(),
)
OPTIONAL_COLUMNS = {'Suggested candidate for Copilot'}
REQUIRED_COLUMNS = tuple(name for name in KNOWN_COLUMNS if name not in OPTIONAL_COLUMNS)
SOURCE_NAME = "Microsoft 365 Copilot readiness user export"
SCOPE_NOTE = (
    "Counts describe exported rows only; tenant-wide coverage is not established. "
    "Readiness, candidate and Microsoft 365 workload flags do not establish actual "
    "Copilot usage or a tenant-wide Copilot license total."
)
UNKNOWN_VALUES = {"", "unknown", "n/a", "na", "null", "none", "-"}


def _header(value):
    return " ".join(str(value or "").strip().casefold().split())


def _empty_evidence(source_file=""):
    return {
        "available": False,
        "status": "not_supplied",
        "source": SOURCE_NAME,
        "source_file": source_file,
        "report_date": "",
        "report_period": "",
        "report_dates": [],
        "report_periods": [],
        "total_rows": 0,
        "metrics": {},
        "summary": SCOPE_NOTE,
        "warnings": [],
        "error": "",
    }


def _failure(evidence, status, message):
    evidence.update(available=False, status=status, error=message, metrics={})
    evidence.pop("user_details", None)
    return evidence


def _flag(value):
    normalized = value.strip().casefold()
    if normalized == "true":
        return True, False
    if normalized == "false":
        return False, False
    return None, normalized not in UNKNOWN_VALUES


def _report_date(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def load_copilot_readiness_export(path, include_user_details=False):
    """Read an unmodified readiness CSV without network access or tenant assumptions.

    ``metrics`` maps each FLAG_COLUMNS key to true/false/unknown row counts.
    ``report_period`` preserves the export's period string (for example, "30").
    A missing/invalid metadata value leaves the corresponding scalar empty rather
    than substituting a download date or a standard Graph usage-report period.

    Mixed dates/periods, duplicate identities and malformed CSV are unavailable
    evidence, with no metrics. Unknown flags or missing metadata permit aggregate
    evidence with status "warning". Optional ``user_details`` contains the original
    user principal name and flags as bool/None; it belongs in restricted outputs.
    Error and warning messages never include raw cells or full filesystem paths.
    """
    evidence = _empty_evidence()
    if not path:
        return evidence

    try:
        source = Path(path)
        evidence["source_file"] = source.name
        if source.suffix.casefold() != ".csv":
            return _failure(evidence, "error", "The readiness export must be a CSV file.")
        raw = source.read_bytes()
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        reader = csv.reader(io.StringIO(raw.decode(encoding), newline=""), strict=True)
        headers = next(reader, [])
        normalized_headers = [_header(value) for value in headers]
        header_counts = Counter(normalized_headers)
        if not headers or any(not value or count > 1 for value, count in header_counts.items()):
            return _failure(
                evidence, "invalid_headers",
                "The readiness export has empty or duplicate column headers.",
            )
        missing = [name for name in REQUIRED_COLUMNS if _header(name) not in header_counts]
        if missing:
            return _failure(
                evidence, "invalid_headers",
                "Expected a Copilot readiness user export. Missing required columns: "
                + ", ".join(missing) + ". Copilot usage exports have a different schema.",
            )

        indices = {name: normalized_headers.index(_header(name)) for name in KNOWN_COLUMNS
                   if _header(name) in header_counts}
        absent_optional = OPTIONAL_COLUMNS - indices.keys()
        for column in sorted(absent_optional):
            evidence['warnings'].append(f'{column}: not included in this export; candidate status remains unknown.')
        if any(name not in {_header(column) for column in KNOWN_COLUMNS} for name in normalized_headers):
            evidence["warnings"].append("Additional columns were ignored.")
        metrics = {key: {"true": 0, "false": 0, "unknown": 0} for key in FLAG_COLUMNS}
        unknown_counts = Counter()
        invalid_counts = Counter()
        report_dates = set()
        report_periods = set()
        missing_dates = 0
        missing_periods = 0
        missing_identities = 0
        duplicate_identities = 0
        identities = set()
        user_details = []

        for row in reader:
            if not row or not any(value.strip() for value in row):
                continue
            evidence["total_rows"] += 1
            if len(row) != len(headers):
                return _failure(
                    evidence, "invalid_data",
                    "A data row has a different number of columns from the header. "
                    "Export the readiness report again without modifying the CSV.",
                )

            values = {name: row[index].strip() for name, index in indices.items()}
            raw_date = values["Report Refresh Date"]
            raw_period = values["Report Period"]
            parsed_date = _report_date(raw_date)
            if parsed_date:
                report_dates.add(parsed_date)
            else:
                missing_dates += 1
            if re.fullmatch(r"[1-9]\d*", raw_period):
                report_periods.add(raw_period)
            else:
                missing_periods += 1

            identity = values["User Principal Name"]
            if identity:
                identity_key = identity.casefold()
                duplicate_identities += int(identity_key in identities)
                identities.add(identity_key)
            else:
                missing_identities += 1

            flags = {}
            for key, column in FLAG_COLUMNS.items():
                flag, invalid = _flag(values.get(column, ''))
                flags[key] = flag
                bucket = "unknown" if flag is None else "true" if flag else "false"
                metrics[key][bucket] += 1
                unknown_counts[key] += int(flag is None)
                invalid_counts[key] += int(invalid)
            if include_user_details:
                user_details.append({
                    "user_principal_name": identity,
                    "report_date": raw_date,
                    "report_period": raw_period,
                    **flags,
                })

        evidence["report_dates"] = sorted(report_dates)
        evidence["report_periods"] = sorted(report_periods, key=int)
        if len(report_dates) == 1 and not missing_dates:
            evidence["report_date"] = next(iter(report_dates))
        if len(report_periods) == 1 and not missing_periods:
            evidence["report_period"] = next(iter(report_periods))
        if not evidence["total_rows"]:
            return _failure(evidence, "invalid_data", "The readiness export contains no data rows.")
        if len(report_dates) > 1 or len(report_periods) > 1:
            return _failure(
                evidence, "invalid_data",
                "The readiness export mixes refresh dates or reporting periods. "
                "Supply one report snapshot so its rows can be assessed together.",
            )
        if duplicate_identities:
            return _failure(
                evidence, "invalid_data",
                "The readiness export contains duplicate user principal names. "
                "Supply one row per exported user to avoid double counting.",
            )

        warnings = evidence["warnings"]
        if missing_dates:
            warnings.append(
                f"{missing_dates} exported row(s) have a missing or invalid refresh date; "
                "the report date is not established for all rows."
            )
        if missing_periods:
            warnings.append(
                f"{missing_periods} exported row(s) have a missing or invalid reporting period; "
                "the reporting period is not established for all rows."
            )
        if missing_identities:
            warnings.append(
                f"{missing_identities} exported row(s) have no user principal name; "
                "uniqueness cannot be verified for those rows."
            )
        for key, column in FLAG_COLUMNS.items():
            if unknown_counts[key] and column not in absent_optional:
                warnings.append(
                    f"{column}: {unknown_counts[key]} unknown value(s), including "
                    f"{invalid_counts[key]} unrecognized value(s); these were not counted as false."
                )
        evidence.update(
            available=True,
            status="warning" if warnings else "available",
            metrics=metrics,
            summary=f"{evidence['total_rows']} exported row(s) assessed. {SCOPE_NOTE}",
        )
        if include_user_details:
            evidence["user_details"] = user_details
        return evidence
    except FileNotFoundError:
        return _failure(evidence, "error", "The readiness export file was not found.")
    except (UnicodeError, csv.Error):
        return _failure(
            evidence, "invalid_data",
            "The readiness CSV could not be decoded or parsed. Export it again as UTF-8 CSV.",
        )
    except (OSError, TypeError, ValueError):
        return _failure(evidence, "error", "The readiness export file could not be read.")
