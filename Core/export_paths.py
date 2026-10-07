"""Shared destinations for customer assessments and their report builds."""

import re
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path

REPORT_STEM = 'AI Readiness and M365 Hardening'
SUMMARY_STEM = 'Readiness Summary'
EVIDENCE_FOLDER = 'Evidence'
BUILDS_FOLDER = 'Builds'


def customer_folder_name(customer_name=None, tenant_name=None, tenant_id=None):
    """Use one safe directory component, including on Windows."""
    label = next((str(value).strip() for value in (customer_name, tenant_name, tenant_id)
                  if value and str(value).strip()), 'Customer')
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', ' ', label.replace('_', ' '))
    label = re.sub(r'\s+', ' ', label).strip(' .') or 'Customer'
    # Windows reserves device names even when followed by a file extension.
    if re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])', label.split('.')[0].rstrip()):
        label = 'Customer ' + label
    if len(label.encode('utf-16-le')) // 2 > 32:
        prefix, units = '', 0
        for character in label:
            width = len(character.encode('utf-16-le')) // 2
            if units + width > 23:
                break
            prefix += character
            units += width
        label = prefix.rstrip(' .') + ' ' + sha256(label.encode('utf-8')).hexdigest()[:8]
    return label


def customer_reports_directory(customer_name=None, tenant_name=None, tenant_id=None):
    return Path('Reports') / customer_folder_name(customer_name, tenant_name, tenant_id)


def report_file_stem(stem, *, customer_name=None, tenant_name=None, tenant_id=None):
    """Identify reports with the same short, safe customer label as their folder."""
    if not any(value and str(value).strip() for value in (customer_name, tenant_name, tenant_id)):
        return stem
    return f'{stem} - {customer_folder_name(customer_name, tenant_name, tenant_id)}'


def new_numbered_directory(parent):
    """Reserve a short sequence number without replacing any previous build."""
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        folder = parent / str(number)
        try:
            folder.mkdir(exist_ok=False)
        except FileExistsError:
            number += 1
            continue
        return folder.resolve()


def new_assessment_directory(*, customer_name=None, tenant_name=None, tenant_id=None):
    parent = customer_reports_directory(customer_name, tenant_name, tenant_id)
    parent.mkdir(parents=True, exist_ok=True)
    assessment_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    number = 1
    while True:
        folder = parent / (assessment_date if number == 1 else f'{assessment_date} ({number})')
        try:
            folder.mkdir(exist_ok=False)
        except FileExistsError:
            number += 1
            continue
        return folder.resolve()


def new_deliverables_directory(package_directory):
    return new_numbered_directory(Path(package_directory) / BUILDS_FOLDER)


def report_directory(output_dir=None, *, customer_name=None, tenant_name=None, tenant_id=None):
    """Direct exporters use the customer folder; report builds supply their run folder."""
    folder = Path(output_dir) if output_dir is not None else customer_reports_directory(
        customer_name, tenant_name, tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder
