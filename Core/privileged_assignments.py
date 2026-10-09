"""Pure, scope-aware interpretation of retained directory privilege grants.

No collector, renderer clock, remediation or governance mutation belongs here.
Original timestamp strings and every contributing occurrence remain unchanged.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re

from .identity_investigation import _get as get
from .source_evidence import envelope_complete

RULE_VERSION = '1.0.0'
ACTIVE_STATES = frozenset({'ActivePermanent', 'ActiveTimeBound', 'DirectActiveDurationUnverified'})
ELIGIBLE_STATES = frozenset({'EligiblePermanent', 'EligibleTimeBound'})
ASSIGNMENT_SOURCES = {'role_assignments': ('active', 'direct'),
    'role_assignment_schedules': ('active', 'schedule'),
    'role_eligibility_schedules': ('eligible', 'schedule'),
    'role_assignment_schedule_instances': ('active', 'instance'),
    'role_eligibility_schedule_instances': ('eligible', 'instance')}


def stable_key(prefix, value):
    return prefix + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                              ensure_ascii=True, default=str).encode()).hexdigest()


def instant(value):
    """Compare offset timestamps exactly, including sub-microsecond Graph digits."""
    if isinstance(value, datetime):
        value = value.isoformat()
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})', str(value or ''))
    if not match:
        return None
    try:
        # Parse whole seconds only. The retained fraction is compared as Decimal.
        stamp = datetime.fromisoformat(match[1] + 'T' + match[2] + ('+00:00' if match[4] == 'Z' else match[4]))
        delta = stamp.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return Decimal(delta.days * 86400 + delta.seconds) + Decimal('0.' + (match[3] or '0'))
    except (ValueError, TypeError, OverflowError):
        return None


def evaluation_timestamp(value):
    text = value.isoformat() if hasattr(value, 'isoformat') else str(value or '')
    # The existing assessment API accepts dates; evaluate through that UTC day.
    return text + 'T23:59:59.999999999Z' if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text) else text


def _duration(value):
    match = re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?', str(value or ''))
    if not match or not any(match.groups()):
        return None
    seconds = sum(Decimal(v or 0) * unit for v, unit in zip(match.groups(), (86400, 3600, 60, 1)))
    return seconds if seconds > 0 else None


def temporal_fields(row):
    schedule = get(row, 'scheduleInfo', {}) or {}
    expiration = get(schedule, 'expiration', {}) or {}
    return (get(schedule, 'startDateTime') or get(row, 'startDateTime'),
            get(expiration, 'endDateTime') or get(row, 'endDateTime'),
            str(get(expiration, 'type', '') or ''), get(expiration, 'duration'))


def classify_assignment(row, kind, evaluated_at, *, source_kind='schedule'):
    """Eligibility never proves activation; unknown duration never proves permanence."""
    now = instant(evaluation_timestamp(evaluated_at))
    if now is None or kind not in {'active', 'eligible'}:
        return 'UnknownTemporalState'
    start_raw, end_raw, expiration, duration = temporal_fields(row)
    start, end = instant(start_raw), instant(end_raw)
    if (start_raw and start is None) or (end_raw and end is None):
        return 'UnknownTemporalState'
    schedule = get(row, 'scheduleInfo', {}) or {}
    for field, raw in (('startDateTime', get(schedule, 'startDateTime')),
                       ('endDateTime', get(get(schedule, 'expiration', {}) or {}, 'endDateTime'))):
        top = get(row, field)
        if top and raw and instant(top) != instant(raw):
            return 'UnknownTemporalState'
    if start is not None and end is not None and end <= start:
        return 'UnknownTemporalState'
    if start is not None and start > now:
        return 'FuturePending'
    if end is not None and end <= now:
        return 'Expired'
    status = str(get(row, 'status', '') or '').lower()
    if status and status not in {'provisioned', 'granted'}:
        return 'UnknownTemporalState'
    if source_kind == 'direct':
        return 'DirectActiveDurationUnverified' if kind == 'active' else 'UnknownTemporalState'
    if start is None or (source_kind == 'schedule' and not status):
        return 'UnknownTemporalState'
    expiration = expiration.lower()
    prefix = 'Active' if kind == 'active' else 'Eligible'
    if expiration == 'noexpiration':
        return prefix + 'Permanent' if not end_raw and not duration else 'UnknownTemporalState'
    if expiration == 'afterduration':
        seconds = _duration(duration)
        if seconds is None or end_raw:
            return 'UnknownTemporalState'
        return prefix + 'TimeBound' if start + seconds > now else 'Expired'
    if expiration == 'afterdatetime' or source_kind == 'instance':
        return prefix + 'TimeBound' if end is not None and end > now else 'UnknownTemporalState'
    return 'UnknownTemporalState'


def compatible_source(source, boundary):
    """Explicit cross-engagement/provider evidence cannot enter a local join."""
    for field in ('assessment_id', 'environment_id', 'provider', 'tenant_id'):
        actual, expected = source.get(field), (boundary or {}).get(field)
        if actual and (not expected or str(actual).casefold() != str(expected).casefold()):
            return False
    return True


def source_rows(sources, names, boundary=None):
    for name in names:
        for dataset_index, dataset in enumerate(sources.get(name, []) or []):
            state = dataset.get('source') or {}
            if not compatible_source(state, boundary):
                continue
            for record_index, raw in enumerate(dataset.get('records', []) or []):
                if isinstance(raw, dict):
                    yield raw, {'dataset': name, 'dataset_index': dataset_index, 'record_index': record_index}, state


def source_coverage(sources, names, boundary=None):
    return all(sources.get(name) and all(compatible_source(d.get('source') or {}, boundary)
        and envelope_complete(d.get('source') or {}) for d in sources[name]) for name in names)


def source_current(source, evaluated_at, max_age_days=35):
    """Use the existing evidence contract's 35-day freshness boundary."""
    now = instant(evaluation_timestamp(evaluated_at))
    captured = instant(source.get('collected_at'))
    return bool(now is not None and captured is not None and 0 <= now - captured <= max_age_days * 86400)


