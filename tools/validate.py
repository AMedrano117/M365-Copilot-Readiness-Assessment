"""Layered validation without tenant access: scenarios, offline rebuild, render-only and comparison.

Every layer reuses production code: declared scenario suites, run_offline_report for
rebuilds, render_snapshot for render-only output, and read_assessment_result for
serialization. A guard blocks and records any network connection, credential token
request or PowerShell launch. Outputs go to a separate engineering folder labelled
engineering validation; retained packages are read, copied and hash-verified, never
changed. See docs/VALIDATION_WORKFLOW.md.

    python tools/validate.py scenarios
    python tools/validate.py rebuild --package "Reports/<customer>/<package>"
    python tools/validate.py render --snapshot "<package>/assessment-results/RUN-....json"
    python tools/validate.py compare BEFORE.json AFTER.json [--mode render]
    python tools/validate.py diagnostics
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
import hashlib
import html.parser
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = '1.0.0'
LABEL = 'ENGINEERING VALIDATION ONLY — NOT CUSTOMER-READY'
MANIFEST = ROOT / 'tests' / 'scenarios' / 'workflow_manifest.json'
LOOPBACK = {'127.0.0.1', '::1', 'localhost'}
POWERSHELL = re.compile(r'(?i)(?:^|[\\/\s"\'])(?:powershell|pwsh)(?:\.exe)?(?=$|[\s"\'])')


class TenantAccessBlocked(RuntimeError):
    """Raised when a validation layer attempts tenant or network access."""


class ValidationError(ValueError):
    """The input package, snapshot or output location cannot be validated safely."""


# ---------------------------------------------------------------- tenant-access guard

@contextmanager
def tenant_access_guard(attempts):
    """Block network connections, credential token requests and PowerShell collectors.

    ``attempts`` receives one entry per blocked attempt, so callers can state
    whether tenant access was attempted. Loopback sockets (used by the event loop
    on Windows) are allowed.
    """
    original_connect = socket.socket.connect
    original_create = socket.create_connection

    def host_of(address):
        return str(address[0] if isinstance(address, tuple) else address).lower()

    def connect(self, address):
        if host_of(address) in LOOPBACK:
            return original_connect(self, address)
        attempts.append({'kind': 'network', 'target': host_of(address)})
        raise TenantAccessBlocked('Network access is blocked in offline validation.')

    def create_connection(address, *args, **kwargs):
        if host_of(address) in LOOPBACK:
            return original_create(address, *args, **kwargs)
        attempts.append({'kind': 'network', 'target': host_of(address)})
        raise TenantAccessBlocked('Network access is blocked in offline validation.')

    def blocked(kind):
        def raiser(*args, **kwargs):
            attempts.append({'kind': kind, 'target': ''})
            raise TenantAccessBlocked(f'{kind} is blocked in offline validation.')
        return raiser

    import subprocess

    class GuardedPopen(subprocess.Popen):
        # Product functions are never patched (the source gate detects that); the process boundary is.
        def __init__(self, args, *rest, **kwargs):
            command = ' '.join(map(str, args)) if isinstance(args, (list, tuple)) else str(args)
            if POWERSHELL.search(command):
                attempts.append({'kind': 'powershell', 'target': ''})
                raise TenantAccessBlocked('PowerShell collectors are blocked in offline validation.')
            super().__init__(args, *rest, **kwargs)

    patches = [patch.object(socket.socket, 'connect', connect), patch('socket.create_connection', create_connection),
               patch('subprocess.Popen', GuardedPopen)]
    try:
        import azure.identity as identity
        for name in ('ClientSecretCredential', 'CertificateCredential', 'InteractiveBrowserCredential',
                     'DeviceCodeCredential', 'DefaultAzureCredential'):
            if hasattr(identity, name):
                patches.append(patch.object(getattr(identity, name), 'get_token', blocked('authentication')))
    except ImportError:
        pass
    for item in patches:
        item.start()
    try:
        yield attempts
    finally:
        for item in reversed(patches):
            item.stop()


# ---------------------------------------------------------------- hashing and locations

def tree_hashes(folder):
    folder = Path(folder)
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob('*')) if path.is_file()}


def package_digest(hashes):
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode('utf-8')).hexdigest()


def engineering_output(output=None, *, protected=()):
    """A new engineering folder outside every protected input and the repository's Reports."""
    if output:
        folder = Path(output).resolve()
        if folder.exists() and any(folder.iterdir()):
            raise ValidationError('The engineering output folder must be new or empty.')
    else:
        folder = Path(tempfile.mkdtemp(prefix='assessment-validation-'))
    for item in [ROOT / 'Reports', *protected]:
        item = Path(item).resolve()
        if folder == item or folder.is_relative_to(item):
            raise ValidationError('Engineering output cannot be written inside a retained package or the Reports folder.')
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'ENGINEERING-VALIDATION.txt').write_text(LABEL + '\nNot a customer deliverable. No assessment history, '
                                                      'baseline or lifecycle delta is recorded here.\n', encoding='utf-8')
    return folder


def engineering_revision(purpose):
    from Core.repository_provenance import capture_revision, publication_policy
    revision = capture_revision(ROOT)
    revision['publication'] = publication_policy(revision, policy='engineering-validation', purpose=purpose,
                                                 reason='Layered validation without tenant access', acknowledged=True)
    if revision['publication']['errors']:
        raise ValidationError('Engineering source policy blocked: ' + ', '.join(revision['publication']['errors']))
    return revision


# ---------------------------------------------------------------- semantic and presentation comparison

def semantic_view(result):
    """The conclusions a rendering must never change, from the shared result only."""
    rows = {}
    for row in result.get('recommendations') or []:
        rows[row.get('RecommendationId')] = {key: row.get(key) for key in (
            'FindingKey', 'FindingFingerprint', 'Feature', 'Disposition', 'Status', 'Priority', 'ReadinessTreatment',
            'ActionLane', 'ControlId', 'EvidenceKey', 'EvidenceId', 'EvidenceStatus', 'ObservationDate', 'FindingClass')}
    return {
        'decision': result.get('decision'), 'evaluation_date': result.get('evaluation_date'),
        'controls': {row['control_id']: {key: row.get(key) for key in ('status', 'configuration_result', 'operational_result')}
                     for row in result.get('controls') or []},
        'recommendations': rows,
        'actions': sorted(str(row.get('RecommendationId')) for row in result.get('actions') or []),
        'blockers': sorted(str(row.get('RecommendationId')) for row in result.get('blockers') or []),
        'customer_actions': {item.get('customer_action_id'): {'lane': item.get('lane'), 'priority': item.get('priority'),
                                                              'members': [m.get('recommendation_id') for m in item.get('members') or []]}
                             for item in result.get('customer_actions') or []},
        'counts': result.get('counts') or {},
        'identity': {key: (result.get('identity') or {}).get(key) for key in ('AssessmentId', 'RunId', 'BaselineRunId')},
        'lifecycle': {key: _stable_lifecycle(result.get(key)) for key in ('delta', 'lifecycle', 'baseline_comparison') if key in result},
    }


# Run identity and generation time; a new rebuild run changes these without changing a conclusion.
RUN_FIELDS = frozenset({'AssessmentId', 'RunId', 'PrimaryEnvironmentId', 'GeneratedAt', 'generated_at'})


def _stable_lifecycle(value):
    if isinstance(value, dict):
        return {key: _stable_lifecycle(item) for key, item in value.items() if key not in RUN_FIELDS}
    if isinstance(value, list):
        return [_stable_lifecycle(item) for item in value]
    return value


def _diff(before, after, fields=None):
    changed = {}
    for key in sorted(set(before) | set(after), key=str):
        if key not in before:
            changed[key] = {'change': 'added'}
        elif key not in after:
            changed[key] = {'change': 'removed'}
        else:
            values = {field: (before[key].get(field), after[key].get(field)) for field in (fields or before[key])
                      if before[key].get(field) != after[key].get(field)} if isinstance(before[key], dict) else (
                {'value': (before[key], after[key])} if before[key] != after[key] else {})
            if values:
                changed[key] = {'change': 'changed', 'fields': values}
    return changed


def compare_results(before, after, *, mode='rebuild', collected_at=None):
    """Semantic differences, stable-ID changes and unexpected regressions between two results."""
    a, b = semantic_view(before), semantic_view(after)
    controls = _diff(a['controls'], b['controls'])
    findings = _diff(a['recommendations'], b['recommendations'])
    identity_fields = ('FindingKey', 'FindingFingerprint')
    stable = {rid: item for rid, item in findings.items()
              if item['change'] != 'changed' or set(item['fields']) & set(identity_fields)}
    customer = _diff(a['customer_actions'], b['customer_actions'])
    by_field = lambda name: sorted(rid for rid, item in findings.items() if item['change'] == 'changed' and name in item['fields'])
    semantic = {
        'decision': (a['decision'], b['decision']) if a['decision'] != b['decision'] else None,
        'evaluation_date': (a['evaluation_date'], b['evaluation_date']) if a['evaluation_date'] != b['evaluation_date'] else None,
        'controls_changed': controls, 'findings_changed': findings,
        'actions_added': sorted(set(b['actions']) - set(a['actions'])), 'actions_removed': sorted(set(a['actions']) - set(b['actions'])),
        'blockers': (a['blockers'], b['blockers']) if a['blockers'] != b['blockers'] else None,
        'customer_actions_changed': customer,
        'priorities_changed': by_field('Priority'), 'readiness_treatment_changed': by_field('ReadinessTreatment'),
        'evidence_references_changed': by_field('EvidenceId'), 'stable_ids_changed': stable,
        # A rebuild is a new Standalone run with its own identity; only a render must keep it.
        'identity': (a['identity'], b['identity']) if mode == 'render' and a['identity'] != b['identity'] else None,
        'lifecycle': (a['lifecycle'], b['lifecycle']) if a['lifecycle'] != b['lifecycle'] else None,
    }
    has_semantic = any(value for value in semantic.values())
    regressions = []
    for rid, item in stable.items():
        if item['change'] == 'changed':
            regressions.append(f'Stable ID {rid} now identifies a different finding.')
        elif item['change'] == 'removed' and (a['recommendations'][rid].get('Disposition') in {'Action', 'Coverage'}):
            regressions.append(f'Open finding {rid} is no longer reported.')
    if semantic['evaluation_date']:
        regressions.append('The evaluation date changed; a rebuild must keep the retained evaluation date.')
    limit = str(collected_at or '')[:10]
    if limit:
        late = sorted(rid for rid, row in b['recommendations'].items()
                      if str(row.get('ObservationDate') or '')[:10] > limit)
        if late:
            regressions.append(f'{len(late)} finding(s) carry an observation date after the retained collection date.')
    if mode == 'render' and has_semantic:
        regressions.insert(0, 'Render-only output changed semantic results.')
    if mode == 'render' and semantic['identity']:
        regressions.append('Render-only output changed assessment or run identity.')
    return {'mode': mode, 'semantic_differences': has_semantic, 'semantic': semantic, 'unexpected_regressions': regressions,
            'run_identity': {'before': a['identity'], 'after': b['identity'], 'new_run': a['identity'] != b['identity']},
            'versions': {'before': _versions(before), 'after': _versions(after)}}


def _versions(result):
    revision = result.get('producing_revision') or {}
    return {'methodology': result.get('methodology_version') or revision.get('methodology_version'),
            'taxonomy': revision.get('taxonomy_version'), 'commit': revision.get('commit_sha')}


