"""Layered validation without tenant access: scenarios, offline rebuild, render-only and comparison.

Every layer reuses production code: declared scenario suites, run_offline_report for
rebuilds, render_snapshot for render-only output, and read_assessment_result for
serialization. A guard blocks and records any network connection, credential token
request or PowerShell launch. Outputs go to a separate engineering folder labelled
engineering validation; retained packages are read, copied and hash-verified, never
changed. See docs/VALIDATION_WORKFLOW.md.

Each run reports an execution status (did validation run?) separately from a
comparison status (what did it find?). A semantic difference is expected only when a
declared, verified transition in tools/semantic_transitions.json explains it.

    python tools/validate.py scenarios
    python tools/validate.py rebuild --package "Reports/<customer>/<package>"
    python tools/validate.py render --snapshot "<package>/assessment-results/RUN-....json"
    python tools/validate.py compare BEFORE.json AFTER.json [--mode render]
    python tools/validate.py diagnostics
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
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

VERSION = '1.1.0'
LABEL = 'ENGINEERING VALIDATION ONLY — NOT CUSTOMER-READY'
MANIFEST = ROOT / 'tests' / 'scenarios' / 'workflow_manifest.json'
TRANSITIONS = ROOT / 'tools' / 'semantic_transitions.json'
LOOPBACK = {'127.0.0.1', '::1', 'localhost'}
POWERSHELL = re.compile(r'(?i)(?:^|[\\/\s"\'])(?:powershell|pwsh)(?:\.exe)?(?=$|[\s"\'])')

EXECUTION_STATUSES = ('Completed', 'FailedToExecute', 'InvalidPackage', 'IncompatiblePackage')
COMPARISON_STATUSES = ('ExactMatch', 'PresentationOnlyDifferences', 'ExpectedSemanticDifferences',
                       'UnexpectedSemanticRegression', 'DisplayIdentityConflict')
# Documented in docs/VALIDATION_WORKFLOW.md; a status that is not Completed takes precedence.
EXIT_CODES = {'ExactMatch': 0, 'PresentationOnlyDifferences': 0, 'ExpectedSemanticDifferences': 0,
              'UnexpectedSemanticRegression': 1, 'InvalidPackage': 2, 'DisplayIdentityConflict': 3,
              'FailedToExecute': 4, 'IncompatiblePackage': 5}
EXPECTED_KINDS = frozenset({'methodology_rule', 'taxonomy_rule', 'documented_control_requirement',
                            'corrected_evidence_interpretation', 'required_control_flag',
                            'declared_compatibility_transition'})


class TenantAccessBlocked(RuntimeError):
    """Raised when a validation layer attempts tenant or network access."""


class ValidationError(ValueError):
    """Validation could not run safely; ``status`` is the execution outcome."""
    status = 'FailedToExecute'


class InvalidPackageError(ValidationError):
    """The package or snapshot is missing, malformed or fails its integrity check."""
    status = 'InvalidPackage'


class IncompatiblePackageError(ValidationError):
    """The package is intact but cannot be compared with the current methodology or contract."""
    status = 'IncompatiblePackage'


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
    """A new, uniquely named engineering folder outside every protected input and the repository's Reports.

    Without ``output`` each call gets its own system temporary folder, so concurrent
    validation processes never share files. An explicit ``output`` must be new or empty.
    """
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


def current_versions(revision=None):
    from Core.repository_provenance import runtime_versions
    revision = revision or {}
    runtime = {} if revision.get('methodology_version') and revision.get('taxonomy_version') else runtime_versions(ROOT)
    return {'methodology': revision.get('methodology_version') or runtime.get('methodology_version'),
            'taxonomy': revision.get('taxonomy_version') or runtime.get('taxonomy_version'),
            'commit': revision.get('commit_sha')}


def _versions(result):
    revision = result.get('producing_revision') or {}
    return {'methodology': result.get('methodology_version') or revision.get('methodology_version'),
            'taxonomy': revision.get('taxonomy_version'), 'commit': revision.get('commit_sha')}


def _version_tuple(value):
    try:
        return tuple(int(part) for part in str(value).split('.'))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- declared transitions

MATCH_SCOPES = frozenset({'decision', 'control', 'finding', 'customer_action', 'lifecycle'})


def load_transitions(path=None):
    """The declared register of methodology, taxonomy and contract transitions; validated, never inferred."""
    path = Path(path or TRANSITIONS)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValidationError(f'The semantic transition register could not be read: {exc}') from exc
    entries, errors = data.get('transitions') or [], []
    for entry in entries:
        name = entry.get('id') or '<unnamed>'
        errors += [f'{name}: {field} is required' for field in ('id', 'kind', 'reason', 'rule', 'introduced_by', 'matches')
                   if not entry.get(field)]
        if entry.get('kind') not in EXPECTED_KINDS:
            errors.append(f'{name}: kind must be one of {", ".join(sorted(EXPECTED_KINDS))}')
        for match in entry.get('matches') or []:
            scope = match.get('scope')
            if scope not in MATCH_SCOPES or not match.get('field'):
                errors.append(f'{name}: each match needs a known scope and a field')
            elif match.get('previously_absent'):
                if 'from' in match:
                    errors.append(f'{name}: a previously absent field has no previous value')
            elif not match.get('from') or not match.get('to'):
                # Explicit values are required: a transition never accepts whatever the current code produced.
                errors.append(f'{name}: a match must declare its previous and current values')
    if errors:
        raise ValidationError('The semantic transition register is invalid: ' + '; '.join(errors))
    return entries


class GitHistory:
    """Whether one commit contains another, from the local repository; None when it cannot be determined."""

    def __init__(self, root=ROOT):
        self.root, self.cache = Path(root), {}

    def contains(self, commit, ancestor):
        if not commit or not ancestor:
            return None
        if (commit, ancestor) not in self.cache:
            import subprocess
            run = subprocess.run(['git', 'merge-base', '--is-ancestor', ancestor, commit], cwd=self.root,
                                 capture_output=True)
            self.cache[(commit, ancestor)] = {0: True, 1: False}.get(run.returncode)
        return self.cache[(commit, ancestor)]


def transition_blocker(entry, versions, history):
    """Why a declared transition cannot explain this comparison, or None when it applies."""
    introduced = entry['introduced_by']
    if history.contains(versions['after'].get('commit'), introduced) is not True:
        return 'The current source does not contain the introducing commit, or it could not be verified.'
    if history.contains(versions['before'].get('commit'), introduced) is not False:
        return 'The retained package already contains the introducing commit, or its producing commit is unrecorded.'
    for label in ('methodology', 'taxonomy'):
        need, have = entry.get(f'effective_{label}_version'), versions['after'].get(label)
        if need and not (_version_tuple(need) and _version_tuple(have) and _version_tuple(have) >= _version_tuple(need)):
            return f'The current {label} version {have} predates the effective version {need}.'
    return None


def package_compatibility(result, current, transitions=()):
    """Reasons an intact retained result cannot be compared with the current contract; empty when comparable."""
    reasons = [f'The retained result has no {section} list.' for section in ('recommendations', 'controls')
               if not isinstance(result.get(section), list)]
    before = _versions(result)
    for label in ('methodology', 'taxonomy'):
        old, new = _version_tuple(before.get(label)), _version_tuple(current.get(label))
        if label == 'methodology' and not old:
            reasons.append('The retained result does not record a methodology version.')
        elif old and new and old[0] != new[0] and not any(
                entry.get('kind') == 'declared_compatibility_transition'
                and str((entry.get('compatible_from') or {}).get(label)) == str(old[0]) for entry in transitions):
            reasons.append(f'The retained {label} version {before.get(label)} and the current version '
                           f'{current.get(label)} differ in major version, and no compatibility transition is declared.')
    return reasons


# ---------------------------------------------------------------- display identity

FINDING_FIELDS = ('FindingKey', 'FindingFingerprint', 'Feature', 'Disposition', 'Status', 'Priority',
                  'ReadinessTreatment', 'ActionLane', 'ControlId', 'EvidenceKey', 'EvidenceId', 'EvidenceStatus',
                  'ObservationDate', 'FindingClass')
SEMANTIC_SCOPE_FIELDS = ('Provider', 'Population', 'EvidenceScope')
ANCHOR_FIELDS = ('ControlId', 'FindingKey', 'Service', 'Feature', 'EvidenceKey', 'AffectedObjectIds')
OPEN_DISPOSITIONS = frozenset({'Action', 'Coverage'})
# PersistentFindingId is assessment-scoped: a Standalone rebuild allocates a new AssessmentId.
ASSESSMENT_SCOPED_FIELDS = ('AssessmentId', 'PrimaryEnvironmentId', 'PersistentFindingId')


def semantic_key(row):
    """The finding's semantic identity; never its display ID.

    A finding with a FindingKey uses the production grouping identity
    (finding_reconciliation.finding_key: condition key, material boundary, and evidence
    gap versus condition), without the assessment-scoped fields a new run always changes.
    Otherwise the production fingerprint plus scope is used.
    """
    if row.get('FindingKey'):
        from Core.finding_reconciliation import finding_key
        return 'finding:' + finding_key({**row, **{field: None for field in ASSESSMENT_SCOPED_FIELDS}})
    anchor = row.get('FindingFingerprint')
    if not anchor:
        parts = [str(row.get(field) or '') for field in ANCHOR_FIELDS]
        if not any(parts):
            return None
        anchor = 'derived:' + '|'.join(parts)
    # An evidence gap and a confirmed condition are different questions (finding_reconciliation.finding_key).
    return '|'.join([anchor, *(str(row.get(field) or '') for field in SEMANTIC_SCOPE_FIELDS),
                     'coverage' if row.get('Disposition') == 'Coverage' else 'condition'])


def key_label(key):
    return 'SK-' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:16] if key else None


def _identity_rows(result):
    targets = defaultdict(set)
    for alias in (result.get('identity') or {}).get('Aliases') or []:
        if alias.get('Namespace') == 'RecommendationId:issue' and alias.get('TargetId'):
            targets[str(alias.get('Value'))].add(alias['TargetId'])
    actions = {str(row.get('RecommendationId')) for row in result.get('actions') or []}
    blockers = {str(row.get('RecommendationId')) for row in result.get('blockers') or []}
    controls = defaultdict(set)
    for control in result.get('controls') or []:
        for rid in control.get('recommendation_ids') or []:
            controls[str(rid)].add(control.get('control_id'))
    rows = []
    for row in result.get('recommendations') or []:
        display = str(row.get('RecommendationId'))
        persistent = targets.get(display, set())
        rows.append({
            'display_id': display,
            'persistent_id': row.get('PersistentFindingId') or (next(iter(persistent)) if len(persistent) == 1 else None),
            'base': semantic_key(row), 'key': None,
            'aliases': sorted({str(x) for x in row.get('CompatibilityRecommendationIds') or [] if x and str(x) != display}),
            'controls': sorted(item for item in controls.get(display, ()) if item),
            'fields': {**{field: row.get(field) for field in FINDING_FIELDS},
                       'IsAction': display in actions, 'IsBlocker': display in blockers},
            'evidence': {key: row.get(key) for key in ('EvidenceId', 'EvidenceKey', 'EvidenceSource')},
            'open': row.get('Disposition') in OPEN_DISPOSITIONS, 'raw': row})
    return rows


def _assign_keys(old, new):
    """Use the semantic key; where it repeats, qualify it by evidence record; a key still repeated is ambiguous."""
    counts = Counter(row['base'] for row in old if row['base']), Counter(row['base'] for row in new if row['base'])
    for rows in (old, new):
        for row in rows:
            base = row['base']
            if base:
                row['key'] = base if counts[0][base] <= 1 and counts[1][base] <= 1 else (
                    base + '|evidence:' + str(row['evidence'].get('EvidenceId') or ''))
        repeated = Counter(row['key'] for row in rows if row['key'])
        for row in rows:
            if row['key'] and repeated[row['key']] > 1:
                row['key'] = None


def _unique(rows, field):
    counts = Counter(row[field] for row in rows if row[field])
    return {row[field]: index for index, row in enumerate(rows) if row[field] and counts[row[field]] == 1}


def match_findings(before, after, *, versions=None):
    """Pair findings by PersistentFindingId, semantic key, current display ID, then previous display IDs.

    A display ID alone never establishes that two findings are the same. Relationships
    that cannot be established stay unresolved; no alias is invented.
    """
    old, new = _identity_rows(before), _identity_rows(after)
    _assign_keys(old, new)
    pairs, taken = {}, {}

    def pair(i, j, method):
        pairs[i], taken[j] = (j, method), i

    new_persistent = _unique(new, 'persistent_id')
    for pid, i in _unique(old, 'persistent_id').items():
        if pid in new_persistent:
            pair(i, new_persistent[pid], 'PersistentFindingId')
    new_keys = _unique(new, 'key')
    for key, i in _unique(old, 'key').items():
        j = new_keys.get(key)
        if i not in pairs and j is not None and j not in taken:
            pair(i, j, 'SemanticKey')
    old_display, new_display = _unique(old, 'display_id'), _unique(new, 'display_id')
    for display, i in old_display.items():
        j = new_display.get(display)
        # The current display ID corroborates an equal semantic key; it never pairs on its own.
        if i not in pairs and j is not None and j not in taken and old[i]['base'] and old[i]['base'] == new[j]['base']:
            pair(i, j, 'CurrentDisplayId')
    candidates = defaultdict(set)
    for j, row in enumerate(new):
        for alias in row['aliases']:
            if j not in taken and alias in old_display and old_display[alias] not in pairs:
                candidates[old_display[alias]].add(j)
    claimed = Counter(j for items in candidates.values() for j in items)
    for i, items in sorted(candidates.items()):
        if len(items) == 1 and claimed[next(iter(items))] == 1:
            pair(i, next(iter(items)), 'PreviousDisplayId')

    versions = versions or {'after': _versions(after)}
    effective = {'EffectiveMethodologyVersion': versions['after'].get('methodology'),
                 'EffectiveTaxonomyVersion': versions['after'].get('taxonomy')}
    describe = lambda row: {'DisplayId': row['display_id'], 'PersistentFindingId': row['persistent_id'],
                            'SemanticKey': key_label(row['base']), 'Controls': row['controls'], 'Open': row['open']}
    migrations, conflicts, retired, added, unresolved, removed_rows, added_rows = [], [], [], [], [], [], []
    for i, (j, method) in sorted(pairs.items()):
        if old[i]['display_id'] != new[j]['display_id']:
            migrations.append({'PreviousDisplayIds': [old[i]['display_id']], 'CurrentDisplayId': new[j]['display_id'],
                               'PersistentFindingId': new[j]['persistent_id'] or old[i]['persistent_id'],
                               'SemanticKey': key_label(new[j]['base'] or old[i]['base']), 'MatchedBy': method,
                               'PreviousIdRecordedAsAlias': old[i]['display_id'] in new[j]['aliases'],
                               'ChangeReason': f'The same finding, matched by {method}, carries a new display ID.',
                               **effective})
    for display in sorted(set(old_display) & set(new_display)):
        i, j = old_display[display], new_display[display]
        if pairs.get(i, (None,))[0] == j:
            continue
        different = i in pairs or j in taken or bool(old[i]['base'] and new[j]['base'] and old[i]['base'] != new[j]['base'])
        record = {'DisplayId': display, 'Previous': describe(old[i]), 'Current': describe(new[j])}
        if different:
            conflicts.append(dict(record, ChangeReason='The same display ID refers to different semantic findings.'))
        else:
            unresolved.append(dict(record, ChangeReason='The display ID is unchanged, but the semantic identity '
                                                        'cannot be established for both findings.'))
    resolved_displays = {item['DisplayId'] for item in conflicts + unresolved}
    for side, rows, other, used, out, rows_out in (('previous', old, new, pairs, retired, removed_rows),
                                                   ('current', new, old, taken, added, added_rows)):
        other_bases = defaultdict(list)
        for index, row in enumerate(other):
            if row['base']:
                other_bases[row['base']].append(index)
        other_used = taken if side == 'previous' else pairs
        other_display = new_display if side == 'previous' else old_display
        for index, row in enumerate(rows):
            if index in used:
                continue
            # Established only when no unpaired finding on the other side shares the semantic key.
            established = bool(row['base']) and all(item in other_used for item in other_bases.get(row['base'], []))
            if not established:
                if row['display_id'] not in resolved_displays:
                    unresolved.append({'DisplayId': row['display_id'], side.capitalize(): describe(row),
                                       'ChangeReason': 'Several findings share this semantic key, or it is missing; '
                                                       'the relationship cannot be established.'})
                continue
            rows_out.append(index)
            if row['display_id'] not in other_display:
                out.append({**describe(row), 'ChangeReason': 'The semantic finding is no longer reported.' if side == 'previous'
                            else 'A genuinely new semantic finding.', **effective})
    return {'old': old, 'new': new, 'pairs': pairs, 'migrations': migrations, 'reuse_conflicts': conflicts,
            'retired': retired, 'added': added, 'unresolved': unresolved,
            'removed_rows': removed_rows, 'added_rows': added_rows,
            'matched_by': dict(Counter(method for _, method in pairs.values()))}


# ---------------------------------------------------------------- semantic comparison

# Run identity and generation time; a new rebuild run changes these without changing a conclusion.
RUN_FIELDS = frozenset({'AssessmentId', 'RunId', 'PrimaryEnvironmentId', 'GeneratedAt', 'generated_at'})
CONTROL_FIELDS = ('status', 'configuration_result', 'operational_result')


def _stable_lifecycle(value):
    if isinstance(value, dict):
        return {key: _stable_lifecycle(item) for key, item in value.items() if key not in RUN_FIELDS}
    if isinstance(value, list):
        return [_stable_lifecycle(item) for item in value]
    return value


def semantic_changes(before, after, identity, *, mode):
    """Every semantic difference as an atomic change: scope, id, field, previous and current value."""
    changes = []

    def add(scope, ident, field, previous, current, **extra):
        changes.append({'scope': scope, 'id': ident, 'field': field, 'previous': previous, 'current': current, **extra})

    if before.get('decision') != after.get('decision'):
        add('decision', 'decision', 'decision', before.get('decision'), after.get('decision'),
            evidence={'previous_rationale': before.get('rationale'), 'current_rationale': after.get('rationale')})
    if before.get('evaluation_date') != after.get('evaluation_date'):
        add('evaluation_date', 'evaluation_date', 'evaluation_date', before.get('evaluation_date'), after.get('evaluation_date'))
    old_controls = {row.get('control_id'): row for row in before.get('controls') or []}
    new_controls = {row.get('control_id'): row for row in after.get('controls') or []}
    for control in sorted(set(old_controls) | set(new_controls), key=str):
        a, b = old_controls.get(control), new_controls.get(control)
        evidence = {'previous': (a or {}).get('evidence_ids'), 'current': (b or {}).get('evidence_ids')}
        if a is None or b is None:
            add('control', control, 'presence', 'absent' if a is None else 'present', 'present' if a is None else 'absent',
                evidence=evidence)
            continue
        for field in CONTROL_FIELDS:
            if a.get(field) != b.get(field):
                add('control', control, field, a.get(field), b.get(field), evidence=evidence)
    old, new = identity['old'], identity['new']
    for i, (j, method) in sorted(identity['pairs'].items()):
        for field, value in old[i]['fields'].items():
            if value != new[j]['fields'].get(field):
                add('finding', new[j]['display_id'], field, value, new[j]['fields'].get(field),
                    previous_display_id=old[i]['display_id'], controls=sorted(set(old[i]['controls']) | set(new[j]['controls'])),
                    matched_by=method, evidence={'previous': old[i]['evidence'], 'current': new[j]['evidence']},
                    _current=new[j]['raw'])
    for i in identity['removed_rows']:
        add('finding', old[i]['display_id'], 'presence', 'present', 'absent', controls=old[i]['controls'],
            open=old[i]['open'], evidence={'previous': old[i]['evidence']})
    for j in identity['added_rows']:
        add('finding', new[j]['display_id'], 'presence', 'absent', 'present', controls=new[j]['controls'],
            evidence={'current': new[j]['evidence']})
    for item in identity['unresolved']:
        add('finding', item['DisplayId'], 'identity', item.get('Previous'), item.get('Current'), unresolved=True,
            evidence={'reason': item['ChangeReason']})
    # Customer-action members are compared by finding, not by display ID.
    display_map = {old[i]['display_id']: new[j]['display_id'] for i, (j, _) in identity['pairs'].items()}
    shape = lambda item, mapping: {'lane': item.get('lane'), 'priority': item.get('priority'), 'members': sorted(
        mapping.get(str(member.get('recommendation_id')), str(member.get('recommendation_id')))
        for member in item.get('members') or [])}
    old_actions = {item.get('customer_action_id'): shape(item, display_map) for item in before.get('customer_actions') or []}
    new_actions = {item.get('customer_action_id'): shape(item, {}) for item in after.get('customer_actions') or []}
    for action in sorted(set(old_actions) | set(new_actions), key=str):
        a, b = old_actions.get(action), new_actions.get(action)
        if a is None or b is None:
            add('customer_action', action, 'presence', 'absent' if a is None else 'present', 'present' if a is None else 'absent',
                evidence={'members': (b or a)['members']})
            continue
        for field in ('lane', 'priority', 'members'):
            if a[field] != b[field]:
                add('customer_action', action, field, a[field], b[field])
    old_counts, new_counts = before.get('counts') or {}, after.get('counts') or {}
    for name in sorted(set(old_counts) | set(new_counts), key=str):
        if old_counts.get(name) != new_counts.get(name):
            add('counts', name, 'count', old_counts.get(name), new_counts.get(name))
    lifecycle = lambda result: {key: _stable_lifecycle(result.get(key)) for key in ('delta', 'lifecycle', 'baseline_comparison')
                                if key in result}
    if lifecycle(before) != lifecycle(after):
        add('lifecycle', 'lifecycle', 'lifecycle', lifecycle(before), lifecycle(after))
    if mode == 'render':
        run = lambda result: {key: (result.get('identity') or {}).get(key) for key in ('AssessmentId', 'RunId', 'BaselineRunId')}
        if run(before) != run(after):
            add('identity', 'identity', 'run_identity', run(before), run(after))
    return changes


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _absent_from_retained(retained, scope, field):
    if scope == 'customer_action':
        return 'customer_actions' not in retained
    rows = retained.get('recommendations' if scope == 'finding' else 'controls') or []
    return scope in {'finding', 'control'} and not any(field in row for row in rows)


def _matches(match, change, retained):
    if match['scope'] != change['scope'] or match['field'] != change['field']:
        return False
    if match.get('id') and not {change['id'], change.get('previous_display_id')} & set(_as_list(match['id'])):
        return False
    if match.get('control') and not set(change.get('controls') or []) & set(_as_list(match['control'])):
        return False
    if match.get('to') and change['current'] not in match['to']:
        return False
    # Conditions on the current row state the rule's own trigger; "$previous" requires the retained value.
    current = change.get('_current') or {}
    for field, allowed in (match.get('condition') or {}).items():
        if not (current.get(field) == change['previous'] if allowed == '$previous' else current.get(field) in _as_list(allowed)):
            return False
    if match.get('previously_absent'):
        # Verified from the retained result itself: the field or section did not exist there.
        return change['previous'] in (None, 'absent') and _absent_from_retained(retained, change['scope'], change['field'])
    return change['previous'] in match['from']


def classify_changes(changes, *, mode, transitions, versions, history, retained):
    """Expected only when an applicable declared transition explains the change; everything else is unexpected."""
    blockers = {entry['id']: transition_blocker(entry, versions, history) for entry in transitions} if mode != 'render' else {}
    commits = {'retained': versions['before'].get('commit'), 'current': versions['after'].get('commit')}
    for change in changes:
        if change['scope'] == 'counts':
            continue
        if mode == 'render':
            change.update(classification='unexpected', explanation='Render-only output must not change semantic results.')
        elif change.get('unresolved') or change['scope'] in {'evaluation_date', 'identity'}:
            change.update(classification='unexpected', explanation='A transition can never explain this change.')
        else:
            entry = next((item for item in transitions if blockers[item['id']] is None
                          and any(_matches(match, change, retained) for match in item['matches'])), None)
            if entry:
                change.update(classification='expected', transition=entry['id'], kind=entry['kind'], reason=entry['reason'],
                              rule=entry['rule'], producing_commits=dict(commits, introduced_by=entry['introduced_by']),
                              versions=versions)
            else:
                change.update(classification='unexpected', explanation='No applicable declared transition explains this change.')
    others = [change for change in changes if change['scope'] != 'counts']
    explained = bool(others) and all(change['classification'] == 'expected' for change in others)
    for change in changes:
        if change['scope'] == 'counts':
            if explained:
                change.update(classification='expected', transition=None, kind='derived',
                              reason='Derived summary count; every underlying change is explained.',
                              rule='Core/assessment_result.py counts', producing_commits=commits, versions=versions)
            else:
                change.update(classification='unexpected',
                              explanation='A summary count changed while an underlying change is unexplained or absent.')
    return [{'id': entry['id'], 'applies': blockers.get(entry['id']) is None, 'reason': blockers.get(entry['id'])}
            for entry in transitions] if mode != 'render' else []


def describe_change(change):
    previous = f" (previously {change['previous_display_id']})" if change.get('previous_display_id') not in (None, change['id']) else ''
    if change['field'] == 'presence' and change['scope'] == 'finding' and change['current'] == 'absent' and change.get('open'):
        return f"Open finding {change['id']} is no longer reported."
    if change.get('unresolved'):
        return f"Finding {change['id']}: identity relationship could not be established."
    return f"{change['scope']} {change['id']}{previous} {change['field']}: {change['previous']!r} -> {change['current']!r}"


def compare_results(before, after, *, mode='rebuild', collected_at=None, transitions=None, history=None):
    """Classify every semantic and display-identity difference between two results."""
    versions = {'before': _versions(before), 'after': _versions(after)}
    transitions = load_transitions() if transitions is None else transitions
    history = history or GitHistory()
    identity = match_findings(before, after, versions=versions)
    changes = semantic_changes(before, after, identity, mode=mode)
    evaluated = classify_changes(changes, mode=mode, transitions=transitions, versions=versions, history=history,
                                 retained=before)
    for change in changes:
        change.pop('_current', None)
    expected = [change for change in changes if change['classification'] == 'expected']
    unexpected = [change for change in changes if change['classification'] == 'unexpected']
    regressions = [describe_change(change) for change in unexpected]
    limit = str(collected_at or '')[:10]
    if limit:
        late = sorted(row['display_id'] for row in identity['new']
                      if str(row['fields'].get('ObservationDate') or '')[:10] > limit)
        if late:
            regressions.append(f'{len(late)} finding(s) carry an observation date after the retained collection date.')
    display_changes = identity['migrations'] or identity['retired'] or identity['added']
    if mode == 'render' and (changes or display_changes):
        regressions.insert(0, 'Render-only output changed semantic results.')
    blocking = [f"Display ID {item['DisplayId']} refers to a different semantic finding." for item in identity['reuse_conflicts']]
    controls = {}
    for change in changes:
        if change['scope'] == 'control':
            entry = controls.setdefault(change['id'], {'change': 'changed', 'fields': {}})
            if change['field'] == 'presence':
                entry.update(change='added' if change['current'] == 'present' else 'removed')
            else:
                entry['fields'][change['field']] = (change['previous'], change['current'])
    by_field = lambda name: sorted({change['id'] for change in changes if change['scope'] == 'finding' and change['field'] == name})
    stable = {item['CurrentDisplayId']: 'DisplayIdMigration' for item in identity['migrations']}
    stable.update({item['DisplayId']: 'DisplayIdReuseConflict' for item in identity['reuse_conflicts']})
    stable.update({item['DisplayId']: 'DisplayIdRetired' for item in identity['retired']})
    stable.update({item['DisplayId']: 'DisplayIdAdded' for item in identity['added']})
    stable.update({item['DisplayId']: 'Unresolved' for item in identity['unresolved']})
    finding_changes = sorted({change['id'] for change in changes if change['scope'] == 'finding'})
    semantic = {
        'decision': next(((c['previous'], c['current']) for c in changes if c['scope'] == 'decision'), None),
        'evaluation_date': next(((c['previous'], c['current']) for c in changes if c['scope'] == 'evaluation_date'), None),
        'controls_changed': controls, 'findings_changed': finding_changes,
        'actions_added': sorted(c['id'] for c in changes if c['field'] == 'IsAction' and c['current']),
        'actions_removed': sorted(c['id'] for c in changes if c['field'] == 'IsAction' and not c['current']),
        'customer_actions_changed': sorted({c['id'] for c in changes if c['scope'] == 'customer_action'}),
        'priorities_changed': by_field('Priority'), 'readiness_treatment_changed': by_field('ReadinessTreatment'),
        'evidence_references_changed': by_field('EvidenceId'), 'stable_ids_changed': stable,
        'identity': next(((c['previous'], c['current']) for c in changes if c['scope'] == 'identity'), None),
        'lifecycle': next(((c['previous'], c['current']) for c in changes if c['scope'] == 'lifecycle'), None),
    }
    status = ('DisplayIdentityConflict' if blocking else 'UnexpectedSemanticRegression' if regressions
              else 'ExpectedSemanticDifferences' if expected else None)
    old_ids, new_ids = (before.get('identity') or {}), (after.get('identity') or {})
    run = lambda meta: {key: meta.get(key) for key in ('AssessmentId', 'RunId', 'BaselineRunId')}
    return {'mode': mode, 'semantic_status': status, 'semantic_differences': bool(changes or display_changes),
            'expected_changes': expected, 'unexpected_changes': unexpected, 'unexpected_regressions': regressions,
            'blocking_conflicts': blocking, 'transitions_evaluated': evaluated, 'semantic': semantic,
            'display_identity': {key: identity[key] for key in ('migrations', 'reuse_conflicts', 'retired', 'added', 'unresolved',
                                                                'matched_by')},
            'findings_added': sorted(identity['new'][j]['display_id'] for j in identity['added_rows']),
            'findings_removed': sorted(identity['old'][i]['display_id'] for i in identity['removed_rows']),
            'run_identity': {'before': run(old_ids), 'after': run(new_ids), 'new_run': run(old_ids) != run(new_ids)},
            'versions': versions}


def comparison_status(comparison, presentation):
    if comparison['semantic_status']:
        return comparison['semantic_status']
    return 'PresentationOnlyDifferences' if presentation or comparison['display_identity']['migrations'] else 'ExactMatch'


# ---------------------------------------------------------------- presentation fingerprints

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
# Run-scoped identifiers (RUN-, AST-, PGA- and similar) and digests derived from them; a new run changes these only.
RUN_SCOPED = re.compile(r'\b[A-Z]{2,6}-[0-9a-f]{24,}\b|\b[0-9a-f]{32,}\b')


def _normalize(text):
    return RUN_SCOPED.sub('#', VOLATILE.sub('', text))


def html_fingerprint(path):
    """Opens the HTML and hashes its visible text and markup, ignoring timestamps, commits and run identifiers."""
    source = Path(path).read_text(encoding='utf-8')
    parser = _Text()
    parser.feed(source)
    parser.close()
    text = _normalize('\n'.join(parser.parts))
    return {'text': hashlib.sha256(text.encode()).hexdigest(), 'markup': hashlib.sha256(_normalize(source).encode()).hexdigest(),
            'visible_lines': len(parser.parts)}


def workbook_fingerprint(path):
    """Opens the workbook (integrity checked) and summarizes each sheet; the handle is always closed.

    Row order is not compared: some sheets are ordered by run-scoped identifiers.
    """
    from openpyxl import load_workbook
    from tools.audit_workbook import _compatibility_issues
    workbook = load_workbook(path, data_only=True, read_only=False)
    try:
        issues = _compatibility_issues(path, workbook)
        sheets = {}
        for sheet in workbook.worksheets:
            rows = sorted('\x1f'.join(_normalize(str(cell)) for cell in row if cell is not None)
                          for row in sheet.iter_rows(values_only=True))
            sheets[sheet.title] = {'rows': sheet.max_row, 'columns': sheet.max_column,
                                   'content': hashlib.sha256('\x1e'.join(rows).encode()).hexdigest()}
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
    """Presentation differences by output role; content hashes ignore timestamps, commits and run identifiers."""
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
        raise InvalidPackageError('The package folder must contain collection.json.')
    try:
        payload = load_collection(collection)
        payload = load_completed_package(dict(payload, package_directory=str(folder)))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise InvalidPackageError(f'The package could not be read safely: {exc}') from exc
    if not payload.get('completed_run_snapshot'):
        raise InvalidPackageError('The package has no completed assessment result to compare with.')
    try:
        result = read_assessment_result(payload['completed_run_snapshot'])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise InvalidPackageError(f'The retained assessment result is malformed: {exc}') from exc
    return folder, collection, payload, result


def _read_snapshot(path, label='snapshot'):
    from Core.assessment_serialization import read_assessment_result
    source = Path(path).resolve()
    if not source.is_file():
        raise InvalidPackageError(f'The {label} does not exist.')
    try:
        return source, read_assessment_result(source)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise InvalidPackageError(f'The {label} is malformed: {exc}') from exc


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
    source, original = _read_snapshot(snapshot)
    package = source.parent.parent if source.parent.name == 'assessment-results' else source.parent
    before_hashes = tree_hashes(package)
    revision = revision or engineering_revision(purpose)
    folder = engineering_output(output, protected=[package])
    attempts, log = [], io.StringIO()
    rendered = folder / 'render'
    copy = folder / 'rendered-snapshot.json'
    try:
        with tenant_access_guard(attempts), producing_context(revision), redirect_stdout(log):
            outputs = render_snapshot(source, rendered, report_format='both', snapshot_output=copy, retained_package=package)
        after = read_assessment_result(copy)
    except Exception as exc:  # fails safely: an execution error is reported, never a crash or a partial pass
        _write_failure(folder, 'render', source, exc, attempts)
        raise ValidationError(f'Render-only validation could not run: {exc}') from exc
    comparison = compare_results(original, after, mode='render', transitions=[])
    fingerprints, open_issues = fingerprint_outputs(rendered)
    presentation = compare_outputs(fingerprint_outputs(compare_to)[0], fingerprints) if compare_to else []
    history = [str(path) for path in folder.rglob('*') if path.name in {'assessment-history.json', 'assessment-run.json'}]
    failures = list(open_issues)
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


def offline_rebuild(package, output=None, *, purpose='Offline package validation', fail_on_semantic_change=False,
                    revision=None, transitions=None, history=None):
    """Rebuild a copy of a retained package with production code and compare with its retained result."""
    from Core.assessment_serialization import read_assessment_result
    from Core.cli_parser import parse_arguments
    from Core.offline_report import run_offline_report
    from Core.repository_provenance import producing_context
    folder_in, _, payload, retained = retained_package(package)
    before_hashes = tree_hashes(folder_in)
    transitions = load_transitions() if transitions is None else transitions
    revision = revision or engineering_revision(purpose)
    incompatible = package_compatibility(retained, current_versions(revision), transitions)
    if incompatible:
        raise IncompatiblePackageError(' '.join(incompatible))
    folder = engineering_output(output, protected=[folder_in])
    copy = folder / 'source-copy'
    shutil.copytree(folder_in, copy)
    work = folder / 'rebuild'
    work.mkdir()
    attempts, log = [], io.StringIO()
    previous_cwd, previous_argv = Path.cwd(), sys.argv
    try:
        # The working directory is process-wide: run one rebuild per process at a time.
        os.chdir(work)
        sys.argv = ['main.py', '--mode', 'offline', '--run-type', 'Standalone', '--collection-input',
                    str(copy / 'collection.json'), '--color', 'never']
        with tenant_access_guard(attempts), producing_context(revision), redirect_stdout(log):
            code = run_offline_report(parse_arguments(None, []))
    except Exception as exc:
        # Includes the source gate: editing the checkout during a rebuild blocks it.
        os.chdir(previous_cwd)
        _write_failure(folder, 'rebuild', folder_in, exc, attempts)
        raise ValidationError(f'The offline rebuild could not run: {exc}') from exc
    finally:
        os.chdir(previous_cwd)
        sys.argv = previous_argv
    snapshots = sorted((work / 'Reports').rglob('assessment-results/*.json')) if (work / 'Reports').is_dir() else []
    if code or len(snapshots) != 1:
        message = f'The offline rebuild did not produce one assessment result (exit {code}).'
        _write_failure(folder, 'rebuild', folder_in, RuntimeError(message), attempts, log=log.getvalue()[-2000:])
        raise ValidationError(message + '\n' + log.getvalue()[-2000:])
    rebuilt = read_assessment_result(snapshots[0])
    comparison = compare_results(retained, rebuilt, mode='rebuild', collected_at=payload.get('collected_at'),
                                 transitions=transitions, history=history)
    rebuilt_package = snapshots[0].parent.parent
    fingerprints, open_issues = fingerprint_outputs(rebuilt_package)
    retained_outputs, _ = fingerprint_outputs(folder_in / 'Deliverables') if (folder_in / 'Deliverables').is_dir() else ({}, [])
    presentation = compare_outputs(retained_outputs, fingerprints)
    failures = list(open_issues)
    try:
        replay = render_only(snapshots[0], folder / 'replay', purpose=purpose, revision=revision)
    except ValidationError as exc:
        replay = None
        failures.append(f'Replay of the rebuilt result could not run: {exc}')
    if replay and replay['execution_status'] != 'Completed':
        failures.append('Replay of the rebuilt result did not complete.')
    elif replay and replay['comparison_status'] not in {'ExactMatch', 'PresentationOnlyDifferences'}:
        comparison['unexpected_regressions'].append('Replay of the rebuilt result changed semantic results.')
        comparison['semantic_status'] = comparison['semantic_status'] or 'UnexpectedSemanticRegression'
    if tree_hashes(folder_in) != before_hashes:
        failures.append('The retained package changed during validation.')
    if attempts:
        failures.append('Tenant access was attempted.')
    report = _report('rebuild', source=folder_in, package=folder_in, package_hashes=before_hashes, result=retained,
                     comparison=comparison, outputs=fingerprints, presentation=presentation, attempts=attempts,
                     failures=failures, folder=folder, fail_on_semantic_change=fail_on_semantic_change)
    report.update(rebuilt_snapshot=str(snapshots[0]), replay_passed=bool(replay and replay['passed']),
                  retained_collected_at=payload.get('collected_at'), rebuilt_evaluation_date=rebuilt.get('evaluation_date'))
    _write_report(folder, report)
    return report


def exit_code(report):
    if report['execution_status'] != 'Completed':
        return EXIT_CODES[report['execution_status']]
    code = EXIT_CODES[report['comparison_status']]
    if not code and report.get('fail_on_semantic_change') and report['comparison_status'] == 'ExpectedSemanticDifferences':
        return EXIT_CODES['UnexpectedSemanticRegression']
    return code


def _report(mode, *, source, package, package_hashes, result, comparison, outputs, presentation, attempts, failures, folder,
            fail_on_semantic_change=False):
    from Core.repository_provenance import capture_revision
    current = capture_revision(ROOT)
    report = {
        'schema_version': VERSION, 'mode': mode, 'label': LABEL, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'execution_status': 'FailedToExecute' if failures else 'Completed',
        'comparison_status': comparison_status(comparison, presentation),
        'source': str(source), 'source_package': str(package), 'source_package_sha256': package_digest(package_hashes),
        'source_files': len(package_hashes),
        'source_package_unchanged': not any('package changed' in item for item in failures),
        'producing_commit': {'retained': comparison['versions']['before'].get('commit'),
                             'current': comparison['versions']['after'].get('commit'),
                             'validation_checkout': current.get('commit_sha'),
                             'validation_source_state': current.get('source_state')},
        'versions': comparison['versions'], 'semantic_differences': comparison['semantic_differences'],
        'expected_changes': comparison['expected_changes'], 'unexpected_changes': comparison['unexpected_changes'],
        'unexpected_regressions': comparison['unexpected_regressions'],
        'blocking_conflicts': comparison['blocking_conflicts'], 'display_identity': comparison['display_identity'],
        'findings_added': comparison['findings_added'], 'findings_removed': comparison['findings_removed'],
        'transitions_evaluated': comparison['transitions_evaluated'],
        'run_identity': comparison['run_identity'], 'semantic': comparison['semantic'],
        'presentation_differences': presentation,
        'output_files': {role: [Path(item['file']).name for item in items] for role, items in outputs.items()},
        'tenant_access_performed': bool(attempts), 'tenant_access_attempts': attempts, 'failures': failures,
        'fail_on_semantic_change': fail_on_semantic_change,
        'customer_publication_eligible': False, 'history_created': False, 'baseline_selected': False,
        'output_directory': str(folder),
    }
    report['exit_code'] = exit_code(report)
    report['passed'] = report['exit_code'] == 0
    return report


def _write_report(folder, report):
    (Path(folder) / 'comparison.json').write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding='utf-8')
    (Path(folder) / 'comparison.md').write_text('\n'.join(summary_lines(report)) + '\n', encoding='utf-8')


def _write_failure(folder, mode, source, exc, attempts, log=''):
    report = {'schema_version': VERSION, 'mode': mode, 'label': LABEL, 'execution_status': 'FailedToExecute',
              'comparison_status': None, 'exit_code': EXIT_CODES['FailedToExecute'], 'passed': False, 'source': str(source),
              'error': f'{type(exc).__name__}: {exc}', 'log_tail': log, 'tenant_access_performed': bool(attempts),
              'tenant_access_attempts': attempts, 'customer_publication_eligible': False}
    (Path(folder) / 'comparison.json').write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding='utf-8')
    (Path(folder) / 'comparison.md').write_text(f"# {LABEL}\n\n- Execution status: FailedToExecute\n- Error: {report['error']}\n"
                                                f"- Tenant access performed: {'Yes' if attempts else 'No'}\n", encoding='utf-8')


MD_LIMIT = 25  # Markdown lists at most this many items per group; comparison.json keeps every item.


def _limited(items, render):
    lines = [render(item) for item in items[:MD_LIMIT]]
    if len(items) > MD_LIMIT:
        lines.append(f'- … and {len(items) - MD_LIMIT} more (all listed in comparison.json)')
    return lines or ['- None']


def summary_lines(report):
    identity, versions = report['display_identity'], report['versions']
    expected, unexpected = report['expected_changes'], report['unexpected_regressions']
    by_transition = defaultdict(list)
    for change in expected:
        by_transition[change.get('transition') or 'derived'].append(change)
    lines = [f"# {report['label']}", '', '## Summary', '',
             f"- Execution status: {report['execution_status']}",
             f"- Comparison status: {report['comparison_status']}"
             + (' (blocking)' if report['comparison_status'] == 'DisplayIdentityConflict' else ''),
             f"- Exit code: {report['exit_code']}",
             f"- Expected semantic changes: {len(expected)} (traced to {len([k for k in by_transition if k != 'derived'])} "
             f"declared transition(s))",
             f"- Unexpected semantic regressions: {len(unexpected)}",
             f"- Presentation-only differences: {len(report['presentation_differences'])}",
             f"- Display IDs: {len(identity['migrations'])} migrated, {len(identity['reuse_conflicts'])} reuse conflict(s) "
             f"(blocking), {len(identity['retired'])} retired, {len(identity['added'])} added, "
             f"{len(identity['unresolved'])} unresolved",
             f"- Findings added / removed: {len(report['findings_added'])} / {len(report['findings_removed'])}",
             f"- Tenant access performed: {'Yes' if report['tenant_access_performed'] else 'No'}",
             f"- Source package unchanged: {'yes' if report['source_package_unchanged'] else 'no'}",
             '', '## Source and versions', '',
             f"- Mode: {report['mode']}", f"- Source package: {report['source_package']}",
             f"- Source package SHA-256: {report['source_package_sha256']} ({report['source_files']} files)",
             f"- Producing commits: original {report['producing_commit']['retained'] or 'unrecorded'}; current "
             f"{report['producing_commit']['current'] or 'unrecorded'} (checkout "
             f"{report['producing_commit']['validation_checkout'] or 'unrecorded'}, "
             f"{report['producing_commit']['validation_source_state']})",
             f"- Methodology / taxonomy: {versions['before']['methodology']} / {versions['before']['taxonomy']}"
             f" -> {versions['after']['methodology']} / {versions['after']['taxonomy']}",
             f"- Run identity: {'new engineering run (not a semantic change)' if report['run_identity']['new_run'] else 'unchanged'}",
             f"- Identity matching: {', '.join(f'{k} {v}' for k, v in sorted(identity['matched_by'].items())) or 'none'}",
             f"- Output files: {', '.join(name for names in report['output_files'].values() for name in names) or 'none'}"]
    if report['failures']:
        lines += ['', '## Execution failures', ''] + [f'- {item}' for item in report['failures']]
    lines += ['', '## Display-ID reuse conflicts (blocking)', ''] + _limited(identity['reuse_conflicts'], lambda item: (
        f"- {item['DisplayId']}: previously {item['Previous']['SemanticKey'] or 'no semantic key'}, now "
        f"{item['Current']['SemanticKey'] or 'no semantic key'}"))
    lines += ['', '## Unexpected semantic regressions', ''] + _limited(unexpected, lambda item: f'- {item}')
    lines += ['', '## Expected semantic changes', '']
    for name, items in sorted(by_transition.items()):
        first = items[0]
        lines += [f"### {name} ({len(items)} change(s))", '', f"- Kind: {first.get('kind')}", f"- Reason: {first.get('reason')}",
                  f"- Rule: {first.get('rule')}",
                  f"- Commits: original {first['producing_commits'].get('retained')}; introduced by "
                  f"{first['producing_commits'].get('introduced_by') or 'not applicable'}; current "
                  f"{first['producing_commits'].get('current')}", '']
        lines += _limited(items, lambda c: f"- {c['scope']} {c['id']} {c['field']}: {c['previous']!r} -> {c['current']!r}")
        lines.append('')
    if not by_transition:
        lines.append('- None')
    lines += ['', '## Display-ID migrations', ''] + _limited(identity['migrations'], lambda item: (
        f"- {', '.join(item['PreviousDisplayIds'])} -> {item['CurrentDisplayId']} (matched by {item['MatchedBy']}; "
        f"{item['SemanticKey']})"))
    lines += ['', '## Retired display IDs', ''] + _limited(identity['retired'], lambda item: f"- {item['DisplayId']}")
    lines += ['', '## Added display IDs', ''] + _limited(identity['added'], lambda item: f"- {item['DisplayId']}")
    lines += ['', '## Unresolved identity relationships', ''] + _limited(identity['unresolved'], lambda item: (
        f"- {item['DisplayId']}: {item['ChangeReason']}"))
    controls = report['semantic']['controls_changed']
    lines += ['', '## Controls changed', ''] + ([f"- {key}: {value['fields'] or value['change']}" for key, value in controls.items()]
                                               or ['- None'])
    lines += ['', '## Presentation differences', ''] + ([f'- {item}' for item in report['presentation_differences']] or ['- None'])
    lines += ['', '## Transitions evaluated', ''] + ([f"- {item['id']}: {'applies' if item['applies'] else item['reason']}"
                                                     for item in report['transitions_evaluated']] or ['- None'])
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
            _, before = _read_snapshot(args.before, 'before snapshot')
            _, after = _read_snapshot(args.after, 'after snapshot')
            comparison = compare_results(before, after, mode=args.mode)
            status = comparison_status(comparison, [])
            print(json.dumps(dict(comparison, execution_status='Completed', comparison_status=status),
                             indent=2, sort_keys=True, default=str))
            return EXIT_CODES[status]
        report = (offline_rebuild(args.package, args.output, fail_on_semantic_change=args.fail_on_semantic_change)
                  if args.command == 'rebuild' else render_only(args.snapshot, args.output, compare_to=args.compare_to))
        print('\n'.join(summary_lines(report)))
        print(f"\nReport: {Path(report['output_directory']) / 'comparison.md'}")
        return report['exit_code']
    except ValidationError as exc:
        print(f'Execution status: {exc.status}\n{exc}', file=sys.stderr)
        return EXIT_CODES[exc.status]


if __name__ == '__main__':
    sys.exit(main())
