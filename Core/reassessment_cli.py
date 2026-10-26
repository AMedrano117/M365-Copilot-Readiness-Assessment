"""Read-only reassessment shortcut resolution and explicit operator selection."""
from copy import copy
from pathlib import Path


def normalize_reassessment(args):
    """Resolve ownership from history, without selecting a baseline or tenant."""
    result = copy(args)
    if not getattr(result, 'reassess', None):
        return result, []
    result.run_type = 'Reassessment'
    result.assessment_history = result.reassess
    result._reassessment_candidates = []
    from .assessment_history import read_history
    try:
        history = read_history(result.assessment_history)
        if history.get('State') == 'legacy':
            return result, ['reassessment_history_legacy']
        previous = getattr(result, '_reassessment_history_integrity', None)
        if previous and previous != history['Integrity']:
            return result, ['reassessment_history_changed_after_selection']
        result._reassessment_history_integrity = history['Integrity']
        if result.assessment_id and result.assessment_id != history['AssessmentId']:
            return result, ['reassessment_history_ownership_mismatch']
        result.assessment_id = history['AssessmentId']
        from .assessment_identity import environment_id
        if environment_id(result.tenant_id) != history['PrimaryEnvironmentId']:
            return result, ['reassessment_history_environment_mismatch']
        result._reassessment_candidates = [
            {'run_id': row['RunId'], 'run_type': row['RunType'],
             'evaluation_date': row['EvaluatedAt'], 'sequence': row['Sequence'],
             'historical_import': bool(row.get('HistoricalImport'))}
            for row in history['Runs'] if row['Status'] == 'completed'
        ]
        if not result._reassessment_candidates:
            return result, ['reassessment_history_no_completed_runs']
    except (ValueError, OSError, KeyError, TypeError):
        return result, ['reassessment_history_invalid']
    if not result.baseline_run_id:
        return result, ['reassessment_baseline_selection_pending']
    return result, []


def select_reassessment_baseline(args):
    """Return confirmed intent; the caller revalidates history and baseline."""
    import sys
    from .console_reporting import print_paragraph
    from .assessment_history import read_history
    if read_history(args.assessment_history)['Integrity'] != args._reassessment_history_integrity:
        raise ValueError('Assessment history changed before selection; retry explicitly.')
    candidates = args._reassessment_candidates
    if not sys.stdin.isatty():
        from .console_reporting import display_path
        history = display_path(args.assessment_history).replace("'", "''")
        from .console_reporting import cli_command
        raise ValueError('Noninteractive reassessment requires --baseline-run-id. Example: '
                         f"{cli_command()} --reassess '{history}' --baseline-run-id '<RUN-id>'")
    print_paragraph('Choose the completed run to compare against:')
    for number, candidate in enumerate(candidates, 1):
        print_paragraph(f"{number}. {candidate['run_id']} | {candidate['run_type']} | "
                        f"{candidate['evaluation_date']}" +
                        (' | Historical reports only; technical delta unavailable' if candidate.get('historical_import') else ''))
    try:
        if len(candidates) == 1:
            selected = candidates[0]
        else:
            answer = input('Baseline number (blank to cancel): ').strip()
            if not answer:
                raise ValueError('Reassessment cancelled; no changes made.')
            if not answer.isdecimal() or not 1 <= int(answer) <= len(candidates):
                raise ValueError('Invalid baseline selection; no changes made.')
            selected = candidates[int(answer) - 1]
        answer = input(f"Compare against {selected['run_id']} "
                       f"({selected['run_type']}, {selected['evaluation_date']})? [y/N]: ").strip().lower()
        if answer not in {'y', 'yes'}:
            raise ValueError('Reassessment cancelled; no changes made.')
    except (EOFError, KeyboardInterrupt):
        raise ValueError('Reassessment cancelled; no changes made.') from None
    result = copy(args)
    result.baseline_run_id = selected['run_id']
    return result


def reassessment_command(history_path, run_id):
    """PowerShell-copyable command; the current customer environment is explicit."""
    from .console_reporting import display_path, cli_command
    history = display_path(Path(history_path)).replace("'", "''")
    return f"{cli_command()} --mode live --reassess '{history}' --baseline-run-id '{run_id}'"


def resolve_reassessment(args, tenant_id):
    """Confirm explicit intent and validate the retained baseline before execution."""
    if not getattr(args, 'reassess', None):
        return args
    result = copy(args)
    result.tenant_id = tenant_id
    result, errors = normalize_reassessment(result)
    if errors == ['reassessment_baseline_selection_pending']:
        result = select_reassessment_baseline(result)
        result, errors = normalize_reassessment(result)
    if errors:
        messages = {
            'reassessment_history_environment_mismatch': 'The selected customer environment does not match this assessment history.',
            'reassessment_history_invalid': 'Assessment history is missing, unreadable or invalid. Use the history created by a completed Initial run; do not populate it manually.',
            'reassessment_history_legacy': 'Legacy history cannot establish assessment ownership. Use a completed explicit Initial run.',
            'reassessment_history_no_completed_runs': 'Assessment history has no completed runs to compare against.',
            'reassessment_history_changed_after_selection': 'Assessment history changed during baseline selection; retry explicitly.',
        }
        raise ValueError(messages.get(errors[0], errors[0]))
    from .assessment_identity import environment_id
    from .assessment_history import select_baseline
    verified_environment = environment_id(tenant_id)
    if result.primary_environment_id and result.primary_environment_id != verified_environment:
        raise ValueError('Verified primary environment does not match the selected tenant.')
    select_baseline(result.assessment_history, assessment_id=result.assessment_id,
                    environment_id=verified_environment, baseline_run_id=result.baseline_run_id)
    return result