class _Text(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in {'script', 'style'}

    def handle_endtag(self, tag):
        self.skip -= tag in {'script', 'style'} and self.skip > 0

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(' '.join(data.split()))


VOLATILE = re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?|\b[0-9a-f]{40}\b|\b[0-9a-f]{7}\b')


def html_fingerprint(path):
    """Opens the HTML and hashes its visible text and markup, ignoring timestamps and commit IDs."""
    source = Path(path).read_text(encoding='utf-8')
    parser = _Text()
    parser.feed(source)
    parser.close()
    text = VOLATILE.sub('', '\n'.join(parser.parts))
    markup = VOLATILE.sub('', source)
    return {'text': hashlib.sha256(text.encode()).hexdigest(), 'markup': hashlib.sha256(markup.encode()).hexdigest(),
            'visible_lines': len(parser.parts)}


def workbook_fingerprint(path):
    """Opens the workbook (integrity checked) and summarizes each sheet."""
    from openpyxl import load_workbook
    from tools.audit_workbook import _compatibility_issues
    workbook = load_workbook(path, data_only=True, read_only=False)
    try:
        issues = _compatibility_issues(path, workbook)
        sheets = {}
        for sheet in workbook.worksheets:
            values = [VOLATILE.sub('', str(cell)) for row in sheet.iter_rows(values_only=True) for cell in row if cell is not None]
            sheets[sheet.title] = {'rows': sheet.max_row, 'columns': sheet.max_column,
                                   'content': hashlib.sha256('\x1f'.join(values).encode()).hexdigest()}
    finally:
        workbook.close()
    return {'issues': issues, 'sheets': sheets}


def output_files(folder):
    folder = Path(folder)
    return sorted(path for path in folder.rglob('*') if path.is_file() and path.suffix.lower() in {'.html', '.xlsx'})


def _role(path):
    name = Path(path).name.lower()
    return ('summary' if name.startswith('readiness summary') else 'technical' if name.startswith('technical evidence')
            else 'report') + Path(path).suffix.lower()


def fingerprint_outputs(folder, *, skip=()):
    entries, issues = {}, []
    for path in output_files(folder):
        if any(Path(path).is_relative_to(Path(item)) for item in skip):
            continue
        try:
            fingerprint = html_fingerprint(path) if path.suffix.lower() == '.html' else workbook_fingerprint(path)
        except Exception as exc:  # an output that cannot open is a validation failure, never a crash
            issues.append(f'{path.name} could not be opened: {type(exc).__name__}')
            continue
        issues += [f'{path.name}: {issue}' for issue in fingerprint.get('issues') or []]
        entries.setdefault(_role(path), []).append({'file': str(path), **fingerprint})
    return entries, issues


def compare_outputs(before, after):
    """Presentation differences by output role; content hashes ignore timestamps and commit IDs."""
    differences = []
    for role in sorted(set(before) | set(after)):
        old, new = (before.get(role) or [None])[-1], (after.get(role) or [None])[-1]
        if old is None or new is None:
            differences.append(f'{role}: {"added" if old is None else "removed"}')
        elif role.endswith('.html') and old['text'] != new['text']:
            differences.append(f'{role}: visible text changed')
        elif role.endswith('.html') and old['markup'] != new['markup']:
            differences.append(f'{role}: markup or styling changed')
        elif role.endswith('.xlsx'):
            for sheet in sorted(set(old['sheets']) | set(new['sheets'])):
                a, b = old['sheets'].get(sheet), new['sheets'].get(sheet)
                if a != b:
                    differences.append(f"{role} sheet '{sheet}': " + ('added' if a is None else 'removed' if b is None else
                                       f"rows {a['rows']} -> {b['rows']}" if a['rows'] != b['rows'] else 'content changed'))
    return differences


# ---------------------------------------------------------------- package inputs

def retained_package(package):
    """The retained completed result of a package, integrity-checked by the production reader."""
    from Core.assessment_replay import load_completed_package
    from Core.assessment_serialization import read_assessment_result
    from Core.offline_collection import load_collection
    folder = Path(package).resolve()
    collection = folder / 'collection.json'
    if not folder.is_dir() or not collection.is_file():
        raise ValidationError('The package folder must contain collection.json.')
    try:
        payload = load_collection(collection)
        payload = load_completed_package(dict(payload, package_directory=str(folder)))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ValidationError(f'The package could not be read safely: {exc}') from exc
    if not payload.get('completed_run_snapshot'):
        raise ValidationError('The package has no completed assessment result to compare with.')
    try:
        result = read_assessment_result(payload['completed_run_snapshot'])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ValidationError(f'The retained assessment result is malformed: {exc}') from exc
    return folder, collection, payload, result


# ---------------------------------------------------------------- layers

def run_scenarios(names=None, *, stream=None):
    """The declared scenario layer: referenced tests run in-process with tenant access blocked."""
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    selected = [item for item in manifest['scenarios'] if not names or item['name'] in names]
    tests_dir = str(ROOT / 'tests')
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    suite = unittest.TestSuite()
    for item in selected:
        for test in item['tests']:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromName(test))
    attempts = []
    with tenant_access_guard(attempts):
        outcome = unittest.TextTestRunner(stream=stream or sys.stderr, verbosity=1).run(suite)
    return {'scenarios': [item['name'] for item in selected], 'tests_run': outcome.testsRun,
            'failures': len(outcome.failures) + len(outcome.errors), 'tenant_access_attempts': attempts,
            'passed': outcome.wasSuccessful() and not attempts}


