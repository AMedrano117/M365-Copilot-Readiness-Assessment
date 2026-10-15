"""Read-only report download comparison; no customer values or signed URLs are printed."""

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from Core.credentials_check import check_credentials, load_env_file
from Core.get_graph_client import GRAPH_SCOPE, _build_credential
from Core.orchestrator_powershell import powershell_environment


REPORT_URL = "https://graph.microsoft.com/v1.0/reports/getOffice365ActiveUserCounts(period='D7')"
JSON_REPORT_URL = "https://graph.microsoft.com/beta/reports/getOffice365ActiveUserCounts(period='D7')"
NETWORK_VARIABLES = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR')


def matrix_reports():
    """Cover the actual usage collector endpoints and its summary periods."""
    reports = [
        ('Active users D7', REPORT_URL, ('Report Refresh Date', 'Office 365', 'Report Date', 'Report Period'),
         ('reportRefreshDate', 'office365', 'reportDate', 'reportPeriod'), JSON_REPORT_URL),
        ('Apps users D30', "https://graph.microsoft.com/v1.0/reports/getM365AppUserCounts(period='D30')",
         ('Report Refresh Date', 'Report Period', 'Outlook'), ('reportRefreshDate', 'reportPeriod', 'outlook'), None),
        ('Apps platforms D30', "https://graph.microsoft.com/v1.0/reports/getM365AppPlatformUserCounts(period='D30')",
         ('Report Refresh Date', 'Report Period', 'Windows'), ('reportRefreshDate', 'reportPeriod', 'windows'), None),
    ]
    for period in ('D7', 'D28', 'D90', 'D180', 'ALL'):
        reports.append((f'Copilot summary {period} v2',
                        f"https://graph.microsoft.com/v1.0/copilot/reports/getMicrosoft365CopilotUserCountSummary(period='{period}',version='v2')",
                        ('Report Refresh Date', 'Report Period', 'Any App Active Users'),
                        ('reportRefreshDate', 'adoptionByProduct'), None))
    reports.extend([
        ('Copilot trend D28 v2',
         "https://graph.microsoft.com/v1.0/copilot/reports/getMicrosoft365CopilotUserCountTrend(period='D28',version='v2')",
         ('Report Refresh Date', 'Report Period', 'Report Date', 'Any App Active Users'),
         ('reportRefreshDate', 'adoptionByDate'), None),
        ('Copilot detail D28 v2',
         "https://graph.microsoft.com/v1.0/copilot/reports/getMicrosoft365CopilotUsageUserDetail(period='D28',version='v2')",
         ('Report Refresh Date', 'Report Period', 'User Principal Name'),
         ('reportRefreshDate', 'userPrincipalName'), None),
    ])
    return reports


def matrix_payload_summary(response, csv_fields, json_fields):
    """Recognize report data without printing any values; empty is not a pass."""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get('value'), list):
        rows = payload['value']
        if not rows:
            return 'EMPTY', 'JSON; no report rows'
        if all(isinstance(row, dict) and set(json_fields) <= row.keys() for row in rows):
            return 'PASS', f'JSON; {len(rows)} top-level rows'
        return 'INCONCLUSIVE', 'JSON; unexpected report schema'
    try:
        reader = csv.DictReader(io.StringIO(response.content.decode('utf-8-sig', errors='replace')))
        if not set(csv_fields) <= set(reader.fieldnames or []):
            return 'INCONCLUSIVE', 'unexpected report format'
        count = sum(1 for row in reader if any(value not in (None, '') for value in row.values()))
        return ('PASS', f'CSV; {count} rows') if count else ('EMPTY', 'CSV; no report rows')
    except csv.Error:
        return 'INCONCLUSIVE', 'unreadable report CSV'


