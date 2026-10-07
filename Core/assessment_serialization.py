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
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plain_data(result), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return str(target)