def render_only(snapshot, output=None, *, purpose='Render-only validation', compare_to=None, revision=None):
    """Render HTML and Excel from an existing snapshot; any semantic difference fails validation."""
    from Core.assessment_replay import render_snapshot
    from Core.assessment_serialization import read_assessment_result
    from Core.repository_provenance import producing_context
    source = Path(snapshot).resolve()
    if not source.is_file():
        raise ValidationError('The snapshot does not exist.')
    try:
        original = read_assessment_result(source)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ValidationError(f'The snapshot is malformed: {exc}') from exc
    package = source.parent.parent if source.parent.name == 'assessment-results' else source.parent
    before_hashes = tree_hashes(package)
    folder = engineering_output(output, protected=[package])
    revision = revision or engineering_revision(purpose)
    attempts, log = [], io.StringIO()
    rendered = folder / 'render'
    copy = folder / 'rendered-snapshot.json'
    try:
        with tenant_access_guard(attempts), producing_context(revision), redirect_stdout(log):
            outputs = render_snapshot(source, rendered, report_format='both', snapshot_output=copy, retained_package=package)
    except ValueError as exc:
        raise ValidationError(f'Render-only validation was blocked: {exc}') from exc
    after = read_assessment_result(copy)
    comparison = compare_results(original, after, mode='render')
    fingerprints, open_issues = fingerprint_outputs(rendered)
    presentation = compare_outputs(fingerprint_outputs(compare_to)[0], fingerprints) if compare_to else []
    history = [str(path) for path in folder.rglob('*') if path.name in {'assessment-history.json', 'assessment-run.json'}]
    failures = list(comparison['unexpected_regressions']) + open_issues
    if history:
        failures.append('Render-only output created assessment history or a run manifest.')
    if tree_hashes(package) != before_hashes:
        failures.append('The source package changed during render-only validation.')
    if attempts:
        failures.append('Tenant access was attempted.')
    report = _report('render', source=source, package=package, package_hashes=before_hashes, result=original,
                     comparison=comparison, outputs=fingerprints, presentation=presentation, attempts=attempts,
                     failures=failures, folder=folder)
    report['render_directory'] = outputs['report_directory']
    _write_report(folder, report)
    return report


def offline_rebuild(package, output=None, *, purpose='Offline package validation', fail_on_semantic_change=False, revision=None):
    """Rebuild a copy of a retained package with production code and compare with its retained result."""
    from Core.assessment_serialization import read_assessment_result
    from Core.cli_parser import parse_arguments
    from Core.offline_report import run_offline_report
    from Core.repository_provenance import producing_context
    folder_in, _, payload, retained = retained_package(package)
    before_hashes = tree_hashes(folder_in)
    folder = engineering_output(output, protected=[folder_in])
    revision = revision or engineering_revision(purpose)
    copy = folder / 'source-copy'
    shutil.copytree(folder_in, copy)
    work = folder / 'rebuild'
    work.mkdir()
    attempts, log = [], io.StringIO()
    previous_cwd, previous_argv = Path.cwd(), sys.argv
    try:
        os.chdir(work)
        sys.argv = ['main.py', '--mode', 'offline', '--run-type', 'Standalone', '--collection-input',
                    str(copy / 'collection.json'), '--color', 'never']
        with tenant_access_guard(attempts), producing_context(revision), redirect_stdout(log):
            code = run_offline_report(parse_arguments(None, []))
    except ValueError as exc:
        # Includes the source gate: editing the checkout during a rebuild blocks it.
        raise ValidationError(f'The offline rebuild was blocked: {exc}') from exc
    finally:
        os.chdir(previous_cwd)
        sys.argv = previous_argv
    failures = []
    snapshots = sorted((work / 'Reports').rglob('assessment-results/*.json')) if (work / 'Reports').is_dir() else []
    if code or len(snapshots) != 1:
        raise ValidationError(f'The offline rebuild did not produce one assessment result (exit {code}).\n' + log.getvalue()[-2000:])
    rebuilt = read_assessment_result(snapshots[0])
    comparison = compare_results(retained, rebuilt, mode='rebuild', collected_at=payload.get('collected_at'))
    rebuilt_package = snapshots[0].parent.parent
    fingerprints, open_issues = fingerprint_outputs(rebuilt_package)
    retained_outputs, _ = fingerprint_outputs(folder_in / 'Deliverables') if (folder_in / 'Deliverables').is_dir() else ({}, [])
    presentation = compare_outputs(retained_outputs, fingerprints)
    replay = render_only(snapshots[0], folder / 'replay', purpose=purpose, revision=revision)
    failures += comparison['unexpected_regressions'] + open_issues
    if not replay['passed']:
        failures.append('Replay of the rebuilt result changed semantic results or failed to render.')
    if tree_hashes(folder_in) != before_hashes:
        failures.append('The retained package changed during validation.')
    if attempts:
        failures.append('Tenant access was attempted.')
    if fail_on_semantic_change and comparison['semantic_differences']:
        failures.append('Semantic differences were found and --fail-on-semantic-change was requested.')
    report = _report('rebuild', source=folder_in, package=folder_in, package_hashes=before_hashes, result=retained,
                     comparison=comparison, outputs=fingerprints, presentation=presentation, attempts=attempts,
                     failures=failures, folder=folder)
    report.update(rebuilt_snapshot=str(snapshots[0]), replay_passed=replay['passed'],
                  retained_collected_at=payload.get('collected_at'), rebuilt_evaluation_date=rebuilt.get('evaluation_date'))
    _write_report(folder, report)
    return report