def principal_type(row, sources, boundary):
    principal = get(row, 'principal', {}) or {}
    explicit = str(get(principal, '@odata.type') or get(row, 'principalType') or '').split('.')[-1].lower()
    types = {explicit} if explicit in {'user', 'group', 'serviceprincipal'} else set()
    identifier = get(row, 'principalId')
    for name, kind in (('users', 'user'), ('groups', 'group'), ('service_principals', 'serviceprincipal')):
        if identifier and any(get(r, 'id') == identifier for r, _, _ in source_rows(sources, [name], boundary)):
            types.add(kind)
    return next(iter(types)) if len(types) == 1 else 'unresolved'


def assignment_boundary(row, context):
    return {**deepcopy(context or {}), **{field: row.get(field) for field in
        ('principal_id', 'principal_type', 'role_definition_id', 'directory_scope_id', 'app_scope_id',
         'administrative_unit_scope', 'kind', 'member_type', 'source_kind', 'source_id',
         'schedule_id', 'instance_id', 'start', 'end', 'expiration_type', 'duration')}}


def normalize_assignments(sources, *, evaluation_timestamp=None, boundary=None):
    from .raw_evidence import safe_record
    assignments, diagnostics, by_key = [], [], {}
    now = instant(_evaluation_time(evaluation_timestamp))
    principal_index = defaultdict(set)
    for name, kind in (('users', 'user'), ('groups', 'group'), ('service_principals', 'serviceprincipal')):
        for raw, _, _ in source_rows(sources, [name], boundary):
            if get(raw, 'id'): principal_index[get(raw, 'id')].add(kind)
    role_index = defaultdict(list)
    for raw, _, _ in source_rows(sources, ['role_definitions'], boundary):
        role_index[get(raw, 'id')].append(raw)
    for name, (kind, source_kind) in ASSIGNMENT_SOURCES.items():
        for dataset in sources.get(name, []) or []:
            if not compatible_source(dataset.get('source') or {}, boundary):
                diagnostics.append({'code': 'cross_boundary_source', 'severity': 'qualification', 'subject': name})
        for raw, ref, source in source_rows(sources, [name], boundary):
            raw = safe_record(raw)
            start, end, expiration, duration = temporal_fields(raw)
            principal = get(raw, 'principal', {}) or {}
            role = get(raw, 'roleDefinition', {}) or {}
            role_id = get(raw, 'roleDefinitionId')
            roles = role_index[role_id]
            if not role and len(roles) == 1: role = roles[0]
            temporal = classify_assignment(raw, kind, evaluation_timestamp, source_kind=source_kind)
            captured = instant(source.get('collected_at'))
            if now is None or (captured is not None and captured > now):
                temporal = 'UnknownTemporalState'
            explicit = str(get(principal, '@odata.type') or get(raw, 'principalType') or '').split('.')[-1].lower()
            types = principal_index[get(raw, 'principalId')] | ({explicit} if explicit in {'user', 'group', 'serviceprincipal'} else set())
            resolved_type = next(iter(types)) if len(types) == 1 else 'unresolved'
            row = dict(source_id=get(raw, 'id'), source_kind=source_kind, principal_id=get(raw, 'principalId'),
                principal_type=resolved_type, principal_display_reference=get(principal, 'displayName'),
                role_definition_id=role_id, role_name=get(role, 'displayName'), role_template_id=get(role, 'templateId'),
                directory_scope_id=get(raw, 'directoryScopeId'), app_scope_id=get(raw, 'appScopeId'),
                administrative_unit_scope=get(raw, 'administrativeUnitId') or
                    (get(raw, 'directoryScopeId') if '/administrativeUnits/' in str(get(raw, 'directoryScopeId')) else None),
                member_type=get(raw, 'memberType') or 'Direct',
                kind=kind, start=start, end=end, expiration_type=expiration, duration=duration,
                schedule_id=get(raw, 'roleAssignmentScheduleId') or get(raw, 'roleEligibilityScheduleId') or
                    (get(raw, 'id') if source_kind == 'schedule' else None),
                instance_id=get(raw, 'id') if source_kind == 'instance' else None,
                assignment_type=get(raw, 'assignmentType'), activated_using=deepcopy(get(raw, 'activatedUsing')),
                temporal_state=temporal, active=temporal in ACTIVE_STATES, eligible=temporal in ELIGIBLE_STATES,
                qualified_complete=envelope_complete(source) and source_current(source, evaluation_timestamp), source_occurrences=[], evidence_refs=[])
            context = assignment_boundary(row, boundary)
            row['identity_boundary'] = context
            row['assignment_key'] = stable_key('PGA-', context)
            occurrence = {'source_ref': ref, 'source': deepcopy(source), 'raw': deepcopy(raw),
                          'occurrence_key': stable_key('PGO-', [name, source, raw])}
            # Identical rows are semantic duplicates; divergent native-ID rows remain separate.
            marker = (row['assignment_key'], stable_key('', raw))
            if marker in by_key:
                retained = by_key[marker]
            else:
                retained = row
                assignments.append(retained); by_key[marker] = retained
            retained['source_occurrences'].append(occurrence)
            retained['evidence_refs'].append(ref)
    variants = defaultdict(list)
    for assignment in assignments:
        variants[stable_key('', assignment['identity_boundary'])].append(assignment)
    for rows in variants.values():
        if len(rows) > 1:
            for row in rows:
                row['assignment_key'] = stable_key('PGA-', [row['identity_boundary'], row['source_occurrences'][0]['raw']])
                row.update(temporal_state='UnknownTemporalState', active=False, eligible=False)
            diagnostics.append({'code': 'conflicting_assignment_occurrence', 'severity': 'qualification', 'subject': rows[0]['source_id']})
    # Two captures that disagree about one native grant are not two effective
    # assignments. Keep every occurrence, but do not pick an active variant.
    native = defaultdict(list)
    for row in assignments:
        if row['source_id']:
            native[(row['source_kind'], row['kind'], row['source_id'])].append(row)
    for rows in native.values():
        if len({stable_key('', row['identity_boundary']) for row in rows}) > 1:
            for row in rows: row.update(temporal_state='UnknownTemporalState', active=False, eligible=False)
            diagnostics.append({'code': 'conflicting_native_assignment', 'severity': 'qualification', 'subject': rows[0]['source_id']})
    # Match only a unique current grant with every scope/type boundary retained.
    fields = ('principal_id', 'principal_type', 'role_definition_id', 'directory_scope_id', 'app_scope_id', 'administrative_unit_scope', 'kind', 'member_type')
    removed = set()
    for instance in [a for a in assignments if a['source_kind'] == 'instance' and a['schedule_id']]:
        matches = [a for a in assignments if a['source_kind'] == 'schedule' and a['source_id'] == instance['schedule_id']
                   and all(a[k] == instance[k] for k in fields) and instant(a['start']) == instant(instance['start'])
                   and (a['end'] == instance['end'] or (instant(a['end']) is not None and instant(a['end']) == instant(instance['end'])))]
        if len(matches) == 1:
            matches[0]['source_occurrences'].extend(instance['source_occurrences'])
            matches[0]['evidence_refs'].extend(instance['evidence_refs'])
            matches[0].setdefault('instance_ids', []).append(instance['instance_id'])
            matches[0]['qualified_complete'] = matches[0]['qualified_complete'] and instance['qualified_complete']
            removed.add(instance['assignment_key'])
        elif matches:
            diagnostics.append({'code': 'ambiguous_instance_match', 'severity': 'qualification', 'subject': instance['assignment_key']})
    for direct in [a for a in assignments if a['source_kind'] == 'direct']:
        matches = [a for a in assignments if a['source_kind'] != 'direct' and a['active'] and a['assignment_key'] not in removed
                   and all(a[k] == direct[k] for k in fields)]
        if len(matches) == 1:
            matches[0]['source_occurrences'].extend(direct['source_occurrences'])
            matches[0]['evidence_refs'].extend(direct['evidence_refs'])
            matches[0]['qualified_complete'] = matches[0]['qualified_complete'] and direct['qualified_complete']
            removed.add(direct['assignment_key'])
        elif len(matches) > 1:
            diagnostics.append({'code': 'ambiguous_assignment_match', 'severity': 'qualification', 'subject': direct['assignment_key']})
    assignments = sorted((a for a in assignments if a['assignment_key'] not in removed), key=lambda a: a['assignment_key'])
    return assignments, diagnostics


_evaluation_time = evaluation_timestamp
