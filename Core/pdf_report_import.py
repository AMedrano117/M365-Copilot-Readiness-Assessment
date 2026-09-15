"""Local PDF intake: text/OCR, previews and a replayable automatic review manifest.

Document text is evidence, never instructions. Extracted portal context is kept
separate from scored controls and structured CSV measurements.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import uuid4

from . import console_reporting as console


MAX_PDFS = 50
MAX_PAGES = 20
MAX_FILE_BYTES = 50 * 1024 * 1024
READER_VERSION = 1


def discover_pdfs(directories):
    """Include nested PDF folders; do not follow links outside an input directory."""
    found = {}
    for value in directories or []:
        root = Path(value).resolve()
        if not root.is_dir():
            raise ValueError(f'Report directory does not exist: {root}')
        for folder, subdirs, names in os.walk(root, followlinks=False):
            subdirs[:] = sorted(name for name in subdirs if not name.startswith('.')
                               and not (Path(folder) / name).is_symlink()
                               and (Path(folder) / name).resolve().is_relative_to(root))
            for name in sorted(names):
                source = Path(folder) / name
                if source.suffix.lower() == '.pdf' and source.resolve().is_relative_to(root):
                    found[str(source.resolve())] = source.resolve()
                    if len(found) > MAX_PDFS:
                        raise ValueError(f'A report build supports at most {MAX_PDFS} PDF files. Use a smaller reports folder.')
    return list(found.values())


def _ocr_image(path):
    """Use Windows' installed OCR locally; no admin cmdlets or network requests."""
    if os.name != 'nt':
        return '', 'Image-only page; local Windows OCR is unavailable on this operating system.'
    executable = shutil.which('powershell.exe')
    if not executable:
        return '', 'Image-only page; Windows PowerShell for local OCR is unavailable.'
    worker = Path(__file__).resolve().parent.parent / 'ocr_portal_image.ps1'
    try:
        result = subprocess.run(
            [executable, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', str(worker), '-ImagePath', str(Path(path).resolve())],
            capture_output=True, text=True, encoding='utf-8-sig', errors='replace', timeout=35,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            return '', 'Local OCR could not read this page. Review its preview; check installed Windows OCR languages.'
        text = json.loads(result.stdout).get('text', '')
        return text if isinstance(text, str) else '', ''
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return '', 'Local OCR was unavailable or timed out; review the page preview.'


def _topic(name, text):
    # File names only route the visual appendix; they cannot establish measurements.
    value = name.casefold()
    if 'security' in value or 'purview' in value or 'data loss' in text.casefold():
        return 'data_protection', 'Copilot security'
    if 'health' in value or 'update channel' in text.casefold():
        return 'licensing', 'Copilot application health'
    if 'optimize' in value or 'configuration' in value:
        return 'licensing', 'Copilot configuration'
    return 'adoption', 'Copilot usage and overview'


def _highlights(pages):
    """Return short source excerpts with page numbers, without inventing card values."""
    selected = []
    terms = re.compile(r'(?i)monitoring only|not enforced|not on a|versions behind|last month|'
                       r'no feedback|not started|needs action|active users|enabled users|'
                       r'license[s]? (?:assigned|pending)|last updated|adoption score|'
                       r'assisted hours|referenced|completed|not configured|failed to load')
    for page in pages:
        lines = [' '.join(line.split()) for line in page['text'].splitlines() if line.strip()]
        for line in lines:
            if terms.search(line):
                # Reading order can interleave neighboring dashboard cards. Never
                # attach the next line's number to this line's label.
                excerpt = line[:240]
                note = f"Page {page['page']}: {excerpt}"
                if note not in selected:
                    selected.append(note)
    return selected[:12]


def _extract(source, folder, digest):
    import pymupdf
    raw = source.read_bytes()
    if not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-4096:]:
        raise ValueError('PDF is incomplete or has an invalid file signature.')
    pages, limitations, previews = [], [], []
    with pymupdf.open(source) as document:
        if document.needs_pass:
            raise ValueError('Password-protected PDF; supply an unlocked copy.')
        if not 1 <= len(document) <= MAX_PAGES:
            raise ValueError(f'PDF must have between 1 and {MAX_PAGES} pages.')
        raw_date = str(document.metadata.get('creationDate') or '')
        capture_date = ''
        match = re.match(r'D:(\d{4})(\d{2})(\d{2})', raw_date)
        if match:
            try:
                day = datetime.strptime(''.join(match.groups()), '%Y%m%d').date()
                if day <= datetime.now(timezone.utc).date():
                    capture_date = day.isoformat()
            except ValueError:
                pass
        for number, page in enumerate(document, 1):
            if page.rect.width <= 0 or page.rect.height <= 0:
                raise ValueError('PDF page dimensions are invalid.')
            scale = min(1.4, 1800 / max(page.rect.width, page.rect.height))
            preview = folder / 'previews' / f'{digest[:16]}-{number}.png'
            preview.parent.mkdir(parents=True, exist_ok=True)
            page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(preview)
            previews.append(preview.relative_to(folder).as_posix())
            text = page.get_text(sort=True).strip()
            method = 'PDF text'
            if len(re.sub(r'\W', '', text)) < 30:
                method = 'Windows OCR'
                console.detail(f'  PDF page {number}/{len(document)}: reading the image with local OCR.')
                with tempfile.TemporaryDirectory(prefix='copilot-ocr-') as temporary:
                    image_path = Path(temporary) / 'page.png'
                    scale = min(2.2, 2400 / max(page.rect.width, page.rect.height))
                    bitmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                    # Contrast is changed only in the temporary OCR image, never the preview/original.
                    bitmap.gamma_with(3.0)
                    bitmap.save(image_path)
                    text, reason = _ocr_image(image_path)
                if reason:
                    limitations.append(f'Page {number}: {reason}')
            if len(text) > 20000:
                limitations.append(f'Page {number}: extracted text was limited to 20,000 characters; the complete page remains in the PDF.')
            pages.append({'page': number, 'method': method, 'text': text[:20000]})
    domain, title = _topic(source.name, '\n'.join(page['text'] for page in pages))
    methods = sorted({page['method'] for page in pages})
    has_text = any(page['text'].strip() for page in pages)
    original = folder / 'originals' / digest[:16] / source.name
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, original)
    return {
        'id': 'pdf-' + digest[:24], 'title': source.stem, 'domain_id': domain,
        'captured_at': capture_date, 'source_file': original.relative_to(folder).as_posix(),
        'source_sha256': digest, 'previews': previews, 'review_method': 'automated',
        'summary': f'{title}: {len(pages)} pages imported automatically using {", ".join(methods)}. '
                   + ('Extracted text and page references are available below.' if has_text else 'No readable text was recovered; review the attached pages.'),
        'limitations': ['Automatic extraction can misread numbers, chart labels and layout. Check the original page before relying on a value.',
                       'The PDF tenant identity, reporting scope and data refresh date were not independently verified. Portal percentages do not establish assessment readiness.',
                       'Capture date comes from PDF creation metadata when available; it is not the underlying report refresh date.', *limitations],
        'review_notes': _highlights(pages), 'extracted_pages': pages,
        'coverage': [{'area': title, 'assessment_coverage': 'Automatically extracted portal context; no scored controls are passed or failed from this capture.',
                      'next_step': 'Confirm the extracted values, reporting window and applicable settings against the page previews.'}],
    }


