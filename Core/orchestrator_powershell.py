"""PowerShell subprocess management for orchestrator."""

import os
import sys
import json
import hashlib
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from .spinner import get_timestamp, _stdout_lock
from . import console_reporting as console


PURVIEW_CACHE_MAX_AGE_SECONDS = 8 * 60 * 60
PURVIEW_CACHE_SCHEMA_VERSION = 3
COLLECTOR_DIAGNOSTICS_PATH = (
    Path(__file__).resolve().parent.parent / "Reports" / "collector_diagnostics.log"
)


def _sanitize_collector_detail(detail):
    """Redact bearer tokens and common secret assignments from collector diagnostics."""
    sanitized = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(detail or ""))
    sanitized = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", sanitized)
    sanitized = re.sub(
        r"(?i)((?:client[_-]?secret|access[_-]?token|refresh[_-]?token|password)\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        sanitized,
    )
    sanitized = re.sub(r"\beyJ[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]+){1,2}\b", "[REDACTED_JWT]", sanitized)
    for variable_name in ("CLIENT_SECRET", "AZURE_CLIENT_SECRET"):
        secret = os.environ.get(variable_name, "")
        if len(secret) >= 8:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    return sanitized[:2000]


def _record_collector_diagnostics(collector, lines):
    """Append sanitized warnings/errors to the ignored local diagnostics log."""
    sanitized_lines = []
    for line in lines or []:
        cleaned = _sanitize_collector_detail(line).strip()
        if cleaned and cleaned not in sanitized_lines:
            sanitized_lines.append(cleaned)
    if not sanitized_lines:
        return

    try:
        COLLECTOR_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with COLLECTOR_DIAGNOSTICS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {collector}\n")
            for line in sanitized_lines:
                handle.write(f"  {line}\n")
    except OSError:
        # Diagnostics are best-effort and must never prevent an assessment run.
        pass


def _collector_failure_reason(lines):
    """Prefer the actual PowerShell error over a wrapped error-ID or stack footer."""
    candidates = []
    for line in lines:
        cleaned = _sanitize_collector_detail(line).strip()
        if not cleaned or cleaned.startswith(('AUTH_', '+', 'At ', 'CategoryInfo', 'FullyQualifiedErrorId')):
            continue
        candidates.append(cleaned)
    for pattern in (r'\b(?:400|401|403|404|429|500|503)\b',
                    r'(?i)unauthorized|forbidden|access.*denied|cannot|unable|failed|not (?:loaded|recognized)|exception|error'):
        for line in candidates:
            if re.search(pattern, line):
                return line
    return 'PowerShell collector failed. See Reports/collector_diagnostics.log for details.'