async def diagnose_matrix(credential, *, client_factory=httpx.AsyncClient):
    print('Comparing usage report endpoints with the same application credential.', flush=True)
    print('CSV and JSON are tested separately; only the Active users JSON request uses beta.', flush=True)
    print('Report contents are read for format checks, then discarded. No report files are saved.', flush=True)
    print('Results show returned rows, not complete collection or report freshness.', flush=True)
    try:
        token = await asyncio.to_thread(credential.get_token, GRAPH_SCOPE)
    except Exception as exc:
        print('Authentication: FAIL (' + error_summary(exc) + ').', flush=True)
        return 2
    failures = 0
    async with client_factory(timeout=30.0, follow_redirects=False) as client:
        for label, url, csv_fields, json_fields, json_url in matrix_reports():
            for output_type in ('CSV', 'JSON'):
                request_id = str(uuid4())
                started = datetime.now(timezone.utc).isoformat(timespec='seconds')
                host, stage, graph_status, server_id = 'graph.microsoft.com', 'Graph', 0, ''
                try:
                    response = await client.get(
                        json_url if output_type == 'JSON' and json_url else url,
                        params={'$format': 'text/csv' if output_type == 'CSV' else 'application/json'},
                        headers={'Authorization': 'Bearer ' + token.token,
                                 'Accept': 'text/csv' if output_type == 'CSV' else 'application/json',
                                 'client-request-id': request_id},
                    )
                    graph_status = response.status_code
                    try:
                        server_id = str(UUID(response.headers.get('request-id', '')))
                    except ValueError:
                        pass
                    if response.status_code == 302:
                        target = download_url(response.headers.get('Location', ''))
                        if output_type == 'JSON':
                            status, detail = 'FAIL', 'HTTP 302; expected direct JSON; redirect not followed'
                        elif target is None:
                            status, detail = 'FAIL', 'HTTP 302; invalid HTTPS destination'
                        else:
                            stage, host = 'Download', target.host
                            # Supply no Graph Authorization or default authenticated client headers.
                            response = await client.get(target, headers={'Accept': 'text/csv'}, follow_redirects=True)
                            status, detail = matrix_payload_summary(response, csv_fields, json_fields) if response.status_code == 200 else (
                                'FAIL', f'HTTP {response.status_code}')
                    elif response.status_code == 200:
                        status, detail = matrix_payload_summary(response, csv_fields, json_fields)
                    else:
                        status, detail = 'FAIL', f'HTTP {response.status_code}'
                except Exception as exc:
                    status, detail = 'FAIL', error_summary(exc)
                failures += status != 'PASS'
                print(f'{label} | {output_type} | {status} | {stage} {host} | Graph HTTP {graph_status or "no response"} | {detail}', flush=True)
                print(f'  UTC {started}; client-request-id {request_id}; Graph request-id {server_id or "unavailable"}', flush=True)
    print('CSV failures with JSON passes identify alternate report routes. EMPTY means the request returned no rows, '
          'not proof of no tenant activity. HTTP 403/404 can indicate access or report availability; '
          'a transport failure is a separate result.', flush=True)
    return 1 if failures else 0


def error_summary(exc):
    """Describe the exception chain without messages, URLs, bodies or credentials."""
    parts, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        part = type(exc).__name__
        codes = [f'{name}={value}' for name in ('winerror', 'errno')
                 if isinstance(value := getattr(exc, name, None), int)]
        if codes:
            part += ' (' + ', '.join(codes) + ')'
        if part not in parts:
            parts.append(part)
        exc = exc.__cause__ or exc.__context__
    return ' -> '.join(parts)


def download_url(value):
    try:
        url = httpx.URL(value)
        if url.scheme == 'https' and url.host and not url.username and not url.password:
            return url
    except Exception:
        pass
    return None


def report_format_valid(response):
    try:
        first_line = response.content.decode('utf-8-sig', errors='replace').splitlines()[0]
        headers = set(next(csv.reader(io.StringIO(first_line))))
        return {'Report Refresh Date', 'Report Date', 'Report Period'} <= headers
    except (IndexError, StopIteration, csv.Error):
        return False


async def python_download(client, url):
    try:
        # Authorization was supplied only to the Graph request, not to the client.
        response = await client.get(url, headers={'Accept': 'text/csv'}, follow_redirects=True)
        valid = response.status_code == 200 and report_format_valid(response)
        return {'success': valid, 'status_code': response.status_code,
                'error_type': 'UnexpectedReportFormat' if response.status_code == 200 and not valid else ''}
    except Exception as exc:
        return {'success': False, 'status_code': 0, 'error_type': error_summary(exc)}