def _report(mode, *, source, package, package_hashes, result, comparison, outputs, presentation, attempts, failures, folder):
    from Core.repository_provenance import capture_revision
    current = capture_revision(ROOT)
    return {
        'schema_version': VERSION, 'mode': mode, 'label': LABEL, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': str(source), 'source_package': str(package), 'source_package_sha256': package_digest(package_hashes),
        'source_files': len(package_hashes), 'producing_commit': {'retained': _versions(result).get('commit'),
                                                                  'validation': current.get('commit_sha'),
                                                                  'validation_source_state': current.get('source_state')},
        'versions': comparison['versions'], 'semantic_differences': comparison['semantic_differences'],
        'run_identity': comparison['run_identity'],
        'semantic': comparison['semantic'], 'presentation_differences': presentation,
        'output_files': {role: [Path(item['file']).name for item in items] for role, items in outputs.items()},
        'tenant_access_performed': bool(attempts), 'tenant_access_attempts': attempts,
        'unexpected_regressions': comparison['unexpected_regressions'], 'failures': failures,
        'customer_publication_eligible': False, 'history_created': False, 'baseline_selected': False,
        'output_directory': str(folder), 'passed': not failures,
    }


def _write_report(folder, report):
    (Path(folder) / 'comparison.json').write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding='utf-8')
    (Path(folder) / 'comparison.md').write_text('\n'.join(summary_lines(report)) + '\n', encoding='utf-8')


def summary_lines(report):
    semantic = report['semantic']
    count = lambda value: len(value) if isinstance(value, (list, dict)) else (1 if value else 0)
    lines = [f"# {report['label']}", '', f"- Mode: {report['mode']}", f"- Source package: {report['source_package']}",
             f"- Source package SHA-256: {report['source_package_sha256']} ({report['source_files']} files, unchanged: "
             f"{'no' if any('package changed' in item for item in report['failures']) else 'yes'})",
             f"- Producing commit: retained {report['producing_commit']['retained'] or 'unrecorded'}; validation "
             f"{report['producing_commit']['validation'] or 'unrecorded'} ({report['producing_commit']['validation_source_state']})",
             f"- Methodology / taxonomy: {report['versions']['before']['methodology']} / {report['versions']['before']['taxonomy']}"
             f" -> {report['versions']['after']['methodology']} / {report['versions']['after']['taxonomy']}",
             f"- Tenant access performed: {'Yes' if report['tenant_access_performed'] else 'No'}",
             f"- Run identity: {'new engineering run (not a semantic change)' if report.get('run_identity', {}).get('new_run') else 'unchanged'}",
             f"- Decision changed: {'yes' if semantic['decision'] else 'no'}",
             f"- Controls changed: {count(semantic['controls_changed'])}",
             f"- Findings changed: {count(semantic['findings_changed'])}",
             f"- Actions added / removed: {count(semantic['actions_added'])} / {count(semantic['actions_removed'])}",
             f"- Customer actions changed: {count(semantic['customer_actions_changed'])}",
             f"- Priorities / readiness treatments changed: {count(semantic['priorities_changed'])} / "
             f"{count(semantic['readiness_treatment_changed'])}",
             f"- Evidence references changed: {count(semantic['evidence_references_changed'])}",
             f"- Stable IDs changed: {count(semantic['stable_ids_changed'])}",
             f"- Semantic differences: {'yes' if report['semantic_differences'] else 'none'}",
             f"- Presentation-only differences: {len(report['presentation_differences'])}",
             f"- Output files: {', '.join(name for names in report['output_files'].values() for name in names) or 'none'}"]
    for title, items in (('Controls changed', semantic['controls_changed']),):
        if items:
            lines += ['', f'## {title}'] + [f"- {key}: {value.get('fields', value.get('change'))}" for key, value in items.items()]
    if report['presentation_differences']:
        lines += ['', '## Presentation differences'] + [f'- {item}' for item in report['presentation_differences']]
    lines += ['', '## Unexpected regressions'] + ([f'- {item}' for item in report['unexpected_regressions']] or ['- None'])
    lines += ['', f"Result: {'PASSED' if report['passed'] else 'FAILED'}"] + [f'- {item}' for item in report['failures']]
    return lines


