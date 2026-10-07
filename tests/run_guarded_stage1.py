"""Run tests without tenant access or writes to the repository/customer artifacts.

python -B tests/run_guarded_stage1.py --strict --regression
python -B tests/run_guarded_stage1.py --strict test_stage1_semantics
python -B tests/run_guarded_stage1.py --allow-temp test_dashboard_package

Temporary writes are confined to one new OS-temp directory. Subprocesses and
external sockets remain blocked. Tests are never automatically skipped.
"""

import argparse
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest


class ConciseResult(unittest.TextTestResult):
    def printErrors(self):
        for kind, entries in (('ERROR', self.errors), ('FAIL', self.failures)):
            for test, detail in entries:
                lines = detail.splitlines()
                message = next((line for line in lines if line.startswith(('AssertionError:', 'RuntimeError:',
                    'FileNotFoundError:', 'TypeError:', 'ValueError:', 'ImportError:', 'AttributeError:'))),
                    next((line for line in reversed(lines) if line.strip()), ''))
                self.stream.writeln(f'{kind}: {test.id()}')
                self.stream.writeln(message[:1000])

REGRESSION = [
    'test_shared_assessment_result.EvidenceContractTests',
    'test_shared_assessment_result.SharedAssessmentTests',
    'test_tenant_baseline', 'test_finding_evidence',
    'test_methodology_v4.PolicyAndOperationTests',
    'test_methodology_v4.DeviceAndSourceTests',
]
PROCESS_TESTS = [
    'test_cleanup_local.LocalCleanupTests',
    'test_cleanup_service_principal.CleanupServicePrincipalTests',
    'test_permission_profile_setup.OfflineSetupProfileTests',
    'test_permission_profile_setup.WorkloadAccessSetupTests',
    'test_sharepoint_export_directories.SharePointExportDirectoryTests',
    'test_sharepoint_site_details.SharePointIdentityReadTests',
    'test_optional_offline_imports.OptionalOfflineImportTests',
    'test_orchestrator_setup.PowerShellModuleSetupTests.test_real_read_only_probe_has_no_powershell_parser_error',
    'test_certificate_validation.GraphCertificateValidationTests.test_stdin_validator_does_not_emit_password_or_private_key',
    'test_certificate_validation.GraphCertificateValidationTests.test_utf8_stdin_accepts_optional_bom_with_non_ascii_path_and_password',
]
CACHE_TESTS = [
    'test_sharepoint_collector_runtime.SharePointCollectionTests.' + name for name in (
        'test_default_keeps_browser_prompt_and_collection_outcome',
        'test_failed_sign_in_shows_http_error_instead_of_wrapped_error_identifier',
        'test_parse_failure_is_visible_and_raw_stdout_not_logged',
        'test_partial_collection_prints_failed_dataset',
        'test_verbose_adds_collector_success_details',
    )
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--strict', action='store_true')
    modes.add_argument('--allow-temp', action='store_true')
    parser.add_argument('--regression', action='store_true')
    parser.add_argument('--discover', action='store_true')
    parser.add_argument('--safe-discover', action='store_true', help='Discover Python tests excluding subprocess and retained-cache tests listed below')
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('tests', nargs='*')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / '.assessment-deps'), str(root / 'tests'), str(root)]
    # Azure's import-time user agent queries the OS through platform on Windows.
    # Warm only that local OS metadata before prohibiting subprocess execution.
    platform.platform()
    temporary = tempfile.TemporaryDirectory(prefix='readiness-stage1-tests-') if args.allow_temp else None
    allowed = Path(temporary.name).resolve() if temporary else None
    if allowed:
        tempfile.tempdir = str(allowed)
    original_cwd = Path.cwd()
    violations = []

    def reject(event, value):
        frame = sys._getframe(1)
        while frame:
            case = frame.f_locals.get('self')
            if isinstance(case, unittest.TestCase):
                value = str(value) + ' during ' + case.id()
                break
            frame = frame.f_back
        violations.append((event, str(value)))
        raise RuntimeError('Stage 1 test guard blocked ' + event + ': ' + str(value))

    def writable(event, value):
        if isinstance(value, int):
            return  # Existing file descriptors (stdout and test-owned handles).
        target = Path(os.fsdecode(value)).resolve()
        if allowed and target.is_relative_to(allowed):
            return
        reject(event, target)

    def local_socket_setup(event, values):
        frame = sys._getframe(2)
        while frame:
            if frame.f_code.co_name == '_fallback_socketpair' and frame.f_globals.get('__name__') == 'socket':
                return True
            if (event == 'socket.bind' and values[1] == ('::1', 0) and
                    frame.f_code.co_name == '_has_ipv6' and frame.f_globals.get('__name__') == 'urllib3.util.connection'):
                return True  # Import-time OS capability probe; sends no traffic.
            frame = frame.f_back
        return False

    def guard(event, values):
        if event == 'open':
            mode, flags = values[1] or '', values[2] or 0
            if any(char in mode for char in 'wax+') or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                writable(event, values[0])
        elif event in {'os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.utime', 'os.truncate'}:
            writable(event, values[0])
        elif event in {'os.rename', 'os.link', 'os.symlink'}:
            writable(event, values[0])
            writable(event, values[1])
        elif event in {'subprocess.Popen', 'os.system', 'os.startfile', 'os.startfile/2'}:
            reject(event, 'subprocess execution')
        elif event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo', 'socket.sendto'}:
            # asyncio uses a local socketpair for its wakeup pipe on Windows.
            # Only Python's own socketpair setup is allowed, never test traffic.
            if not local_socket_setup(event, values):
                reject(event, 'network access')

    sys.addaudithook(guard)
    if allowed:
        os.chdir(allowed)  # Relative report/cache defaults also stay in the sandbox.
    names = (REGRESSION if args.regression else []) + args.tests
    suite = (unittest.defaultTestLoader.discover(str(root / 'tests')) if args.discover or args.safe_discover
             else unittest.defaultTestLoader.loadTestsFromNames(names or ['test_stage1_semantics']))
    if args.safe_discover:
        def cases(group):
            for case in group:
                if isinstance(case, unittest.TestSuite):
                    yield from cases(case)
                else:
                    yield case
        retained, excluded = [], []
        for case in cases(suite):
            (excluded if any(case.id() == prefix or case.id().startswith(prefix + '.') for prefix in PROCESS_TESTS + CACHE_TESTS) else retained).append(case)
        suite = unittest.TestSuite(retained)
        print('EXCLUDED SUBPROCESS/CACHE TESTS:', len(excluded))
        for prefix in PROCESS_TESTS:
            print('EXCLUDED:', prefix)
        for prefix in CACHE_TESTS:
            print('EXCLUDED RETAINED CACHE WRITE:', prefix)
    result = unittest.TextTestRunner(verbosity=1 if args.quiet else 2,
                                   resultclass=ConciseResult if args.quiet else unittest.TextTestResult).run(suite)
    failed_cases = {getattr(test, 'test_case', test).id() for test, _ in result.failures}
    error_cases = {getattr(test, 'test_case', test).id() for test, _ in result.errors}
    print(f'ACTUAL: run={result.testsRun} passed={result.testsRun-len(failed_cases | error_cases)-len(result.skipped)} '
          f'failed_tests={len(failed_cases)} error_tests={len(error_cases)} '
          f'failed_assertions={len(result.failures)} skipped={len(result.skipped)} guard_blocks={len(violations)}')
    for test, reason in result.skipped:
        print('SKIPPED:', test.id(), reason)
    for event, value in violations:
        print('GUARD BLOCK:', event, value)
    if temporary:
        os.chdir(original_cwd)
        temporary.cleanup()
    return 0 if result.wasSuccessful() and not violations else 1


if __name__ == '__main__':
    raise SystemExit(main())
