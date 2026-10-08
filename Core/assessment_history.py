"""Portable completed-run history, explicit baselines and failure-safe updates."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

VERSION = '1.0.0'


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def seal(history):
    value = deepcopy(history)
    value.pop('Integrity', None)
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    value['Integrity'] = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    return value


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


@contextmanager
def _locked(path):
    lock = Path(path).with_name(Path(path).name + '.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError('Assessment history is locked by another update; no changes made.') from None
    os.close(descriptor)
    try:
        yield
    finally:
        lock.unlink()


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Run creation time must have a timezone.')
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ValueError('Assessment history has an invalid run timestamp.') from None


def read_history(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise ValueError('Assessment history is missing or unreadable.') from None
    return validate_history(value)


def validate_history(value):
    """Validate the complete candidate before any atomic history replacement."""
    if not isinstance(value, dict):
        raise ValueError('Assessment history must be an object.')
    if not value.get('SchemaVersion'):
        return dict(value, State='legacy', Diagnostics=[{
            'severity':'unresolved_legacy_reference','code':'legacy_history',
            'subject':'history','message':'History predates verified run ownership; continuity is unresolved.'}])
    if value['SchemaVersion'] != VERSION:
        raise ValueError('Unsupported assessment history schema version.')
    if value.get('Integrity') != seal(value)['Integrity']:
        raise ValueError('Assessment history integrity mismatch.')
    if not str(value.get('AssessmentId', '')).startswith('AST-') or not str(value.get('PrimaryEnvironmentId', '')).startswith('ENV-'):
        raise ValueError('Assessment history ownership is missing.')
    runs = value.get('Runs')
    if not isinstance(runs, list):
        raise ValueError('Assessment history Runs must be a list.')
    identifiers, initial = set(), []
    for sequence, entry in enumerate(runs, 1):
        if not isinstance(entry, dict) or entry.get('Sequence') != sequence:
            raise ValueError('Assessment history sequence is invalid.')
        identifier = entry.get('RunId')
        if not str(identifier or '').startswith('RUN-') or identifier in identifiers:
            raise ValueError('Duplicate or missing RunId in assessment history.')
        if any(entry.get(field) != value.get(field) for field in ('AssessmentId','PrimaryEnvironmentId')):
            raise ValueError('Assessment history contains foreign run ownership.')
        if entry.get('Status') != 'completed':
            raise ValueError('History entries must represent completed semantic runs.')
        mode, baseline = entry.get('RunType'), entry.get('BaselineRunId')
        if mode not in {'Initial','Reassessment','Standalone'}:
            raise ValueError('Invalid run type in assessment history.')
        if mode == 'Reassessment':
            if not baseline or baseline not in identifiers:
                raise ValueError('Missing, self or future baseline in assessment history.')
        elif baseline:
            raise ValueError('Initial and Standalone history entries cannot select a baseline.')
        if mode == 'Initial':
            initial.append(identifier)
        for field in ('SnapshotLocator','PackageLocator','SnapshotHash','CreatedAt','EvaluatedAt',
                      'MethodologyVersion','IdentitySchemaVersion','ReconciliationVersion','Comparability'):
            if not entry.get(field):
                raise ValueError('Assessment history entry lacks ' + field + '.')
        _time(entry['CreatedAt'])
        if not isinstance(entry['Comparability'],dict) or entry['Comparability'].get('Outcome') not in {
                'Comparable','ComparableWithQualifications','NotComparable','NotEvaluated'}:
            raise ValueError('History comparability outcome is invalid.')
        if entry['Comparability']['Outcome'] != 'Comparable' and not entry['Comparability'].get('Reasons'):
            raise ValueError('History comparability reasons are missing.')
        if 'Lifecycle' in entry:
            lifecycle=entry['Lifecycle']
            if (not isinstance(lifecycle,dict) or lifecycle.get('BaselineRunId')!=baseline
                    or lifecycle.get('ValidationOutcome')!='validated'
                    or lifecycle.get('DetailedRecordsLocator')!=entry['SnapshotLocator']
                    or not lifecycle.get('EngineVersion') or not lifecycle.get('MetricRuleVersion')
                    or not isinstance(lifecycle.get('Summary'),dict) or type(lifecycle.get('Enabled')) is not bool
                    or lifecycle.get('Outcome') not in {'Comparable','ComparableWithQualifications','NotComparable','NotEvaluated'}):
                raise ValueError('History lifecycle metadata is invalid.')
        identifiers.add(identifier)
    if len(initial) > 1 or (initial and initial != [value.get('InitialRunId')]):
        raise ValueError('Assessment history has multiple or inconsistent Initial runs.')
    if runs and value.get('InitialRunId') and not initial:
        raise ValueError('Reserved Initial run must complete before history continuation.')
    if 'Governance' in value or 'GovernanceReferences' in value:
        from .governance_contract import locator, time
        revisions=value.get('GovernanceReferences')
        if not isinstance(revisions,list) or not revisions or value.get('Governance')!=revisions[-1]:
            raise ValueError('Assessment history governance reference revisions do not reconcile.')
        for reference in revisions:
            if (not isinstance(reference,dict) or reference.get('SchemaVersion')!='1.0.0'
                    or reference.get('ValidationStatus')!='validated' or not isinstance(reference.get('Summary'),dict)
                    or not isinstance(reference.get('Integrity'),str) or len(reference['Integrity'])!=64):
                raise ValueError('Assessment history governance reference is invalid.')
            locator(reference.get('DecisionLogLocator'));time(reference.get('AsOf'))
    return value


def create_history(path, identity, purpose=None):
    value = {key:identity.get(key) for key in ('AssessmentId','PrimaryEnvironmentId','MethodologyVersion','CatalogVersion')}
    value.update(SchemaVersion=VERSION, Purpose=purpose, InitialRunId=identity['RunId']
        if identity.get('RunType')=='Initial' else None, Runs=[], UpdatedAt=timestamp())
    with _locked(path):
        if Path(path).exists():
            raise ValueError('Assessment history already exists; it cannot be overwritten.')
        atomic_write(path, seal(value))
    return read_history(path)


def _locator(path, history_path):
    return Path(os.path.relpath(Path(path).resolve(), Path(history_path).resolve().parent)).as_posix()


def _resolve(history_path, locator):
    if not isinstance(locator, str) or not locator or Path(locator).is_absolute() or '://' in locator:
        raise ValueError('History locators must be portable relative file paths.')
    return (Path(history_path).resolve().parent / locator).resolve()


def select_baseline(path, *, assessment_id, environment_id, baseline_run_id,
                    current_run_id=None, current_sequence=None, current_created_at=None):
    history = read_history(path)
    if history.get('State')=='legacy':
        raise ValueError('Legacy history cannot establish baseline ownership.')
    if history['AssessmentId'] != assessment_id or history['PrimaryEnvironmentId'] != environment_id:
        raise ValueError('Baseline assessment or environment ownership mismatch.')
    matches = [entry for entry in history['Runs'] if entry['RunId']==baseline_run_id]
    if len(matches)!=1:
        raise ValueError('Explicit baseline run was not found in the selected history.')
    entry = matches[0]
    if baseline_run_id==current_run_id:
        raise ValueError('Baseline cannot be the current run.')
    if current_sequence is not None and entry['Sequence'] >= current_sequence:
        raise ValueError('Baseline cannot be a future run.')
    if current_created_at and _time(entry['CreatedAt']) > _time(current_created_at):
        raise ValueError('Baseline cannot be a later-created run.')
    snapshot = _resolve(path, entry['SnapshotLocator'])
    package = _resolve(path, entry['PackageLocator'])
    if not package.is_dir() or not snapshot.is_relative_to(package):
        raise ValueError('History snapshot points to the wrong package.')
    try:
        if file_hash(snapshot) != entry['SnapshotHash']:
            raise ValueError('Baseline snapshot integrity mismatch.')
        from .assessment_serialization import read_assessment_result
        result = read_assessment_result(snapshot)
    except (OSError, ValueError):
        raise ValueError('Baseline snapshot is unreadable, invalid or has an integrity mismatch.') from None
    meta = result.get('identity') or {}
    for field in ('AssessmentId','RunId','PrimaryEnvironmentId'):
        if meta.get(field) != entry[field]:
            raise ValueError('History and baseline snapshot identity mismatch.')
    if meta.get('State') != 'complete':
        raise ValueError('Baseline lacks verified persistent identity.')
    return result


def append_run(path, result, *, snapshot_path, package_path, expected_hash=None):
    from .assessment_references import require_valid_assessment
    require_valid_assessment(result)
    meta, context = result['identity'], result['run_context']
    snapshot, package = Path(snapshot_path).resolve(), Path(package_path).resolve()
    if not snapshot.is_file() or not package.is_dir() or not snapshot.is_relative_to(package):
        raise ValueError('Completed run requires a snapshot inside its package.')
    with _locked(path):
        history = read_history(path)
        if history.get('State')=='legacy':
            raise ValueError('Cannot append verified runs to unresolved legacy history.')
        if expected_hash is not None and history['Integrity'] != expected_hash:
            raise ValueError('Assessment history changed since run preparation; retry explicitly.')
        for field in ('AssessmentId','PrimaryEnvironmentId'):
            if meta.get(field) != history.get(field):
                raise ValueError('Cannot append a foreign assessment or environment.')
        if any(entry['RunId']==meta['RunId'] for entry in history['Runs']):
            raise ValueError('Duplicate RunId; rendering must not append a semantic run.')
        if context['RunType']=='Initial' and history['Runs']:
            raise ValueError('Only one Initial run is permitted.')
        if context['RunType']=='Reassessment':
            select_baseline(path, assessment_id=meta['AssessmentId'], environment_id=meta['PrimaryEnvironmentId'],
                baseline_run_id=context['BaselineRunId'],current_run_id=meta['RunId'],
                current_sequence=len(history['Runs'])+1,current_created_at=meta['CreatedAt'])
        from .assessment_serialization import read_assessment_result
        persisted = read_assessment_result(snapshot)
        from .assessment_serialization import plain_data
        if persisted['identity'] != plain_data(meta) or persisted.get('run_context') != plain_data(context):
            raise ValueError('Completed snapshot does not match the run being appended.')
        if persisted.get('lifecycle')!=plain_data(result.get('lifecycle')):
            raise ValueError('Completed snapshot lifecycle differs from the run being appended.')
        entry = {key:meta.get(key) for key in ('AssessmentId','RunId','PrimaryEnvironmentId',
            'RunType','BaselineRunId','CreatedAt','EvaluatedAt','MethodologyVersion','CatalogVersion')}
        entry.update(Sequence=len(history['Runs'])+1, Status='completed',
            PackageLocator=_locator(package, path),SnapshotLocator=_locator(snapshot,path),
            SnapshotHash=file_hash(snapshot),CollectionCoverage=deepcopy(result.get('collection_coverage',{})),
            IdentitySchemaVersion=meta['SchemaVersion'],ReconciliationVersion=context['ReconciliationVersion'],
            Comparability=deepcopy(context['Comparability']), Purpose=context.get('Purpose'),
            RunBoundaries=deepcopy(result.get('run_boundaries',{})))
        lifecycle=result.get('lifecycle')
        if lifecycle:
            entry['Lifecycle']={key:deepcopy(lifecycle.get(key)) for key in
                ('EngineVersion','MetricRuleVersion','BaselineRunId','Enabled','Outcome','Summary','GeneratedAt')}
            entry['Lifecycle'].update(ValidationOutcome='validated',DetailedRecordsLocator=entry['SnapshotLocator'])
        updated = deepcopy(history)
        updated['Runs'].append(entry)
        updated['UpdatedAt'] = timestamp()
        updated = seal(updated)
        validate_history(updated)
        atomic_write(path, updated)
    return entry