def windows_download(url):
    powershell = shutil.which('powershell.exe') if os.name == 'nt' else None
    if not powershell:
        return {'success': False, 'unavailable': True}
    script = Path(__file__).with_name('diagnose-usage-report-download.ps1')
    try:
        process = subprocess.run(
            [powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
            input=json.dumps({'url': str(url)}), capture_output=True, text=True, timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW, env=powershell_environment(powershell),
        )
        # Ignore stderr entirely: PowerShell/provider errors can echo request URLs.
        result = json.loads(process.stdout.strip())
        if not isinstance(result, dict):
            raise ValueError('Unexpected diagnostic result')
        # Only allow the helper's expected fields and safe exception type names.
        return {'success': result.get('success') is True,
                'status_code': int(result.get('status_code') or 0),
                'native_error_code': result.get('native_error_code') if isinstance(result.get('native_error_code'), int) else None,
                'error_type': str(result.get('error_type') or '') if
                str(result.get('error_type') or '').isidentifier() else 'WindowsDownloadError'}
    except Exception as exc:
        return {'success': False, 'status_code': 0, 'error_type': error_summary(exc)}


def show_result(label, result):
    if result.get('unavailable'):
        print(f'{label}: unavailable on this computer.', flush=True)
        return
    detail = f"HTTP {result['status_code']}" if result.get('status_code') else result.get('error_type') or 'No response'
    if result.get('status_code') == 200 and not result.get('success'):
        detail += ', response is not the expected aggregate report CSV'
    if result.get('native_error_code') is not None:
        detail += ', Windows socket code ' + str(result['native_error_code'])
    print(f"{label}: {'PASS' if result.get('success') else 'FAIL'} ({detail})", flush=True)


def interpretation(python_result, windows_result):
    if python_result.get('success'):
        return ('The aggregate report download works in Python now. Rerun --check-connections. '
                'If it fails intermittently, retain the diagnostic timing and ask the network team to inspect connection resets.')
    if windows_result.get('success'):
        return ('Windows downloaded the same report successfully, while Python failed. '
                'Compare Python proxy settings and certificate trust with Windows; these clients can use different network settings. '
                'Ask the network team whether TLS inspection or client-specific filtering affects Python on the reported download host.')
    if windows_result.get('unavailable'):
        return ('Python could not download the report, and Windows PowerShell is unavailable for comparison. '
                'Ask the network team to check the reported download host and the error category above.')
    return ('Both Python and Windows failed to download the report. Ask the network team to inspect outbound HTTPS '
            'to the reported download host, including proxy, firewall and TLS-inspection logs. '
            'A network interruption or Microsoft-side problem is still possible; this result does not identify which. '
            'Retry from an approved alternate network or computer if available and compare the results.')


async def diagnose_json(credential, *, client_factory=httpx.AsyncClient):
    """Test the documented beta JSON response without visiting a download URL."""
    print('Testing the beta aggregate D7 JSON report with the same application credential...', flush=True)
    print('This tests one preview report; it does not establish CSV or Copilot report availability.', flush=True)
    try:
        token = await asyncio.to_thread(credential.get_token, GRAPH_SCOPE)
    except Exception as exc:
        print('Authentication: FAIL (' + error_summary(exc) + ').', flush=True)
        return 2
    async with client_factory(timeout=30.0, follow_redirects=False) as client:
        try:
            response = await client.get(
                JSON_REPORT_URL, params={'$format': 'application/json'},
                headers={'Authorization': 'Bearer ' + token.token, 'Accept': 'application/json'},
            )
        except Exception as exc:
            print('Direct JSON report: FAIL (' + error_summary(exc) + ').', flush=True)
            return 1
        if response.status_code != 200:
            print(f'Direct JSON report: FAIL (HTTP {response.status_code}). No redirect was followed.', flush=True)
            if response.status_code in (401, 403):
                print('Check the selected credential and Reports.Read.All application consent.', flush=True)
            return 1
        try:
            payload = response.json()
            rows = payload.get('value') if isinstance(payload, dict) else None
            expected = {'reportRefreshDate', 'reportDate', 'reportPeriod', 'office365'}
            valid = isinstance(rows, list) and bool(rows) and all(
                isinstance(row, dict) and expected <= row.keys() for row in rows)
        except ValueError:
            valid = False
        if not valid:
            print('Direct JSON report: inconclusive (HTTP 200, but no rows matching the expected report schema).', flush=True)
            return 1
        print(f'Direct JSON report: PASS (HTTP 200; {len(rows)} aggregate rows; report values withheld).', flush=True)
        print('Graph returned report data without the CSV download host. The beta JSON route is a candidate '
              'workaround for this report; other reports must be tested separately.', flush=True)
        return 0


async def diagnose(credential, *, client_factory=httpx.AsyncClient, windows_probe=windows_download):
    print('Checking application authentication and requesting the aggregate D7 report...', flush=True)
    try:
        token = await asyncio.to_thread(credential.get_token, GRAPH_SCOPE)
    except Exception as exc:
        print('Authentication: FAIL (' + error_summary(exc) + '). Check the selected environment and credential.', flush=True)
        return 2
    async with client_factory(timeout=30.0, follow_redirects=False) as client:
        try:
            response = await client.get(REPORT_URL, headers={'Authorization': 'Bearer ' + token.token, 'Accept': 'text/csv'})
        except Exception as exc:
            print('Graph report request: FAIL (' + error_summary(exc) + '). No report download was attempted.', flush=True)
            return 2
        status = response.status_code
        if status == 200:
            if report_format_valid(response):
                print('Graph returned the report directly: PASS (HTTP 200). Rerun --check-connections.', flush=True)
                return 0
            print('Graph returned HTTP 200, but the response is not the expected aggregate report CSV.', flush=True)
            return 2
        if status != 302:
            print(f'Graph report request: FAIL (HTTP {status}).', flush=True)
            if status in (401, 403):
                print('Check the selected credential and Reports.Read.All application consent.', flush=True)
            else:
                print('Retry later; the service did not supply the expected report redirect.', flush=True)
            return 2
        print('Graph report request: PASS (HTTP 302). The application was authorized to request this report.', flush=True)
        url = download_url(response.headers.get('Location', ''))
        if url is None:
            print('Report redirect: FAIL (missing or invalid HTTPS destination).', flush=True)
            return 2
        print('Download host: ' + url.host + ' (temporary URL withheld).', flush=True)
        print('Trying the report download with Python...', flush=True)
        python_result = await python_download(client, url)
        show_result('Python report download', python_result)
        if python_result['success']:
            print(interpretation(python_result, {}), flush=True)
            return 0
        print('Comparing the same download with Windows PowerShell...', flush=True)
        windows_result = await asyncio.to_thread(windows_probe, url)
        show_result('Windows report download', windows_result)
        print('Next action: ' + interpretation(python_result, windows_result), flush=True)
        return 1


def main():
    parser = argparse.ArgumentParser(description='Diagnose usage-report downloads without printing credentials, report rows or temporary URLs.')
    parser.add_argument('--env-file', required=True, help='The same customer environment file used by --check-connections.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--json-only', action='store_true',
                        help='Test the beta D7 active-user report as direct JSON using the same credential; no CSV redirect is followed.')
    mode.add_argument('--matrix', action='store_true',
                      help='Compare CSV and JSON for all usage collector endpoints, including Copilot periods; print safe per-request results.')
    args = parser.parse_args()
    # The HTTP libraries can log full URLs. This command emits its own safe summary.
    logging.disable(logging.CRITICAL)
    print('Usage report download diagnostic', flush=True)
    print('Started (UTC): ' + datetime.now(timezone.utc).isoformat(timespec='seconds'), flush=True)
    print(f'Python {sys.version.split()[0]}; HTTPX {httpx.__version__}', flush=True)
    print('Run this on the same computer and network as the failed connection check.', flush=True)
    credential = None
    try:
        load_env_file(args.env_file)
        missing = check_credentials(load_environment=False)
        if missing:
            print('Configuration: missing ' + ', '.join(missing), flush=True)
            return 2
        present = [name for name in NETWORK_VARIABLES if os.environ.get(name) or os.environ.get(name.lower())]
        print('Proxy/certificate environment settings present: ' + (', '.join(present) or 'none') + ' (values withheld).', flush=True)
        credential, _kind = _build_credential()
        probe = diagnose_matrix if args.matrix else diagnose_json if args.json_only else diagnose
        return asyncio.run(probe(credential))
    except KeyboardInterrupt:
        print('Diagnostic cancelled.', flush=True)
        return 130
    except Exception as exc:
        print('Diagnostic could not complete (' + error_summary(exc) + ').', flush=True)
        return 2
    finally:
        if credential is not None:
            try:
                credential.close()
            except Exception as exc:
                print('Credential cleanup failed (' + error_summary(exc) + ').', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
