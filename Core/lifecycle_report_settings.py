"""Keep lifecycle age policy and operator-confirmed dates with the assessment."""

from datetime import date
import hashlib
import os
from pathlib import Path


def lifecycle_settings(settings=None, *, max_age_days=None, report_dates=None, evaluation_date=None):
    settings = dict(settings or {})
    age = max_age_days if max_age_days is not None else settings.get(
        'lifecycle_report_max_age_days', os.environ.get('LIFECYCLE_REPORT_MAX_AGE_DAYS', '90'))
    try:
        age = int(age)
        if age <= 0:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('Lifecycle report age limit must be a positive number of days.') from None
    confirmed = dict(settings.get('lifecycle_report_dates') or {})
    for specification in report_dates or []:
        path_text, separator, date_text = str(specification).rpartition('=')
        if not separator or not path_text.strip():
            raise ValueError('Use --lifecycle-report-date "PATH=YYYY-MM-DD" for an operator-confirmed report date.')
        try:
            reported = date.fromisoformat(date_text.strip())
        except ValueError:
            raise ValueError('Lifecycle report dates must use YYYY-MM-DD.') from None
        if evaluation_date and reported > date.fromisoformat(str(evaluation_date)):
            raise ValueError('A lifecycle report date cannot be later than the evaluation date.')
        path = Path(path_text.strip())
        if not path.is_file():
            raise ValueError(f'Lifecycle report date confirmation requires an existing file: {path}')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        confirmed[digest] = reported.isoformat()
    settings['lifecycle_report_max_age_days'] = age
    settings['lifecycle_report_dates'] = confirmed
    return settings