def _launch_powershell(script_path, script_args, prefer_windows=False):
    """Launch the PowerShell edition appropriate for the selected collector."""
    candidates = []
    # Discover both hosts in the conventional order, then prefer Windows
    # PowerShell for the SharePoint Online module when requested.
    for executable_name in ("pwsh", "powershell"):
        executable = shutil.which(executable_name)
        if executable and executable not in candidates:
            candidates.append(executable)
    if prefer_windows:
        candidates.sort(key=lambda item: 0 if Path(item).stem.lower() == "powershell" else 1)

    if not candidates:
        raise FileNotFoundError(
            "PowerShell was not found. Install PowerShell 7 or enable Windows PowerShell in PATH."
        )

    last_error = None
    for executable in candidates:
        try:
            return subprocess.Popen(
                [executable, "-NoProfile", "-File", script_path, *script_args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
            )
        except OSError as exc:
            # Microsoft Store execution aliases can resolve via PATH even when the
            # corresponding application is not installed. Try the next host.
            last_error = exc

    raise last_error


def _format_age(seconds):
    """Format a cache age for operator-friendly console output."""
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _get_purview_cache_path(tenant_id):
    """Return the tenant-scoped cache file path for Purview deployment data."""
    tenant_key = (tenant_id or "default").strip().lower()
    hashed_key = hashlib.sha256(tenant_key.encode("utf-8")).hexdigest()[:16]
    cache_dir = Path(__file__).resolve().parent.parent / ".cache" / "purview"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{hashed_key}.json"


def _inspect_purview_cache(tenant_id, max_age_seconds=PURVIEW_CACHE_MAX_AGE_SECONDS):
    """Inspect cached Purview deployment data and report whether it can be reused."""
    if not tenant_id:
        return None

    cache_path = _get_purview_cache_path(tenant_id)
    if not cache_path.exists():
        return None

    try:
        raw_payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if raw_payload.get("schema_version") != PURVIEW_CACHE_SCHEMA_VERSION:
            return {
                "usable": False,
                "reason": "incompatible",
                "cache_path": str(cache_path),
            }
        cached_at = float(raw_payload.get("cached_at_epoch", 0))
        age_seconds = time.time() - cached_at
        if cached_at <= 0:
            return {
                "usable": False,
                "reason": "invalid",
                "cache_path": str(cache_path),
            }

        if age_seconds > max_age_seconds:
            return {
                "usable": False,
                "reason": "stale",
                "cache_path": str(cache_path),
                "age_seconds": age_seconds,
            }

        json_payload = raw_payload.get("purview_data_json")
        if not json_payload:
            return {
                "usable": False,
                "reason": "empty",
                "cache_path": str(cache_path),
            }

        parsed_payload = json.loads(json_payload)
        if not isinstance(parsed_payload, dict) or "dlp_rules" not in parsed_payload:
            return {
                "usable": False,
                "reason": "incompatible",
                "cache_path": str(cache_path),
            }
        return {
            "usable": True,
            "reason": "fresh",
            "json_payload": json_payload,
            "cache_path": str(cache_path),
            "age_seconds": age_seconds,
        }
    except Exception:
        return {
            "usable": False,
            "reason": "unreadable",
            "cache_path": str(cache_path),
        }


def _save_purview_cache(tenant_id, json_payload):
    """Persist Purview deployment data for reuse in later runs."""
    if not tenant_id or not json_payload:
        return

    try:
        cache_path = _get_purview_cache_path(tenant_id)
        cache_record = {
            "schema_version": PURVIEW_CACHE_SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "cached_at_epoch": time.time(),
            "purview_data_json": json_payload,
        }
        cache_path.write_text(json.dumps(cache_record, ensure_ascii=False), encoding="utf-8")
    except Exception:
        # Cache persistence is best-effort only.
        pass


def _set_purview_runtime_payload(json_payload, source):
    """Keep large Purview responses out of environment variables on Windows."""
    from .get_purview_client import set_purview_data_payload

    parsed_payload = json.loads(json_payload) if isinstance(json_payload, str) else json_payload
    set_purview_data_payload(parsed_payload)
    os.environ['PURVIEW_DATA_SOURCE'] = source
    os.environ.pop('PURVIEW_DATA_JSON', None)
    return parsed_payload


async def collect_power_platform_data(
    tenant_id,
    run_power_platform,
    run_copilot_studio,
    auth_mode='auto',
):
    """Launch unified Power Platform/Copilot Studio data collector.
    
    This ensures single authentication and data sharing between both services.
    Sets environment variables that will be consumed by service pipelines.
    
    Args:
        tenant_id: Azure tenant ID
        run_power_platform: Whether Power Platform service is requested
        run_copilot_studio: Whether Copilot Studio service is requested
    """
    # Check if data already collected (avoid re-launch)
    if os.environ.get("POWER_PLATFORM_DATA_SOURCE"):
        return
    
    # Launch unified collector (PS1 files are in parent directory, not in Core)
    ps_script_path = os.path.join(os.path.dirname(__file__), "..", "collect_power_platform_and_copilot_studio_data.ps1")
    console.status('Power Platform / Copilot Studio: collecting deployment evidence; browser sign-in may be required.')
    process = _launch_powershell(
        ps_script_path,
        [
            "-DataOnly", "-TenantId", tenant_id,
            "-AuthMode", ("Fresh" if auth_mode == "fresh" else "Auto"),
        ],
    )
    
    # Spinner control
    spinner_stop_event = threading.Event()
    
    def run_spinner(message):
        """Display a rotating spinner with message"""
        spinner_chars = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
        idx = 0
        while not spinner_stop_event.is_set():
            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   {spinner_chars[idx]} {message}')
                sys.stdout.flush()
            idx = (idx + 1) % len(spinner_chars)
            time.sleep(0.1)
    
    spinner_thread_holder = [None]

    def start_spinner(message):
        """Start spinner with a new message."""
        if not console.is_verbose() or not sys.stdout.isatty():
            return
        spinner_stop_event.clear()
        spinner_thread_holder[0] = threading.Thread(target=run_spinner, args=(message,), daemon=True)
        spinner_thread_holder[0].start()

    def stop_spinner():
        """Stop and clear spinner"""
        spinner_stop_event.set()
        if spinner_thread_holder[0] and spinner_thread_holder[0].is_alive():
            spinner_thread_holder[0].join(timeout=0.5)
        if console.is_verbose() and sys.stdout.isatty():
            with _stdout_lock:
                sys.stdout.write('\r' + ' ' * 120 + '\r')
                sys.stdout.flush()
    
    # Determine spinner message based on which services are running
    if run_power_platform and run_copilot_studio:
        spinner_message = 'Collecting Power Platform & Copilot Studio data...'
    elif run_power_platform:
        spinner_message = 'Collecting Power Platform data...'
    else:  # run_copilot_studio only
        spinner_message = 'Collecting Copilot Studio data...'
    
    # Start spinner
    start_spinner(spinner_message)
    
    # Stream output for real-time device code display
    stderr_lines = []
    stdout_lines = []
    
    def stderr_thread_func(process):
        try:
            for line in iter(process.stderr.readline, ''):
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                stderr_lines.append(line)
                if (
                    line.startswith('AUTH_PROMPT')
                    or line.startswith('AUTH_COMPLETE')
                    or line.startswith('AUTH_REUSED')
                    or line.startswith('AUTH_ERROR')
                    or line.startswith('COLLECTION_WARNING')
                ):
                    parts = line.split(':', 2)
                    event_type = parts[0]
                    service_name = parts[1] if len(parts) > 1 else 'Power Platform'
                    service_details = parts[2] if len(parts) > 2 else ''
                    stop_spinner()
                    with _stdout_lock:
                        if event_type == 'AUTH_PROMPT':
                            console.status((f'Browser sign-in requested for {service_name}\n').rstrip())
                            if service_details:
                                console.detail((f'[{get_timestamp()}]   ℹ️  This popup is for: {service_details}\n').rstrip())
                            sys.stdout.flush()
                            start_spinner(f'Waiting for {service_name} sign-in...')
                        elif event_type == 'AUTH_REUSED':
                            console.detail((f'[{get_timestamp()}]   ℹ️  Reusing an existing {service_name} session\n').rstrip())
                            console.detail((f'[{get_timestamp()}]   ℹ️  Continuing Power Platform deployment collection...\n').rstrip())
                            sys.stdout.flush()
                            start_spinner(spinner_message)
                        elif event_type == 'AUTH_COMPLETE':
                            console.detail((f'[{get_timestamp()}]   ✅ {service_name} sign-in accepted\n').rstrip())
                            console.detail((f'[{get_timestamp()}]   ℹ️  Continuing Power Platform deployment collection...\n').rstrip())
                            sys.stdout.flush()
                            start_spinner(spinner_message)
                        elif event_type == 'AUTH_ERROR':
                            console.status((f'{service_name} sign-in failed\n').rstrip(), tone='warning')
                            if service_details:
                                console.status((f'Authentication detail: {service_details}\n').rstrip(), tone='warning')
                            sys.stdout.flush()
                        else:
                            console.status((f'{service_name} collection was incomplete\n').rstrip(), tone='warning')
                            if service_details:
                                console.status((f'Collector detail: {service_details}\n').rstrip(), tone='warning')
                            sys.stdout.flush()
                            start_spinner(spinner_message)
        except Exception:
            pass
    
    def stdout_thread_func(process):
        try:
            full_output = []
            for line in iter(process.stdout.readline, ''):
                if not line:
                    break
                full_output.append(line)
                line_stripped = line.rstrip()
                # Display non-JSON output in real-time
                if line_stripped and not line_stripped.startswith('{'):
                    stop_spinner()
                    with _stdout_lock:
                        sys.stdout.write(f'{line_stripped}\n')
                        sys.stdout.flush()
            stdout_lines.extend([l.rstrip() for l in full_output])
        except Exception:
            pass
    
    stderr_thread = threading.Thread(target=stderr_thread_func, args=(process,))
    stderr_thread.daemon = True
    stderr_thread.start()
    
    stdout_thread = threading.Thread(target=stdout_thread_func, args=(process,))
    stdout_thread.daemon = True
    stdout_thread.start()
    
    # Wait for completion
    process.wait()
    stop_spinner()
    stderr_thread.join(timeout=1.0)
    stdout_thread.join(timeout=1.0)
    
    # Extract JSON from stdout
    json_output = ''
    for line in reversed(stdout_lines):
        if line.startswith('{'):
            json_output = line
            break
    
    if json_output and process.returncode == 0:
        # Set environment variables for both services to consume
        os.environ["POWER_PLATFORM_DATA_SOURCE"] = "subprocess"
        os.environ["POWER_PLATFORM_DATA_JSON"] = json_output
        with _stdout_lock:
            console.detail((f'[{get_timestamp()}]   ✓ Data collection complete\n').rstrip())
            sys.stdout.flush()
        warnings = [line for line in stderr_lines if line.startswith('COLLECTION_WARNING')]
        _record_collector_diagnostics('Power Platform', warnings)
    elif process.returncode != 0:
        _record_collector_diagnostics('Power Platform', stderr_lines)
        with _stdout_lock:
            console.status((f'Interactive Power Platform data collection unavailable; continuing with basic recommendations\n').rstrip(), tone='warning')
            sys.stdout.flush()


async def collect_purview_data_via_powershell(auth_mode='auto', tenant_id=None):
    """Launch PowerShell to collect Purview data with interactive authentication.
    
    Sets environment variables that will be consumed by get_purview_client.
    
    Returns:
        bool: True if data collection succeeded, False otherwise
    """
    cache_info = _inspect_purview_cache(tenant_id)

    if auth_mode == 'fresh':
        if cache_info and cache_info.get('usable'):
            with _stdout_lock:
                console.detail((f'[{get_timestamp()}]   ℹ️  Purview cache bypassed because interactive auth mode is set to fresh\n').rstrip())
                sys.stdout.flush()
    else:
        if cache_info and cache_info.get('usable'):
            _set_purview_runtime_payload(cache_info['json_payload'], 'cache')
            with _stdout_lock:
                console.detail((f'[{get_timestamp()}]   ℹ️  Using cached Purview deployment data from a previous successful run ({_format_age(cache_info["age_seconds"])} old)\n').rstrip())
                console.detail('Purview evidence loaded from the saved cache.')
                sys.stdout.flush()
            return True
        if cache_info and cache_info.get('reason') == 'stale':
            with _stdout_lock:
                console.detail((f'[{get_timestamp()}]   ℹ️  Purview cache is too old ({_format_age(cache_info["age_seconds"])} old); collecting fresh deployment data\n').rstrip())
                sys.stdout.flush()
        elif cache_info and cache_info.get('reason') == 'incompatible':
            with _stdout_lock:
                console.detail((f'[{get_timestamp()}]   ℹ️  Purview cache predates the current DLP rule collection; collecting fresh deployment data\n').rstrip())
                sys.stdout.flush()

    with _stdout_lock:
        console.detail((f'[{get_timestamp()}]   ℹ️  Launching PowerShell to collect Purview data (may require interactive auth)...\n').rstrip())
        if auth_mode == 'fresh':
            console.detail((f'[{get_timestamp()}]   ℹ️  A fresh Microsoft 365 sign-in will be requested if needed\n').rstrip())
        else:
            console.detail((f'[{get_timestamp()}]   ℹ️  Existing Microsoft 365 connections in this PowerShell session will be reused when possible\n').rstrip())
            console.detail((f'[{get_timestamp()}]   ℹ️  A new background PowerShell process may still need to establish its own service connections\n').rstrip())
        console.detail((f'[{get_timestamp()}]   ⚠️  A browser sign-in window may appear if interactive authentication is needed\n').rstrip())
        console.detail((f'[{get_timestamp()}]   ⚠️  Check if browser window is hidden behind other apps/screens\n').rstrip())
        sys.stdout.flush()
    
    # Invoke collect_purview_data.ps1 in DataOnly mode with real-time stderr streaming
    # PS1 files are in parent directory, not in Core
    ps_script = os.path.join(os.path.dirname(__file__), '..', 'collect_purview_data.ps1')
    collector_args = [
        '-DataOnly', '-AuthMode', ('Fresh' if auth_mode == 'fresh' else 'Auto'),
        '-TenantId', tenant_id or os.environ.get('TENANT_ID', ''),
        '-ClientId', os.environ.get('CLIENT_ID', ''),
        '-Organization', os.environ.get('PURVIEW_ORGANIZATION', ''),
    ]
    certificate_path = os.environ.get('PURVIEW_CERTIFICATE_PATH', '')
    if not certificate_path and os.environ.get('CERTIFICATE_PATH', '').lower().endswith(('.pfx', '.p12')):
        certificate_path = os.environ.get('CERTIFICATE_PATH', '')
    certificate_thumbprint = os.environ.get('PURVIEW_CERTIFICATE_THUMBPRINT', '')
    certificate_password = os.environ.get('PURVIEW_CERTIFICATE_PASSWORD', os.environ.get('CERTIFICATE_PASSWORD', ''))
    if certificate_path:
        collector_args.extend(['-CertificatePath', certificate_path])
    if certificate_thumbprint:
        collector_args.extend(['-CertificateThumbprint', certificate_thumbprint])
    if certificate_password:
        collector_args.extend(['-CertificatePassword', certificate_password])
    if os.environ.get('PURVIEW_INCLUDE_SPECIALIZED', '').strip().lower() in {'1', 'true', 'yes'}:
        collector_args.append('-IncludeSpecialized')
    if (certificate_path or certificate_thumbprint) and os.environ.get('CLIENT_ID') and os.environ.get('PURVIEW_ORGANIZATION'):
        console.status('Purview: connecting to Security & Compliance and Exchange Online with the application certificate...')
    else:
        console.status('Purview: connecting to Security & Compliance and Exchange Online; browser sign-in may be required.')
    process = _launch_powershell(
        ps_script, collector_args, prefer_windows=bool(certificate_thumbprint)
    )
    
    # Spinner control
    spinner_stop_event = threading.Event()
    current_spinner_message = None
    
    def run_spinner(message):
        """Display a rotating spinner with message"""
        spinner_chars = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
        idx = 0
        while not spinner_stop_event.is_set():
            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   {spinner_chars[idx]} {message}')
                sys.stdout.flush()
            idx = (idx + 1) % len(spinner_chars)
            time.sleep(0.1)
    
    spinner_thread = None
    
    def start_spinner(message):
        """Start spinner with given message"""
        nonlocal spinner_thread, current_spinner_message
        if not console.is_verbose() or not sys.stdout.isatty():
            return
        current_spinner_message = message
        spinner_stop_event.clear()
        spinner_thread = threading.Thread(target=run_spinner, args=(message,), daemon=True)
        spinner_thread.start()
    
    def stop_spinner():
        """Stop and clear spinner"""
        nonlocal spinner_thread
        if spinner_thread and spinner_thread.is_alive():
            spinner_stop_event.set()
            spinner_thread.join(timeout=0.5)
            with _stdout_lock:
                # Clear the spinner line
                sys.stdout.write('\r' + ' ' * 120 + '\r')
                sys.stdout.flush()
    
    # Start spinner for interactive authentication
    start_spinner('Waiting for Microsoft 365 sign-in...')
    stderr_lines = []
    
    # Stream stderr in real-time to show authentication progress
    def stream_stderr():
        try:
            for line in process.stderr:
                line = line.strip()
                if not line:
                    continue
                stderr_lines.append(line)
                if (
                    line.startswith('AUTH_PROMPT')
                    or line.startswith('AUTH_COMPLETE')
                    or line.startswith('AUTH_REUSED')
                    or line.startswith('AUTH_ERROR')
                ):
                    parts = line.split(':', 2)
                    event_type = parts[0]
                    service_name = parts[1] if len(parts) > 1 else 'Microsoft 365'
                    service_details = parts[2] if len(parts) > 2 else ''
                    stop_spinner()
                    with _stdout_lock:
                        if event_type == 'AUTH_PROMPT':
                            console.status((f'Browser sign-in requested for {service_name}\n').rstrip())
                            if service_details:
                                console.detail((f'[{get_timestamp()}]   ℹ️  This popup is for: {service_details}\n').rstrip())
                            start_spinner(f'Waiting for {service_name} sign-in...')
                        elif event_type == 'AUTH_REUSED':
                            console.detail((f'[{get_timestamp()}]   ℹ️  Reusing an existing {service_name} session\n').rstrip())
                            console.detail((f'[{get_timestamp()}]   ℹ️  Finalizing secure service connections...\n').rstrip())
                            start_spinner('Finalizing secure service connections...')
                        elif event_type == 'AUTH_COMPLETE':
                            console.detail((f'[{get_timestamp()}]   ✅ {service_name} sign-in accepted\n').rstrip())
                            console.detail((f'[{get_timestamp()}]   ℹ️  Finalizing secure service connections...\n').rstrip())
                            start_spinner('Finalizing secure service connections...')
                        else:
                            console.status((f'{service_name} sign-in failed\n').rstrip(), tone='warning')
                            if service_details:
                                console.status((f'Authentication detail: {service_details}\n').rstrip(), tone='warning')
                        sys.stdout.flush()
        except ValueError:
            # Pipe closed, thread can exit
            pass
    
    stderr_thread = threading.Thread(target=stream_stderr, daemon=True)
    stderr_thread.start()
    
    # Wait for process to complete and get stdout (don't use communicate())
    stdout = process.stdout.read()
    process.wait()
    result_returncode = process.returncode
    
    # Stop any remaining spinner
    stop_spinner()
    
    # Give stderr thread time to finish processing remaining lines
    stderr_thread.join(timeout=2.0)
    
    if result_returncode != 0:
        last_detail = next(
            (
                line for line in reversed(stderr_lines)
                if line
                and not line.startswith('AUTH_PROMPT')
                and not line.startswith('AUTH_COMPLETE')
                and not line.startswith('AUTH_REUSED')
            ),
            None,
        )
        with _stdout_lock:
            console.status((f'Purview interactive collection unavailable; Purview configuration will be reported as not assessed\n').rstrip(), tone='warning')
            if last_detail:
                console.status((f'Purview collector detail: {last_detail}\n').rstrip(), tone='warning')
            sys.stdout.flush()
        _record_collector_diagnostics('Purview', stderr_lines)
        return False
    # Parse JSON output from PowerShell
    import json
    try:
        stdout_text = stdout.strip()
        json_str = None

        # Prefer parsing individual lines from the bottom up so stray banner text
        # or warnings do not corrupt a successful JSON payload.
        for line in reversed(stdout_text.splitlines()):
            candidate = line.strip()
            if not candidate.startswith('{'):
                continue
            try:
                json.loads(candidate)
                json_str = candidate
                break
            except json.JSONDecodeError:
                continue

        # Fallback for unexpected multi-line JSON output.
        if json_str is None:
            json_start = stdout_text.find('{')
            json_end = stdout_text.rfind('}')
            if json_start >= 0 and json_end >= 0:
                candidate = stdout_text[json_start:json_end + 1]
                json.loads(candidate)
                json_str = candidate

        if json_str is None:
            raise ValueError("No JSON found in PowerShell output")

        # Validate once more and inject data for get_purview_client to consume.
        parsed_payload = _set_purview_runtime_payload(json_str, 'subprocess')
        _save_purview_cache(tenant_id, json_str)

        with _stdout_lock:
            console.detail('Purview policy collection finished; see source coverage for completeness.')
            collection_summary = parsed_payload.get('collection_summary', {}) if isinstance(parsed_payload, dict) else {}
            required_failures = collection_summary.get('required_failures', []) or []
            optional_failures = collection_summary.get('optional_failures', []) or []
            if required_failures:
                names = ', '.join(str(item.get('source', 'Unknown source')) for item in required_failures)
                console.status((f'Required Purview evidence unavailable: {names}\n').rstrip(), tone='warning')
                for item in required_failures:
                    role = item.get('required_role', '')
                    reason = item.get('reason', '')
                    detail = '; '.join(value for value in (reason, f'Read access: {role}' if role else '') if value)
                    console.status((f'   {item.get("source", "Purview source")}: {detail}\n').rstrip(), tone='warning')
            if optional_failures:
                names = ', '.join(str(item.get('source', 'Unknown source')) for item in optional_failures)
                console.status((f'Optional Purview enrichment unavailable: {names}. Core DLP results are unaffected.\n').rstrip(), tone='warning')
            sys.stdout.flush()
        return True
    except (json.JSONDecodeError, ValueError) as e:
        _record_collector_diagnostics('Purview', [*stderr_lines, str(e)])
        with _stdout_lock:
            console.status((f'Purview interactive collection returned unreadable output; Purview configuration will be reported as not assessed\n').rstrip(), tone='warning')
            console.status((f'Purview collector detail: {e}\n').rstrip(), tone='warning')
            sys.stdout.flush()
        return False


SHAREPOINT_JSON_BEGIN = 'ASSESSMENT_SHAREPOINT_JSON_BEGIN'
SHAREPOINT_JSON_END = 'ASSESSMENT_SHAREPOINT_JSON_END'


def _parse_sharepoint_payload(stdout):
    """Read one framed JSON document, with conservative compatibility for older collectors."""
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(stdout or '')).lstrip('\ufeff').strip()
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == SHAREPOINT_JSON_BEGIN]
    ends = [index for index, line in enumerate(lines) if line.strip() == SHAREPOINT_JSON_END]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError('Missing, duplicated, or out-of-order SharePoint JSON frame markers.')
        payload = json.loads('\n'.join(lines[starts[0] + 1:ends[0]]))
    else:
        # A module banner can precede older collectors' JSON. Start only at the
        # first standalone document; never salvage a nested object from broken JSON.
        start = next((index for index, line in enumerate(lines) if line.lstrip().startswith(('{', '['))), None)
        if start is None:
            raise ValueError('No SharePoint JSON document found in collector stdout.')
        candidate = '\n'.join(lines[start:]).lstrip()
        payload, end = json.JSONDecoder().raw_decode(candidate)
        trailing = candidate[end:].strip()
        if trailing and any(line.lstrip().startswith(('{', '[', '}', ']', ',')) for line in trailing.splitlines()):
            raise ValueError('Unexpected additional JSON content after the SharePoint document.')
    if not isinstance(payload, dict):
        raise ValueError('SharePoint JSON must be an object.')
    for name, field, expected in (('tenant', 'settings', dict), ('sites', 'items', list), ('dag_reports', 'reports', list)):
        section = payload.get(name)
        if not isinstance(section, dict) or not isinstance(section.get('available'), bool) or not isinstance(section.get(field), expected):
            raise ValueError(f'SharePoint JSON has an invalid or missing {name} section.')
        if expected is list and any(not isinstance(row, dict) for row in section[field]):
            raise ValueError(f'SharePoint JSON has a non-object record in {name}.{field}.')
    states = payload.get('collection_status')
    if not isinstance(states, dict) or not states or any(
        not isinstance(state, dict) or not isinstance(state.get('available'), bool)
        for state in states.values()
    ):
        raise ValueError('SharePoint JSON has invalid or missing collection_status records.')
    payload['available'] = any(payload[name]['available'] for name in ('tenant', 'sites', 'dag_reports'))
    gaps = [name for name, state in states.items() if not state['available'] or state.get('availability_status') == 'partial']
    payload['availability_status'] = 'partial' if payload['available'] and gaps else 'available' if payload['available'] else 'unavailable'
    if not payload['available']:
        payload['reason'] = payload.get('reason') or 'SharePoint commands did not return usable tenant settings, site settings, or report inventory.'
    return payload


