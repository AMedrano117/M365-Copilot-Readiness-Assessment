"""Shared, identity-free authentication finding semantics for live and replay."""

from copy import deepcopy
from collections import Counter

from .new_recommendation import new_recommendation
from .signin_evidence import legacy_event_summary
from .source_evidence import resolve_source, envelope_complete, envelope_availability


def assessment_authentication_summary(bundle, operation):
    """Persist renderer-independent layers; no row identities in the summary."""
    datasets = (bundle.get('assessment_sources') or {}).get('signin_logs', [])
    records = [row for row in operation.get('IDENTITY.AUTH', {}).get('records', [])
               if row.get('AuthenticationRuleVersion')]
    return {'registration': deepcopy((bundle.get('authentication_methods') or {}).get('registration_population') or {}),
            'enforcement': {'operational_result': operation.get('IDENTITY.AUTH', {}).get('result', 'unknown'),
                            'qualification': 'Policy configuration and event-scoped policy results do not alone establish tenant-wide enforcement.'},
            'observed_authentication': {'event_count': len(records),
                'mfa_satisfaction': dict(Counter(row['MFASatisfaction'] for row in records)),
                'legacy': legacy_event_summary(datasets),
                'qualification': 'Returned events only; requirement, completion, prior claims, external provider satisfaction, strong primary authentication and denied steps remain separate.'}}


def signin_datasets(client):
    """Keep every retained window, including failed supplemental windows."""
    state, association = resolve_source(getattr(client, 'collection_status', {}) or {}, 'signin_logs', 'Entra')
    source = deepcopy(state)
    source['complete'] = association == 'matched' and envelope_complete(state)
    source['availability_status'] = envelope_availability(state) if association == 'matched' else 'unknown'
    records = getattr(client, 'signin_logs', None)
    datasets = [{'records': records if isinstance(records, (list, tuple)) else [], 'source': source}]
    datasets += deepcopy((getattr(client, 'assessment_datasets', {}) or {}).get('signin_logs', []))
    return datasets


def registration_recommendation(metrics, feature):
    population = metrics.get('registration_population')
    if population is not None:
        counts = population['counts']
        registered, known = counts['ExplicitlyRegistered'], population['denominator']
        affected = counts['ExplicitlyNotRegistered']
        observation = (f"{registered} of {known} users with known registration flags"
                       + (f" ({population['percentage']:.1f}%)" if known else '')
                       + ' are MFA registered. '
                       + f"{counts['Unknown']} unknown, {counts['Conflicting']} conflicting, and "
                       + f"{counts['ExcludedWithReason']} excluded with reason; {population['total']} returned users. "
                       + 'Registration is separate from policy enforcement and observed authentication.')
        if not known:
            observation = 'MFA registration has no known eligible denominator. ' + observation
        disposition = 'Action' if affected else 'Coverage' if not known or counts['Unknown'] or counts['Conflicting'] else 'Reference'
        recommendation = (f'Review the {affected} explicitly unregistered users in the linked evidence, confirm intended scope and documented exceptions, '
                          'then arrange registration. Verify MFA enforcement and sign-in behavior separately.' if affected else
                          'Resolve unknown or conflicting registration records and confirm the report scope.' if disposition == 'Coverage' else '')
    else:
        # An old aggregate cannot reproduce flags or select remediation targets.
        total, registered = metrics.get('total_users', 0), metrics.get('mfa_enabled_users', 0)
        observation = (f'The saved summary reports {registered} of {total} users enrolled in MFA; the retained registration population is not available to reproduce this aggregate. '
                       'The known denominator, affected users, enforcement and observed authentication remain unconfirmed.') if total else (
                       'MFA registration could not be determined; no known registration denominator is retained.')
        disposition, recommendation = 'Coverage', 'Review or collect the registration report, its scope and completeness before selecting remediation targets.'
    row = new_recommendation(service='Entra', feature=feature, observation=observation, recommendation=recommendation,
                             priority='High' if disposition == 'Action' else 'Medium',
                             status='Action Required' if disposition == 'Action' else 'Not Assessed' if disposition == 'Coverage' else 'Insight',
                             disposition=disposition, finding_key='entra.authentication.mfa_registration',
                             evidence_key='authentication_detail;mfa_registration_detail' if disposition == 'Action' else 'authentication_detail')
    if population is not None:
        # Full row identities belong to technical evidence, never the finding card.
        row['MFARegistrationPopulation'] = {key: deepcopy(value) for key, value in population.items() if key != 'entries'}
    return row


def qualify_legacy_finding(row, client):
    summary = legacy_event_summary(signin_datasets(client))
    counts = summary['counts']
    row.setdefault('OriginalObservation', row.get('Observation'))
    if row.get('Feature') != 'Legacy authentication sign-ins':
        row.setdefault('OriginalFeature', row.get('Feature'))
    row.setdefault('ReportedLegacySignInCount', summary['total'])
    periods = '; '.join(f"{window.get('window_start') or 'unknown start'} to {window.get('window_end') or 'unknown end'}"
                        for window in summary['windows'])
    observation = (f"{summary['total']} legacy authentication sign-in attempts in the returned sign-in records: "
                   f"{counts['Success']} succeeded; {counts['BlockedByConditionalAccess']} blocked by Conditional Access; "
                   f"{counts['FailedOther']} failed for other reasons; {counts['InterruptedOrChallenged']} interrupted or challenged; "
                   f"{counts['Unknown']} unknown outcomes. Period: {periods}. ")
    observation += ('No successful legacy request was observed within the complete known source boundaries. '
                    if summary['no_success_observed'] else '')
    observation += ('Source coverage is complete for the recorded query. ' if summary['complete'] else 'Source coverage is incomplete or unknown. ')
    observation += 'These event results do not establish tenant-wide blocking or downstream data access.'
    if counts['Success']:
        disposition, status = 'Action', 'Action Required'
        recommendation = 'Investigate the successful legacy-client token requests and their identities and resources. Confirm dependencies, migrate required clients, and validate blocking for the intended population.'
    elif counts['Unknown'] or not summary['complete'] or not summary['known_population_and_period']:
        disposition, status = 'Coverage', 'Not Assessed'
        recommendation = 'Resolve unknown event results and collection boundaries before deciding whether legacy blocking is effective. Review the retained attempts separately.'
    else:
        disposition, status, recommendation = 'Reference', 'Insight', ''
    row.update(Feature='Legacy authentication sign-ins', FindingKey='entra.signins.legacy_auth',
               EvidenceKey='legacy_signin_detail', EvidenceSource='entra_signin_logs',
               EvidenceComplete=summary['complete'], EvidenceScope='Returned client-classified sign-in events within recorded source boundaries',
               AuthenticationSummary=summary, Observation=observation,
               Disposition=disposition, Status=status, Recommendation=recommendation)
    return row
