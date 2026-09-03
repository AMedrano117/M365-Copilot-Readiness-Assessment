"""PowerShell subprocess management for orchestrator."""

import os
import sys
import json
import hashlib
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from .spinner import get_timestamp, _stdout_lock


PURVIEW_CACHE_MAX_AGE_SECONDS = 8 * 60 * 60
COLLECTOR_DIAGNOSTICS_PATH = (
    Path(__file__).resolve().parent.parent / "Reports" / "collector_diagnostics.log"
)


def _sanitize_collector_detail(detail):
    """Redact bearer tokens and common secret assignments from collector diagnostics."""
    sanitized = str(detail or "")
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


def _launch_powershell(script_path, script_args):
    """Launch PowerShell 7 when available, with Windows PowerShell as a fallback."""
    candidates = []
    for executable_name in ("pwsh", "powershell"):
        executable = shutil.which(executable_name)
        if executable and executable not in candidates:
            candidates.append(executable)

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

        json.loads(json_payload)
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
            "tenant_id": tenant_id,
            "cached_at_epoch": time.time(),
            "purview_data_json": json_payload,
        }
        cache_path.write_text(json.dumps(cache_record, ensure_ascii=False), encoding="utf-8")
    except Exception:
        # Cache persistence is best-effort only.
        pass


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
        spinner_stop_event.clear()
        spinner_thread_holder[0] = threading.Thread(target=run_spinner, args=(message,), daemon=True)
        spinner_thread_holder[0].start()

    def stop_spinner():
        """Stop and clear spinner"""
        spinner_stop_event.set()
        if spinner_thread_holder[0] and spinner_thread_holder[0].is_alive():
            spinner_thread_holder[0].join(timeout=0.5)
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
                            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Browser sign-in requested for {service_name}\n')
                            if service_details:
                                sys.stdout.write(f'[{get_timestamp()}]   ℹ️  This popup is for: {service_details}\n')
                            sys.stdout.flush()
                            start_spinner(f'Waiting for {service_name} sign-in...')
                        elif event_type == 'AUTH_REUSED':
                            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Reusing an existing {service_name} session\n')
                            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Continuing Power Platform deployment collection...\n')
                            sys.stdout.flush()
                            start_spinner(spinner_message)
                        elif event_type == 'AUTH_COMPLETE':
                            sys.stdout.write(f'[{get_timestamp()}]   ✅ {service_name} sign-in accepted\n')
                            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Continuing Power Platform deployment collection...\n')
                            sys.stdout.flush()
                            start_spinner(spinner_message)
                        elif event_type == 'AUTH_ERROR':
                            sys.stdout.write(f'[{get_timestamp()}]   ⚠️  {service_name} sign-in failed\n')
                            if service_details:
                                sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Authentication detail: {service_details}\n')
                            sys.stdout.flush()
                        else:
                            sys.stdout.write(f'[{get_timestamp()}]   ⚠️  {service_name} collection was incomplete\n')
                            if service_details:
                                sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Collector detail: {service_details}\n')
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
            sys.stdout.write(f'[{get_timestamp()}]   ✓ Data collection complete\n')
            sys.stdout.flush()
        warnings = [line for line in stderr_lines if line.startswith('COLLECTION_WARNING')]
        _record_collector_diagnostics('Power Platform', warnings)
    elif process.returncode != 0:
        _record_collector_diagnostics('Power Platform', stderr_lines)
        with _stdout_lock:
            sys.stdout.write(f'[{get_timestamp()}]   ⚠️  Interactive Power Platform data collection unavailable; continuing with basic recommendations\n')
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
                sys.stdout.write(
                    f'[{get_timestamp()}]   ℹ️  Purview cache bypassed because interactive auth mode is set to fresh\n'
                )
                sys.stdout.flush()
    else:
        if cache_info and cache_info.get('usable'):
            os.environ['PURVIEW_DATA_SOURCE'] = 'cache'
            os.environ['PURVIEW_DATA_JSON'] = cache_info['json_payload']
            with _stdout_lock:
                sys.stdout.write(
                    f'[{get_timestamp()}]   ℹ️  Using cached Purview deployment data from a previous successful run ({_format_age(cache_info["age_seconds"])} old)\n'
                )
                sys.stdout.write(f'[{get_timestamp()}]   ✓ Purview Data Gathering  [████████████████████] 100%\n')
                sys.stdout.flush()
            return True
        if cache_info and cache_info.get('reason') == 'stale':
            with _stdout_lock:
                sys.stdout.write(
                    f'[{get_timestamp()}]   ℹ️  Purview cache is too old ({_format_age(cache_info["age_seconds"])} old); collecting fresh deployment data\n'
                )
                sys.stdout.flush()

    with _stdout_lock:
        sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Launching PowerShell to collect Purview data (may require interactive auth)...\n')
        if auth_mode == 'fresh':
            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  A fresh Microsoft 365 sign-in will be requested if needed\n')
        else:
            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Existing Microsoft 365 connections in this PowerShell session will be reused when possible\n')
            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  A new background PowerShell process may still need to establish its own service connections\n')
        sys.stdout.write(f'[{get_timestamp()}]   ⚠️  A browser sign-in window may appear if interactive authentication is needed\n')
        sys.stdout.write(f'[{get_timestamp()}]   ⚠️  Check if browser window is hidden behind other apps/screens\n')
        sys.stdout.flush()
    
    # Invoke collect_purview_data.ps1 in DataOnly mode with real-time stderr streaming
    # PS1 files are in parent directory, not in Core
    ps_script = os.path.join(os.path.dirname(__file__), '..', 'collect_purview_data.ps1')
    process = _launch_powershell(
        ps_script,
        ['-DataOnly', '-AuthMode', ('Fresh' if auth_mode == 'fresh' else 'Auto')],
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
                            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Browser sign-in requested for {service_name}\n')
                            if service_details:
                                sys.stdout.write(f'[{get_timestamp()}]   ℹ️  This popup is for: {service_details}\n')
                            start_spinner(f'Waiting for {service_name} sign-in...')
                        elif event_type == 'AUTH_REUSED':
                            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Reusing an existing {service_name} session\n')
                            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Finalizing secure service connections...\n')
                            start_spinner('Finalizing secure service connections...')
                        elif event_type == 'AUTH_COMPLETE':
                            sys.stdout.write(f'[{get_timestamp()}]   ✅ {service_name} sign-in accepted\n')
                            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Finalizing secure service connections...\n')
                            start_spinner('Finalizing secure service connections...')
                        else:
                            sys.stdout.write(f'[{get_timestamp()}]   ⚠️  {service_name} sign-in failed\n')
                            if service_details:
                                sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Authentication detail: {service_details}\n')
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
            sys.stdout.write(
                f'[{get_timestamp()}]   ⚠️  Purview interactive collection unavailable; continuing with license-based recommendations\n'
            )
            if last_detail:
                sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Purview collector detail: {last_detail}\n')
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
        json.loads(json_str)
        os.environ['PURVIEW_DATA_SOURCE'] = 'subprocess'
        os.environ['PURVIEW_DATA_JSON'] = json_str
        _save_purview_cache(tenant_id, json_str)

        with _stdout_lock:
            sys.stdout.write(f'\r[{get_timestamp()}]   ✓ Purview Data Gathering  [████████████████████] 100%\n')
            sys.stdout.flush()
        return True
    except (json.JSONDecodeError, ValueError) as e:
        _record_collector_diagnostics('Purview', [*stderr_lines, str(e)])
        with _stdout_lock:
            sys.stdout.write(
                f'[{get_timestamp()}]   ⚠️  Purview interactive collection returned unreadable output; continuing with license-based recommendations\n'
            )
            sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Purview collector detail: {e}\n')
            sys.stdout.flush()
        return False
