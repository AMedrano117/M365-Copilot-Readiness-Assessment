"""Versioned explicit metric rules. No direction inferred from names or severity."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

VERSION = '1.0.0'
MEASUREMENT_FIELDS = ('metric_id','metric_definition','control_id','provider','value','unit',
    'numerator','denominator','population','population_definition','population_membership_hash',
    'scope','affected_objects','window','window_start','window_end','reporting_basis',
    'control_definition_version','evidence_level','availability','complete','truncated',
    'selection','freshness','source_observed_at','observed_at','source_type','source_file',
    'source_hash','evidence_id','source_occurrence_id')
BOUNDARIES = ('metric_id','metric_definition','control_id','provider','unit','population',
    'population_definition','population_membership_hash','scope','affected_objects','reporting_basis',
    'control_definition_version')


def _rule(metric, unit, direction, target, levels, resolution=False, conditions=(), controls=()):
    return dict(MetricId=metric,Unit=unit,Direction=direction,Target=target,
        ZeroMeaning='favorable' if target==0 else 'unfavorable',
        RequiresNumeratorDenominator=unit=='%',RequiresPopulation=True,RequiresScope=True,
        MinimumEvidenceLevels=list(levels),MinimumCollectionQuality='complete current selected evidence',
        PercentageComparisonAllowed=unit=='%',AbsoluteCountComparisonAllowed=unit!='%',
        Tolerance=0,ResolutionAllowed=resolution,HumanReviewRequired=not resolution,Version=VERSION,
        ConditionKeys=list(conditions),ControlIds=list(controls))


# These metric identities are explicit normalized-input contracts. Adapters that
# do not emit them remain non-directional; no field-name or wording heuristics.
RULES = MappingProxyType({row['MetricId']:MappingProxyType(row) for row in (
    _rule('identity.mfa_registration_percent','%','higher',100,('configuration','inventory'),
        conditions=('mfa.registration','evidence.identity.mfa_registration_percent'),controls=('IDENTITY.MFA','IDENTITY.AUTH')),
    _rule('identity.policy_exceptions','count','target_zero',0,('policy_enforcement','observed_operation'),True,
        conditions=('policy.exceptions','evidence.identity.policy_exceptions'),controls=('IDENTITY.MFA','IDENTITY.AUTH')),
    _rule('endpoint.unsupported_devices','devices','lower',0,('configuration','observed_operation'),True,
        conditions=('unsupported.devices','evidence.endpoint.unsupported_devices'),controls=('ENDPOINT.POSTURE',)),
    _rule('endpoint.validated_coverage_percent','%','full_coverage',100,('configuration','observed_operation'),
        conditions=('validated.coverage','evidence.endpoint.validated_coverage_percent'),controls=('ENDPOINT.POSTURE',)),
)})
CONDITION_RULES=MappingProxyType({metric:tuple(rule['ConditionKeys']) for metric,rule in RULES.items()})
CONTROL_STATUS_RULE = dict(MetricId='control.assessment_status',Unit='status',Direction='categorical',
    Target='Met',ZeroMeaning='neutral',RequiresPopulation=True,RequiresScope=True,
    MinimumEvidenceLevels=['policy_enforcement','observed_operation','customer_attestation','portal_review'],
    MinimumCollectionQuality='complete current selected evidence',ResolutionAllowed=True,
    HumanReviewRequired=False,Version=VERSION)


def measurement(row):
    """Small comparison metadata, never a duplicated raw source record."""
    return {key:deepcopy(row.get(key)) for key in MEASUREMENT_FIELDS}


def number(value):
    if type(value) not in (int,float):
        return None
    parsed=Decimal(str(value))
    return parsed if parsed.is_finite() else None


def quality(row):
    return (row.get('availability')=='available' and row.get('complete') is True
        and not row.get('truncated') and row.get('selection')=='selected'
        and row.get('freshness')=='current' and row.get('value') is not None)


def _duration(row):
    try:
        start=datetime.fromisoformat(str(row['window_start']).replace('Z','+00:00'))
        end=datetime.fromisoformat(str(row['window_end']).replace('Z','+00:00'))
        return (end-start).total_seconds()
    except (KeyError,ValueError,TypeError):
        return None


def compare_metric(baseline, current):
    rule=RULES.get(current.get('metric_id'))
    output=dict(MetricId=current.get('metric_id'),Baseline=measurement(baseline),Current=measurement(current),
        Rule=deepcopy(dict(rule)) if rule else None,State='Indeterminate',Reasons=[],Qualifications=[],
        AbsoluteChange=None,PercentagePointChange=None,PercentageChange=None,ResolutionSupported=False,
        RuleVersion=VERSION)
    def issue(code):
        output['Reasons'].append(code)
    for field in BOUNDARIES:
        if baseline.get(field)!=current.get(field):issue('incompatible_'+field)
    for row in (baseline,current):
        if not quality(row):issue('incomplete_or_conflicted_measurement')
        if any(row.get(field) in (None,'') for field in ('metric_id','metric_definition','population','scope','window','evidence_level')):
            issue('measurement_boundary_unknown')
        if rule and (row.get('unit')!=rule['Unit'] or row.get('evidence_level') not in rule['MinimumEvidenceLevels']
                or row.get('control_id') not in rule['ControlIds']):
            issue('metric_rule_requirements_not_met')
        if row.get('unit')=='%':
            numerator,denominator=number(row.get('numerator')),number(row.get('denominator'))
            value=number(row.get('value'))
            if (numerator is None or denominator is None or denominator<=0 or numerator<0 or numerator>denominator
                    or not row.get('population_definition') or value is None
                    or abs(value-100*numerator/denominator)>Decimal('0.000001')):
                issue('percentage_basis_invalid')
    if baseline.get('evidence_level')!=current.get('evidence_level'):
        issue('evidence_level_changed')
    if baseline.get('denominator')!=current.get('denominator'):
        issue('denominator_changed');output['Qualifications'].append('Population denominator changed; membership equivalence is not assumed.')
    periods=('window','window_start','window_end')
    if any(baseline.get(key)!=current.get(key) for key in periods):
        output['Qualifications'].append('Observation periods differ; full original periods are retained.')
        if baseline.get('window')!=current.get('window') or _duration(baseline) is None or _duration(baseline)!=_duration(current):
            issue('periods_not_equivalent')
    if baseline.get('source_type')!=current.get('source_type'):
        issue('source_basis_changed');output['Qualifications'].append('Historical/imported and live evidence bases are not assumed equivalent.')
    output['Reasons']=sorted(set(output['Reasons']))
    if output['Reasons']:
        return output
    before,after=number(baseline.get('value')),number(current.get('value'))
    if before is None or after is None:
        output.update(State='Unchanged' if baseline.get('value')==current.get('value') else 'Changed',Reasons=['non_numeric_condition'])
        return output
    change=after-before
    output['AbsoluteChange']=float(change)
    if current.get('unit')=='%':output['PercentagePointChange']=float(change)
    if before!=0:output['PercentageChange']=float(change/abs(before)*100)
    if change==0:
        output.update(State='Unchanged',Reasons=['equivalent_measurement'])
    elif not rule:
        output.update(State='Changed',Reasons=['metric_direction_unregistered'])
    else:
        toward=change>0 if rule['Direction'] in {'higher','full_coverage'} else change<0
        output.update(State='Improved' if toward else 'Regressed',Reasons=['explicit_metric_direction'])
    if rule and rule['ResolutionAllowed'] and after==Decimal(str(rule['Target'])):
        output['ResolutionSupported']=True
    return output