DIAGNOSTICS = (
    ('Connection preflight', 'python main.py --mode live --env-file <customer.env> --check-connections',
     'Live: read-only probes of each selected source; no assessment output.'),
    ('Microsoft 365 usage reports', 'python tools/diagnose-usage-report.py --env-file <customer.env> [--matrix]',
     'Live: per-report download diagnostic; no assessment output.'),
    ('Usage report download (PowerShell)', 'tools/diagnose-usage-report-download.ps1', 'Live: redirected download path only.'),
    ('Intune evidence', 'tools/Test-IntuneEvidence.ps1', 'Live, read-only GET requests; identifiers omitted from output.'),
    ('Synthetic package audit', 'python tools/validate_offline_report.py', 'No tenant access: fictional package build and output audit.'),
    ('Existing HTML signal audit', 'python tools/audit_report_signal.py <report.html>', 'No tenant access: reads an existing report.'),
    ('Workbook audit', 'python tools/audit_workbook.py <workbook.xlsx>', 'No tenant access: privacy-safe workbook counts.'),
)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Layered validation without tenant access.')
    commands = parser.add_subparsers(dest='command', required=True)
    scenarios = commands.add_parser('scenarios', help='Run the declared fictional scenario layer.')
    scenarios.add_argument('--name', action='append', help='Run only the named scenario group; repeatable.')
    rebuild = commands.add_parser('rebuild', help='Rebuild a copy of a retained package offline and compare.')
    rebuild.add_argument('--package', required=True)
    rebuild.add_argument('--output')
    rebuild.add_argument('--fail-on-semantic-change', action='store_true')
    render = commands.add_parser('render', help='Render HTML and Excel from an existing snapshot only.')
    render.add_argument('--snapshot', required=True)
    render.add_argument('--output')
    render.add_argument('--compare-to', help='An earlier render folder; reports presentation differences.')
    compare = commands.add_parser('compare', help='Compare two assessment-result snapshots.')
    compare.add_argument('before')
    compare.add_argument('after')
    compare.add_argument('--mode', choices=['rebuild', 'render'], default='rebuild')
    commands.add_parser('diagnostics', help='List focused diagnostics and whether they access a tenant.')
    args = parser.parse_args(argv)
    try:
        if args.command == 'scenarios':
            outcome = run_scenarios(args.name)
            print(f"Scenario groups: {len(outcome['scenarios'])}; tests: {outcome['tests_run']}; failures: {outcome['failures']}; "
                  f"tenant access performed: {'Yes' if outcome['tenant_access_attempts'] else 'No'}.")
            return 0 if outcome['passed'] else 1
        if args.command == 'diagnostics':
            for name, command, access in DIAGNOSTICS:
                print(f'{name}\n  {command}\n  {access}')
            return 0
        if args.command == 'compare':
            from Core.assessment_serialization import read_assessment_result
            comparison = compare_results(read_assessment_result(args.before), read_assessment_result(args.after), mode=args.mode)
            print(json.dumps(comparison, indent=2, sort_keys=True, default=str))
            return 1 if comparison['unexpected_regressions'] else 0
        report = (offline_rebuild(args.package, args.output, fail_on_semantic_change=args.fail_on_semantic_change)
                  if args.command == 'rebuild' else render_only(args.snapshot, args.output, compare_to=args.compare_to))
        print('\n'.join(summary_lines(report)))
        print(f"\nReport: {Path(report['output_directory']) / 'comparison.md'}")
        return 0 if report['passed'] else 1
    except ValidationError as exc:
        print(f'Validation input error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
