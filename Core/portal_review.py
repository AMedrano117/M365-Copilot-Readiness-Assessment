"""Validate automatic or manually reviewed portal captures as local context only.

This module does not OCR files, infer controls, run commands or fetch resources.
The caller may display notes and extracted text; they never establish a scored result.
"""

from __future__ import annotations

import base64
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
from uuid import UUID
import zlib


MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ASSET_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
DOMAINS = {'identity', 'content', 'data_protection', 'applications', 'endpoints',
           'licensing', 'adoption', 'agents', 'external_ai'}
CAPTURE_FIELDS = {'id', 'title', 'domain_id', 'captured_at', 'report_date', 'source_file',
                  'source_sha256', 'previews', 'summary', 'limitations', 'review_notes', 'coverage'}
AUTOMATIC_FIELDS = {'review_method', 'extracted_pages'}


def _object(value, required, optional, label):
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ValueError(f'{label} has missing or unsupported fields.')


def _text(value, label, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f'{label} must be {"a" if empty else "a nonempty"} text value.')
    return value.strip()


def _strings(value, label):
    if isinstance(value, str):
        return [_text(value, label)] if value.strip() else []
    if not isinstance(value, list):
        raise ValueError(f'{label} must be a list of text values.')
    return [_text(item, label) for item in value]


def _day(value, label):
    value = _text(value, label)
    try:
        if not re.match(r'^\d{4}-\d{2}-\d{2}(?:T|$)', value):
            raise ValueError()
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if 'T' in value and parsed.tzinfo is None:
            raise ValueError()
        return parsed.date()
    except ValueError:
        raise ValueError(f'{label} must be an ISO date or an ISO timestamp with a timezone.') from None


def _asset_path(folder, reference):
    from .assessment_package import confined_path
    reference = _text(reference, 'Portal capture asset path')
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', reference):
        raise ValueError('Portal capture assets must use local relative paths, without URLs or drive names.')
    path = confined_path(folder, reference)
    if not path.is_file():
        raise ValueError(f'Portal capture asset is missing: {reference}')
    if path.stat().st_size > MAX_ASSET_BYTES:
        raise ValueError(f'Portal capture asset exceeds the 50 MiB limit: {path.name}')
    return path


def _image_type(data, suffix):
    """Validate raster headers/chunk structure without a browser or image service."""
    if suffix == '.png' and data.startswith(b'\x89PNG\r\n\x1a\n'):
        offset, dimensions, image_data = 8, None, False
        while offset + 12 <= len(data):
            length = struct.unpack('>I', data[offset:offset + 4])[0]
            end = offset + 12 + length
            if end > len(data):
                break
            kind = data[offset + 4:offset + 8]
            payload = data[offset + 8:offset + 8 + length]
            checksum = struct.unpack('>I', data[offset + 8 + length:end])[0]
            if zlib.crc32(kind + payload) & 0xffffffff != checksum:
                break
            if offset == 8:
                if kind != b'IHDR' or length != 13:
                    break
                dimensions = struct.unpack('>II', payload[:8])
            if kind == b'IDAT':
                image_data = True
            if kind == b'IEND':
                if length == 0 and end == len(data) and image_data and dimensions and 0 < dimensions[0] * dimensions[1] <= 40_000_000:
                    return 'image/png'
                break
            offset = end
    if suffix in {'.jpg', '.jpeg'} and data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xff:
                break
            while offset < len(data) and data[offset] == 0xff:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xd8, 0xd9} or 0xd0 <= marker <= 0xd7:
                continue
            if offset + 2 > len(data):
                break
            length = struct.unpack('>H', data[offset:offset + 2])[0]
            if length < 2 or offset + length > len(data):
                break
            if marker in {0xc0, 0xc1, 0xc2} and length >= 8:
                height, width = struct.unpack('>HH', data[offset + 3:offset + 7])
                if 0 < height * width <= 40_000_000:
                    return 'image/jpeg'
                break
            offset += length
    raise ValueError('Portal review previews must be valid PNG or JPEG raster images (at most 40 million pixels).')