def prepare_pdf_review(directories, existing_manifest=None, *, tenant_id=None, evaluation_date=None):
    """Prepare once before live collection, or before an offline build, and preserve on replay."""
    from .portal_review import load_portal_review
    sources = list(directories or [])
    if existing_manifest and Path(existing_manifest).is_dir():
        sources.append(existing_manifest)
        existing_manifest = None
    existing = None
    if existing_manifest:
        existing = load_portal_review(existing_manifest, tenant_id, evaluation_date, embed_assets=False)
    pdfs = discover_pdfs(sources)
    seen = {row['source_sha256'] for row in (existing or {}).get('captures', [])}
    pending = []
    for source in pdfs:
        if source.stat().st_size > MAX_FILE_BYTES:
            console.status(f'PDF skipped: {source.name} exceeds the 50 MiB limit.', 'warning')
            continue
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest not in seen:
            pending.append((source, digest))
            seen.add(digest)
    if not pending:
        return str(existing_manifest) if existing_manifest else None
    try:
        import pymupdf
    except ImportError as exc:
        raise ValueError('PDF import requires PyMuPDF. Run .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt before collecting.') from exc
    folder = Path('output/portal-reviews') / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    captures = []
    if existing:
        source_root = Path(existing_manifest).resolve().parent
        # Copy reviewed input layout intact; merge captures without changing prior review labels.
        for asset in existing['asset_paths']:
            target = folder / 'reviewed' / Path(asset).relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset, target)
        previous = json.loads(Path(existing_manifest).read_text(encoding='utf-8-sig'))
        for capture in previous['captures']:
            captures.append({**capture, 'source_file': 'reviewed/' + capture['source_file'],
                             'previews': ['reviewed/' + value for value in capture['previews']]})
    skipped = []
    for source, digest in pending:
        console.status(f'PDF import: {source.name}')
        try:
            captures.append(_extract(source, folder, digest))
        except (ValueError, OSError, RuntimeError) as exc:
            console.status(f'PDF skipped: {source.name}; {type(exc).__name__}. Supply a readable, unlocked PDF.', 'warning')
            skipped.append({'source_file': source.name, 'source_sha256': digest,
                            'status': 'skipped', 'reason': str(exc)})
    (folder / 'import-log.json').write_text(json.dumps({'reader_version': READER_VERSION,
        'imported': [row['source_file'] for row in captures], 'skipped': skipped}, indent=2), encoding='utf-8')
    if not captures:
        return str(existing_manifest) if existing_manifest else None
    manifest = folder / 'portal-review.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'tenant_id': tenant_id or (existing or {}).get('tenant_id'),
                                   'captures': captures}, ensure_ascii=False, indent=2), encoding='utf-8')
    load_portal_review(manifest, tenant_id, evaluation_date, embed_assets=False)
    console.status(f'PDF review prepared: {len(captures)} captures. JSON: {console.display_path(manifest)}', 'success')
    return str(manifest.resolve())