async def collect_sharepoint_governance_via_powershell(admin_url, tenant_id, auth_mode='auto'):
    """Collect SharePoint tenant/site sharing settings and existing SAM report status."""
    from .sharepoint_configuration import SHAREPOINT_ADMIN_URL_REQUIRED, is_valid_sharepoint_admin_url
    if not is_valid_sharepoint_admin_url(admin_url):
        console.status(SHAREPOINT_ADMIN_URL_REQUIRED, tone='warning')
        return {"available": False, "availability_status": "unavailable",
                "configuration_required": "SHAREPOINT_ADMIN_URL", "reason": SHAREPOINT_ADMIN_URL_REQUIRED}

    console.status('SharePoint: collecting sharing settings and completed governance reports...')

    client_id = os.environ.get("CLIENT_ID", "")
    certificate_path = os.environ.get("SHAREPOINT_CERTIFICATE_PATH", "")
    if not certificate_path and os.environ.get("CERTIFICATE_PATH", "").lower().endswith((".pfx", ".p12")):
        certificate_path = os.environ.get("CERTIFICATE_PATH", "")
    certificate_password = os.environ.get("SHAREPOINT_CERTIFICATE_PASSWORD", os.environ.get("CERTIFICATE_PASSWORD", ""))
    certificate_thumbprint = os.environ.get("SHAREPOINT_CERTIFICATE_THUMBPRINT", "")

    args = [
        "-AdminUrl", admin_url, "-TenantId", tenant_id, "-ClientId", client_id,
        "-AuthMode", "Fresh" if auth_mode == "fresh" else "Skip" if auth_mode == "skip" else "Auto",
    ]
    if certificate_path:
        args.extend(["-CertificatePath", certificate_path])
    if certificate_password:
        args.extend(["-CertificatePassword", certificate_password])
    if certificate_thumbprint:
        args.extend(["-CertificateThumbprint", certificate_thumbprint])
    download_root = Path(__file__).resolve().parent.parent / ".cache" / "sharepoint_dag"
    download_root.mkdir(parents=True, exist_ok=True)
    download_path = tempfile.mkdtemp(prefix="run-", dir=str(download_root))
    args.extend(["-DownloadPath", download_path])

    ps_script = os.path.join(os.path.dirname(__file__), "..", "collect_sharepoint_governance.ps1")
    console.detail((f'[{get_timestamp()}]   ℹ️  Collecting SharePoint sharing settings and existing SAM report status').rstrip())
    if not certificate_path and not certificate_thumbprint and auth_mode != 'skip':
        console.status('SharePoint sign-in: complete the browser prompt when it opens.')
    try:
        process = _launch_powershell(ps_script, args, prefer_windows=True)
        stdout, stderr = await __import__('asyncio').to_thread(process.communicate)
    except Exception as exc:
        detail = _sanitize_collector_detail(exc)
        _record_collector_diagnostics('SharePoint', [detail])
        console.status((f'Warning: SharePoint collector could not start: {detail}').rstrip(), tone='warning')
        return {"available": False, "reason": detail, "failure_stage": "launch", "admin_url": admin_url, "collection_status": {}}

    stderr_lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for line in stderr_lines:
        if line.startswith("AUTH_PROMPT"):
            console.detail('Browser sign-in requested for SharePoint governance collection.')
        elif line.startswith("AUTH_COMPLETE"):
            console.detail((f'[{get_timestamp()}]   ✅ SharePoint sign-in accepted').rstrip())
        elif line.startswith("AUTH_REUSED"):
            console.detail((f'[{get_timestamp()}]   ℹ️  Using SharePoint application certificate authentication').rstrip())
    if process.returncode != 0:
        detail = _collector_failure_reason(stderr_lines)
        _record_collector_diagnostics("SharePoint", stderr_lines)
        console.status((f'SharePoint governance collection unavailable: {_sanitize_collector_detail(detail)}').rstrip(), tone='warning')
        return {"available": False, "reason": _sanitize_collector_detail(detail), "failure_stage": "collector", "admin_url": admin_url, "collection_status": {}}

    try:
        payload = _parse_sharepoint_payload(stdout)
        states = payload['collection_status']
        gaps = [(name, state) for name, state in states.items() if not state['available'] or state.get('availability_status') == 'partial']
        complete_count = len(states) - len(gaps)
        message = f'SharePoint evidence: {complete_count}/{len(states)} datasets complete' + ('; collection gaps remain.' if gaps else '.')
        if gaps:
            console.status(message, tone='warning')
        else:
            console.status(message, tone='success')
        for name, state in gaps:
            detail = _sanitize_collector_detail(state.get('reason') or state.get('availability_status') or 'unavailable')
            console.status((f'Warning: {name}: {detail}').rstrip(), tone='warning')
        if gaps:
            _record_collector_diagnostics('SharePoint', [f'{name}: {state.get("reason", "unavailable")}' for name, state in gaps])
        return payload
    except (json.JSONDecodeError, ValueError) as exc:
        # Record framing facts, never raw stdout: it can contain tenant records.
        output_summary = f'stdout characters={len(stdout or "")}; nonempty lines={len((stdout or "").splitlines())}; JSON frame present={SHAREPOINT_JSON_BEGIN in (stdout or "")}'
        _record_collector_diagnostics("SharePoint", [f'admin_url={admin_url}', *stderr_lines, output_summary, str(exc)])
        console.status((f'Warning: SharePoint collector returned unreadable output after the process completed; sharing settings and SAM inventory remain unverified. See Reports/collector_diagnostics.log.').rstrip(), tone='warning')
        return {"available": False, "reason": "SharePoint collector returned unreadable output.", "failure_stage": "output", "admin_url": admin_url, "collection_status": {}}