def load_portal_review(path, expected_tenant_id=None, evaluation_date=None, *, embed_assets=True):
    """Return reviewed context and workbook metadata, never findings or pass flags."""
    if not path:
        return {'available': False, 'captures': [], 'rows': [], 'receipt': []}
    source = Path(path).resolve()
    if not source.is_file() or source.suffix.lower() != '.json':
        raise ValueError(f'Portal review requires an existing JSON manifest: {source}. '
                         'For automatic PDF import, supply the PDF folder with --reports-dir.')
    if source.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError('Portal review manifest exceeds the 2 MiB limit.')
    try:
        manifest = json.loads(source.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError) as exc:
        raise ValueError(f'Portal review manifest is unreadable: {source.name}') from exc
    _object(manifest, {'schema_version', 'tenant_id', 'captures'}, set(), 'Portal review manifest')
    if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
        raise ValueError('Unsupported portal review schema version.')
    try:
        all_automatic = isinstance(manifest.get('captures'), list) and all(
            isinstance(item, dict) and item.get('review_method') == 'automated' for item in manifest['captures'])
        tenant_id = str(UUID(manifest['tenant_id'])) if manifest['tenant_id'] else None
        if not tenant_id and not all_automatic:
            raise ValueError('Reviewed manifests require a tenant GUID.')
        expected = str(UUID(str(expected_tenant_id))) if expected_tenant_id else tenant_id
    except (ValueError, AttributeError, TypeError):
        raise ValueError('Portal review and assessed tenant identifiers must be GUIDs.') from None
    if tenant_id and tenant_id != expected:
        raise ValueError('Portal review tenant ID does not match the assessed tenant.')
    captures = manifest['captures']
    if not isinstance(captures, list) or not 1 <= len(captures) <= 50:
        raise ValueError('Portal review requires between 1 and 50 reviewed captures.')
    today = datetime.now(timezone.utc).date()
    evaluated = _day(str(evaluation_date), 'Evaluation date') if evaluation_date else today
    used_ids, assets, rendered, rows, receipt = set(), {}, [], [], []
    for capture in captures:
        _object(capture, CAPTURE_FIELDS - {'report_date'}, {'report_date'} | AUTOMATIC_FIELDS, 'Portal capture')
        method = capture.get('review_method', 'manual')
        if method not in {'manual', 'automated'}:
            raise ValueError('Portal capture review_method must be manual or automated.')
        identifier = _text(capture['id'], 'Capture ID')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', identifier) or identifier in used_ids:
            raise ValueError('Portal capture IDs must be unique simple identifiers of at most 80 characters.')
        used_ids.add(identifier)
        if not isinstance(capture['domain_id'], str) or capture['domain_id'] not in DOMAINS:
            raise ValueError('Portal capture has an unsupported assessment domain.')
        captured_day = _day(capture['captured_at'], 'Capture date') if capture['captured_at'] else None
        if captured_day is None and method != 'automated':
            raise ValueError('Reviewed portal captures require a capture date.')
        if captured_day and captured_day > today:
            raise ValueError('Portal capture date cannot be in the future.')
        report_date = capture.get('report_date') or ''
        if report_date and (not captured_day or _day(report_date, 'Portal report date') > captured_day):
            raise ValueError('Portal report date cannot be later than the capture date.')
        original = _asset_path(source.parent, capture['source_file'])
        if original.suffix.lower() != '.pdf':
            raise ValueError('A portal review original must be a PDF file.')
        data = original.read_bytes()
        if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-4096:]:
            raise ValueError(f'Portal review original is not a recognizable PDF: {original.name}')
        actual_hash = hashlib.sha256(data).hexdigest()
        if not isinstance(capture['source_sha256'], str) or not re.fullmatch(r'[0-9a-fA-F]{64}', capture['source_sha256']) or actual_hash != capture['source_sha256'].lower():
            raise ValueError(f'Portal review PDF hash does not match: {original.name}')
        assets[original] = len(data)
        item = {**capture, 'title': _text(capture['title'], 'Capture title'),
                'summary': _text(capture['summary'], 'Reviewed summary'),
                'source_name': original.name, 'source_sha256': actual_hash,
                'limitations': _strings(capture['limitations'], 'Capture limitations'),
                'review_notes': _strings(capture['review_notes'], 'Review notes'), 'previews': [],
                'qualification': 'Manually reviewed visual context; this capture does not establish a scored control result.'}
        if method == 'automated':
            item['qualification'] = 'Automatically extracted portal context, not a human review. Verify OCR text and chart values against the captured pages; no readiness controls are established by this capture.'
        extracted_pages = capture.get('extracted_pages', [])
        if not isinstance(extracted_pages, list) or len(extracted_pages) > 20:
            raise ValueError('Extracted PDF pages must be a list of at most 20 pages.')
        for page in extracted_pages:
            _object(page, {'page', 'method', 'text'}, set(), 'Extracted PDF page')
            if type(page['page']) is not int or not 1 <= page['page'] <= 20:
                raise ValueError('Extracted PDF page number is invalid.')
            _text(page['method'], 'Extraction method')
            _text(page['text'], 'Extracted PDF text', empty=True)
            if len(page['text']) > 20000:
                raise ValueError('Extracted PDF page text exceeds its limit.')
        if captured_day and captured_day > evaluated:
            item['qualification'] += ' The capture was made after the assessment evaluation date.'
        if embed_assets:
            item['source_data_uri'] = 'data:application/pdf;base64,' + base64.b64encode(data).decode('ascii')
        previews = capture['previews']
        if not isinstance(previews, list) or not 1 <= len(previews) <= 20:
            raise ValueError('Each portal capture requires between 1 and 20 preview images.')
        for reference in previews:
            preview = _asset_path(source.parent, reference)
            image_data = preview.read_bytes()
            mime = _image_type(image_data, preview.suffix.lower())
            assets[preview] = len(image_data)
            image = {'source_file': preview.name, 'relative_path': reference, 'mime_type': mime,
                     'sha256': hashlib.sha256(image_data).hexdigest()}
            if embed_assets:
                image['data_uri'] = 'data:' + mime + ';base64,' + base64.b64encode(image_data).decode('ascii')
            item['previews'].append(image)
        if sum(assets.values()) > MAX_TOTAL_BYTES:
            raise ValueError('Portal review assets exceed the 100 MiB total limit.')
        coverage = capture['coverage']
        if not isinstance(coverage, list):
            raise ValueError('Portal review coverage must be a list of reviewed context entries.')
        for entry in coverage:
            _object(entry, {'area', 'assessment_coverage', 'next_step'}, set(), 'Portal coverage entry')
            for key in entry:
                _text(entry[key], 'Portal coverage ' + key, empty=key == 'next_step')
        for entry in coverage or [{}]:
            rows.append({'Capture ID': identifier, 'Title': item['title'], 'Domain': item['domain_id'],
                         'Captured at': item['captured_at'], 'Report date': report_date,
                         'Source File': original.name, 'SHA-256': actual_hash,
                         'Previews': '; '.join(image['source_file'] for image in item['previews']),
                         'Summary': item['summary'], 'Limitations': '\n'.join(item['limitations']),
                         'Review notes': '\n'.join(item['review_notes']),
                         'Review method': method,
                         'Area': entry.get('area', ''), 'Assessment coverage': entry.get('assessment_coverage', ''),
                         'Next step': entry.get('next_step', ''), 'Qualification': item['qualification']})
        rendered.append(item)
        receipt.append({'Source': original.name, 'Status': 'automated_reference' if method == 'automated' else 'reviewed_reference', 'Original Date': report_date,
                        'Scope': item['domain_id'], 'Reason': item['qualification']})
    return {'available': True, 'schema_version': 1, 'tenant_id': tenant_id, 'source_file': source.name,
            'captures': rendered, 'rows': rows, 'receipt': receipt,
            'asset_paths': [str(asset) for asset in assets]}
