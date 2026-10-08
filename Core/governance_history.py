"""Atomic, compare-and-swap decision log updates and additive history references."""
from copy import deepcopy
import json
from pathlib import Path
from .assessment_history import _locked, atomic_write, seal, read_history, validate_history, _locator
from .governance import require_log, project
from .governance_contract import fail, VERSION


def read_log(path):
    try:value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):fail('governance_unreadable','Decision log is missing or unreadable.')
    return require_log(value)


def write_log(path,value,*,expected_hash=None):
    require_log(value)
    with _locked(path):
        target=Path(path)
        if target.exists():
            old=read_log(target)
            if expected_hash is None or expected_hash!=old['Integrity']:
                fail('governance_concurrency','Decision log changed; retry from the explicitly read version.')
            if any(old.get(f)!=value.get(f) for f in ('AssessmentId','PrimaryEnvironmentId')):
                fail('governance_ownership','Decision log ownership is immutable.')
            if value['Events'][:len(old['Events'])]!=old['Events'] or len(value['Events'])<=len(old['Events']):
                fail('governance_append_only','Update must append events while preserving every prior record.')
        elif expected_hash is not None:
            fail('governance_concurrency','Expected decision log is missing; no replacement created.')
        atomic_write(target,value)
    return str(target)


def link_history(history_path,log_path,*,as_of,expected_hash):
    """Append a top-level reference revision; preserve all completed run entries.

    The log update and history link are separate atomic operations. A failed
    link never rolls back an approved event; retry only the metadata link.
    """
    log=read_log(log_path);view=project(log,as_of=as_of)
    with _locked(history_path):
        old=read_history(history_path)
        if old.get('State')=='legacy' or old.get('Integrity')!=expected_hash:
            fail('governance_concurrency','Assessment history changed or lacks verified ownership.')
        if any(old.get(f)!=log.get(f) for f in ('AssessmentId','PrimaryEnvironmentId')):
            fail('governance_ownership','History and decision log ownership differ.')
        updated=deepcopy(old)
        reference={'DecisionLogLocator':_locator(log_path,history_path),'Integrity':log['Integrity'],
            'SchemaVersion':VERSION,'AsOf':as_of,'Summary':view['Summary'],'ValidationStatus':'validated'}
        # Only an explicit package tree containing both files is portable.
        from .governance_contract import locator
        locator(reference['DecisionLogLocator'])
        updated.setdefault('GovernanceReferences',[]).append(deepcopy(reference))
        updated['Governance']=reference
        updated=seal(updated);validate_history(updated);atomic_write(history_path,updated)
    return reference
