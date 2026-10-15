"""Sort reference actions for owner review; never create technical findings."""
import re


def prioritize_actions(rows):
    queue = []
    for row in rows:
        status = str(row.get('Test result') or '').casefold()
        responsibility = str(row.get('Responsibility') or '').casefold()
        points = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', str(row.get('Points achieved') or ''))
        earned, possible = (map(int, points.groups()) if points else (None, None))
        remaining = possible - earned if possible is not None and earned <= possible else None
        if responsibility == 'microsoft':
            rank, label, reason = 5, 'Microsoft-managed reference', 'Service-side action; no customer remediation is inferred.'
        elif responsibility != 'customer':
            rank, label, reason = 4, 'Confirm responsibility', 'The export does not establish who manages this action.'
        elif row.get('Import conflicts'):
            rank, label, reason = 0, 'Reconcile source conflict', row['Import conflicts']
        elif 'failedhighrisk' in status:
            rank, label, reason = 0, 'Review first', 'Microsoft reports FailedHighRisk; validate scope, current evidence and the owner response.'
        elif status.startswith('failed'):
            rank, label, reason = 1, 'Review reported failure', 'Microsoft reports a failed test; confirm the current result with the owner.'
        elif status in {'passed', 'pass'}:
            rank, label, reason = 3, 'Verify completed reference', 'Retain the reported pass and its test date; it does not close a technical control.'
        elif status == 'notinscope' or str(row.get('Implementation status') or '').casefold() == 'notinscope':
            rank, label, reason = 3, 'Confirm scope', 'Confirm the recorded scope decision with the assessment owner.'
        else:
            rank, label, reason = 2, 'Plan owner review', 'Blank or unknown test status is unverified, rather than a confirmed failure.'
        row.update({'Review priority': label, 'Priority reason': reason, 'Review order': rank,
                    'Points earned': earned, 'Points possible': possible, 'Points remaining': remaining})
        if responsibility == 'customer' and rank <= 2:
            queue.append(row)
    return sorted(queue, key=lambda row: (row['Review order'], -(row['Points remaining'] or 0),
                                          row['Improvement action'].casefold(), row.get('Source file', ''), row.get('Source row', 0)))
