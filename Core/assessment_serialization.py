"""Serialize the existing shared assessment result without a presentation projection."""

from collections.abc import Mapping
import json
from pathlib import Path
from .raw_evidence import safe_record


def plain_data(value):
    if isinstance(value, Mapping):
        return safe_record({str(key): plain_data(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return [plain_data(item) for item in value]
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, float) and (value != value or abs(value) == float('inf')):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, 'value'):
        return plain_data(value.value)
    return str(value)



def write_assessment_result(path, result):
    """Write a data-only, sanitized snapshot of the current runtime result."""
    from .repository_provenance import require_publication, normalize_revision
    require_publication()
    from .evidence_reconciliation import require_supported_confidence
    require_supported_confidence(result)
    from .assessment_references import require_valid_assessment
    from .identity_finding_taxonomy import project_identity_result
    from .repository_provenance import qualify_publication_result
    snapshot = plain_data(project_identity_result(qualify_publication_result(result)))
    from .repository_provenance import current_revision
    snapshot['producing_revision'] = normalize_revision(snapshot.get('producing_revision'))
    if 'replay_tool_revision' in snapshot:
        snapshot['replay_tool_revision'] = normalize_revision(snapshot['replay_tool_revision'])
    snapshot.setdefault('reconciliation', {'state':'legacy','reason':'Snapshot predates recorded semantic reconciliation; no equivalence inferred.'})
    from .assessment_runs import validate_run_context
    snapshot['run_workflow_diagnostics'] = validate_run_context(snapshot)
    from .lifecycle_validation import validate_lifecycle
    snapshot['lifecycle_diagnostics'] = validate_lifecycle(snapshot)
    from .governance_validation import validate_governance
    snapshot['governance_diagnostics'] = validate_governance(snapshot)
    snapshot['identity_validation'] = require_valid_assessment(snapshot)
    target = Path(path)
    from .publication_io import write_text
    write_text(target, json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    return str(target)


def read_assessment_result(path):
    """Load the shared result directly; legacy state never implies continuity."""
    from .assessment_identity import incomplete_identity
    from .assessment_references import require_valid_assessment
    with Path(path).open(encoding='utf-8-sig') as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError('Assessment snapshot must contain a shared-result object.')
    from .evidence_reconciliation import require_supported_confidence
    require_supported_confidence(result)
    from .repository_provenance import normalize_revision
    result['producing_revision'] = normalize_revision(result.get('producing_revision'))
    if 'replay_tool_revision' in result:
        result['replay_tool_revision'] = normalize_revision(result['replay_tool_revision'])
    if 'identity' not in result:
        result['identity'] = incomplete_identity('legacy')
    if 'reconciliation' not in result:
        result['reconciliation'] = {'state':'legacy','reason':'Snapshot predates recorded semantic reconciliation; no equivalence inferred.'}
    from .assessment_runs import validate_run_context
    result['run_workflow_diagnostics'] = validate_run_context(result)
    from .lifecycle_validation import validate_lifecycle
    result['lifecycle_diagnostics'] = validate_lifecycle(result)
    from .governance_validation import validate_governance
    result['governance_diagnostics'] = validate_governance(result)
    result['identity_validation'] = require_valid_assessment(result)
    from .identity_finding_taxonomy import project_identity_result
    return project_identity_result(result)
