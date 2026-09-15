"""Aggregate the documented paid-Copilot v2 report without retaining user identities."""

from datetime import date
import re


def _count(value):
    if isinstance(value, bool):
        return None
    text = str(value).strip() if value is not None else ''
    if not re.fullmatch(r'\d+|\d{1,3}(?:,\d{3})+', text):
        return None
    return int(text.replace(',', ''))


def summarize_activity(rows, *, error='', period='D28'):
    result = {'available': False, 'availability_status': 'unavailable', 'period': period,
              'refresh_date': '', 'source': 'Microsoft Graph paid Copilot usage user-detail v2',
              'reason': error or 'No usable paid-Copilot detail rows returned.', 'records_collected': 0,
              'population': 'Users included in the paid Microsoft 365 Copilot report',
              'total_prompts': None, 'work_chat_prompts': None, 'web_chat_prompts': None,
              'active_user_days': None}
    if error or not isinstance(rows, list) or not rows:
        return result
    selected, seen, dates = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            result['reason'] = 'An unreadable detail row prevents a complete aggregate.'
            return result
        identity = row.get('userPrincipalName') or row.get('userId')
        if not identity or str(identity).casefold() in seen:
            result['reason'] = 'Missing or repeated user identifiers prevent a reliable aggregate.'
            return result
        seen.add(str(identity).casefold())
        values = row
        nested = row.get('copilotActivityUserDetailsByPeriod')
        if isinstance(nested, list):
            matches = [item for item in nested if isinstance(item, dict)
                       and str(item.get('reportPeriod')) in {period, period[1:]}]
            if len(matches) != 1:
                result['reason'] = 'The requested reporting period is missing or ambiguous.'
                return result
            values = {**row, **matches[0]}
        if values.get('reportPeriod') is not None and str(values['reportPeriod']) not in {period, period[1:]}:
            result['reason'] = 'The returned period differs from the requested period.'
            return result
        try:
            dates.add(date.fromisoformat(str(row.get('reportRefreshDate'))).isoformat())
        except ValueError:
            result['reason'] = 'The report refresh date was not returned in a supported format.'
            return result
        selected.append(values)
    if len(dates) != 1:
        result['reason'] = 'Detail rows have different refresh dates; totals were not combined.'
        return result
    fields = {'total_prompts': 'promptsSubmittedForAllApps',
              'work_chat_prompts': 'promptsSubmittedForCopilotChatWork',
              'web_chat_prompts': 'promptsSubmittedForCopilotChatWeb',
              'active_user_days': 'activeUsageDaysForAllApps'}
    for key, field in fields.items():
        values = [_count(row.get(field)) for row in selected]
        if all(value is not None for value in values):
            result[key] = sum(values)
    missing = [key for key in fields if result[key] is None]
    result.update(available=any(result[key] is not None for key in fields),
                  availability_status='unavailable' if len(missing) == len(fields) else 'partial' if missing else 'available',
                  reason='Some v2 metrics were missing or invalid; incomplete totals remain unknown.' if missing else '',
                  refresh_date=next(iter(dates)), records_collected=len(selected))
    return result
