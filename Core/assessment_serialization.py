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
    from .assessment_references import require_valid_assessment
    snapshot = plain_data(result)
    snapshot.setdefault('reconciliation', {'state':'legacy','reason':'Snapshot predates recorded semantic reconciliation; no equivalence inferred.'})
    from .assessment_runs import validate_run_context
    snapshot['run_workflow_diagnostics'] = validate_run_context(snapshot)
    snapshot['identity_validation'] = require_valid_assessment(snapshot)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return str(target)


def read_assessment_result(path):
    """Load the shared result directly; legacy state never implies continuity."""
    from .assessment_identity import incomplete_identity
    from .assessment_references import require_valid_assessment
    with Path(path).open(encoding='utf-8-sig') as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError('Assessment snapshot must contain a shared-result object.')
    if 'identity' not in result:
        result['identity'] = incomplete_identity('legacy')
    if 'reconciliation' not in result:
        result['reconciliation'] = {'state':'legacy','reason':'Snapshot predates recorded semantic reconciliation; no equivalence inferred.'}
    from .assessment_runs import validate_run_context
    result['run_workflow_diagnostics'] = validate_run_context(result)
    result['identity_validation'] = require_valid_assessment(result)
    return result
