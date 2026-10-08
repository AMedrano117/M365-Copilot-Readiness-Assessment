"""Shared semantic comparison, with an occurrence-preserving compatibility view.

Comparison IDs below are internal content keys, not POB/PFI identities. Unknown
boundaries never become wildcards. No measurements are added or averaged here.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from .evidence_contract import normalize_observation, _comparison_value

VERSION = '2.0.0'
DIMENSIONS = ('tenant_id', 'AssessmentId', 'RunId', 'PrimaryEnvironmentId', 'provider',
    'source_workload', 'control_id', 'control_definition_version', 'metric_id', 'metric_definition',
    'unit', 'population', 'population_definition', 'scope', 'affected_objects', 'applicability',
    'rollout_stage', 'evidence_level', 'window', 'window_start', 'window_end', 'reporting_basis',
    'product', 'tier', 'native_id')
REQUIRED = ('tenant_id', 'control_id', 'metric_id', 'population', 'scope', 'unit')
ALIASES = {'AssessmentId':'assessment_id', 'RunId':'run_id', 'PrimaryEnvironmentId':'environment_id'}


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str, separators=(',', ':'))


def key(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def dimensions(row):
    result = {field: row.get(field, row.get(ALIASES.get(field, ''))) for field in DIMENSIONS}
    result['source_workload'] = row.get('source_workload') or row.get('source_type')
    return result


def capture_time(row):
    """Return the original instant plus precision; a date cannot order its day."""
    value = str(row.get('source_observed_at') or row.get('observed_at') or '')
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None, 'unknown'
    if len(value) == 10:
        return instant.replace(tzinfo=timezone.utc), 'date'
    if instant.tzinfo is None:
        return instant, 'local'  # Unknown timezone cannot order different offsets.
    return instant.astimezone(timezone.utc), 'instant'


def semantic_payload(row):
    instant, precision = capture_time(row)
    value = comparison_value(row)
    if value[0]=='number':
        numeric=format(value[1], 'f')
        if '.' in numeric:
            numeric=numeric.rstrip('0').rstrip('.')
        value=('number','0' if value[1]==0 else numeric)
    return dict(dimensions(row), value=value,
        control_result=row.get('control_result'), observed_at=str(instant), time_precision=precision,
        availability=row.get('availability'), complete=row.get('complete'), truncated=row.get('truncated'),
        numerator=row.get('numerator'), denominator=row.get('denominator'),
        qualifications=sorted(row.get('qualifications') or []), historical=row.get('historical_conclusion', False))


def comparison_key(row):
    boundary = dimensions(row)
    missing = [field for field in REQUIRED if not boundary.get(field)]
    # Unknown source-local boundaries cannot replace a different declared source.
    if missing:
        boundary['unknown_occurrence'] = key({field: row.get(field) for field in
            ('source_file','source_hash','source_type','source_schema','evidence_id')})
    if row.get('metric_value_kind')=='legacy_narrative':
        boundary['uninterpreted_declaration']=row.get('evidence_id')
    if row.get('unit')=='%':
        boundary['denominator']=row.get('denominator')
    return key(boundary)


def comparison_value(row):
    return _comparison_value(row.get('semantic_measurement',row.get('value')))


def _finalists(pool):
    known = [(row, *capture_time(row)) for row in pool]
    dated = [(row, stamp, precision) for row, stamp, precision in known if stamp is not None]
    if not dated:
        return pool
    # Mixed timezone certainty is unordered, so every candidate stays visible.
    if any(precision == 'local' for _, _, precision in dated) and any(precision != 'local' for _, _, precision in dated):
        return pool
    latest_day = max(stamp.date() for _, stamp, _ in dated)
    current = [(row, stamp, precision) for row, stamp, precision in dated if stamp.date() == latest_day]
    if all(precision == 'instant' for _, _, precision in current):
        latest = max(stamp for _, stamp, _ in current)
        current = [entry for entry in current if entry[1] == latest]
    return [row for row, _, _ in current] + [row for row, stamp, _ in known if stamp is None]


def reconcile_evidence(observations, *, evaluation_date=None, expected_tenant_id=None):
    derived={'reconciled_observation_id','source_occurrence_id','selection_reason','selected_evidence_id'}
    rows = [normalize_observation({field:value for field,value in row.items() if field not in derived}, evaluation_date=evaluation_date,
            expected_tenant_id=expected_tenant_id) for row in observations]
    rows.sort(key=encoded)
    groups = defaultdict(list)
    for row in rows:
        groups[comparison_key(row)].append(row)
    conflicts, diagnostics, non_comparable = [], [], []
    for boundary, group in sorted(groups.items()):
        missing = [field for field in REQUIRED if not dimensions(group[0]).get(field)]
        optional_missing = [field for field in DIMENSIONS if not dimensions(group[0]).get(field) and field not in REQUIRED]
        if missing or optional_missing:
            diagnostics.append({'code':'missing_comparison_dimensions','severity':'compatibility_warning',
                'boundary':boundary,'missing_dimensions':missing + optional_missing,
                'reason':'No equivalence is inferred for absent declarations; legacy source-family comparison is limited to matching recorded dimensions.'})
        if group[0].get('metric_value_kind')=='legacy_narrative':
            diagnostics.append({'code':'legacy_narrative_measurement','severity':'compatibility_warning',
                'boundary':boundary,'reason':'No declared measured value or control conclusion; narrative wording cannot establish comparable measurements.'})
        if missing or len(groups) > 1:
            non_comparable.append({'boundary':boundary,'dimensions':dimensions(group[0]),
                'evidence_ids':sorted({row['evidence_id'] for row in group}), 'missing_dimensions':missing,
                'reason':'Required comparison dimensions are missing.' if missing else 'Material boundaries differ; measurements are not combined.'})
        candidates = [row for row in group if row['availability'] in {'available','partial'}
            and row['value'] is not None and row['freshness'] != 'future']
        if not candidates:
            for row in group:
                row['selection'] = 'unavailable'
                row['selection_reason'] = 'No usable measured value for this boundary.'
            continue
        complete = [row for row in candidates if row['complete']]
        finalists = _finalists(complete or candidates)
        values = {(comparison_value(row), row.get('control_result')) for row in finalists}
        if len(values) > 1:
            conflicts.append({'boundary':boundary,'evidence_ids':sorted({row['evidence_id'] for row in finalists}),
                'observation_ids':sorted({key(semantic_payload(row)) for row in finalists}),
                'result':'Not established','reason':'Comparable current observations have incompatible measurements or control conclusions.'})
            for row in group:
                row['selection'] = 'conflict' if any(row is other for other in finalists) else 'superseded'
                row['selection_reason'] = 'Unresolved comparable contradiction.' if row['selection']=='conflict' else 'Retained outside the current evidence-quality pool.'
                if row['selection']=='conflict':
                    row['qualification'] = (row['qualification'] + ' Compatible sources disagree; confirm the value before use.').strip()
            continue
        winner = min(finalists, key=lambda row: (not bool(row['observed_at']),row['source_file'],row['evidence_id'],key(row)))
        winner_payload = semantic_payload(winner)
        for row in group:
            row['selection'] = 'selected' if row is winner else 'duplicate' if semantic_payload(row)==winner_payload else 'superseded'
            row['selection_reason'] = {'selected':'Complete evidence, then latest orderable capture; stable display tie-break.',
                'duplicate':'All recorded material semantic fields match; occurrence retained.',
                'superseded':'Retained separately; complete evidence precedes partial, then latest orderable capture.'}[row['selection']]
            if row is not winner:
                row['selected_evidence_id'] = winner['evidence_id']
                if not row['complete'] and winner['complete']:
                    row['qualification'] = (row['qualification'] + ' A complete comparable source was retained.').strip()
    semantic_groups = defaultdict(list)
    for row in rows:
        payload = semantic_payload(row)
        # Missing required semantics forbid cross-source exact equivalence too.
        if any(not dimensions(row).get(field) for field in REQUIRED):
            payload['unknown_occurrence'] = comparison_key(row)
        if row.get('metric_value_kind')=='legacy_narrative':
            payload['uninterpreted_declaration']=comparison_key(row)
        semantic_groups[key(payload)].append(row)
    retained, occurrences, duplicates = [], [], []
    for identifier, members in sorted(semantic_groups.items()):
        member_ids=[]
        repetitions=defaultdict(int)
        for row in sorted(members,key=encoded):
            digest=key(row); repetitions[digest]+=1
            occurrence_id=digest+':'+str(repetitions[digest])
            member_ids.append(occurrence_id)
            row['reconciled_observation_id']=identifier
            row['source_occurrence_id']=occurrence_id
            occurrences.append({'id':occurrence_id,'observation_id':identifier,'source':deepcopy(row)})
        selected=min(members,key=lambda row: (row['selection']!='selected',encoded(row)))
        retained.append({'id':identifier,'dimensions':dimensions(selected),'occurrence_ids':member_ids,
            'decision':selected['selection'],'reason':selected['selection_reason'],
            'selected_display_occurrence':selected['source_occurrence_id'],
            'missing_dimensions':[field for field in REQUIRED if not dimensions(selected).get(field)]})
        if len(members)>1:
            duplicates.append({'observation_id':identifier,'occurrence_ids':member_ids,'count':len(members),
                'reason':'Equal recorded semantic measurements; all original occurrences retained.'})
    # Map conflict keys after conservative unknown-boundary disambiguation.
    for conflict in conflicts:
        conflict['observation_ids']=sorted({row['reconciled_observation_id'] for row in rows
            if comparison_key(row)==conflict['boundary'] and row['selection']=='conflict'})
    rows.sort(key=lambda row: (row['domain_id'],row['metric_id'],comparison_key(row),row['observed_at'],row['evidence_id'],key(row)))
    return {'version':VERSION,'rows':rows,'observations':retained,'occurrences':occurrences,
        'exact_duplicate_groups':duplicates,'conflicts':conflicts,'non_comparable':non_comparable,
        'diagnostics':diagnostics,'material_dimensions':list(DIMENSIONS)}
