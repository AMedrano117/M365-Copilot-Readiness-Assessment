"""Readable operator messages shared by assessment summaries and build receipts."""

import shutil
import textwrap
import os
from pathlib import Path
import sys


_verbose = False
_color_mode = 'auto'
_windows_vt = None
_TONES = {'section': '1;96', 'info': '96', 'success': '92', 'warning': '93',
          'error': '91', 'path': '1;97', 'muted': '90', 'important': '1;97'}


def configure_console(*, verbose=False, color='auto'):
    """Presentation preferences are per run, never part of saved evidence."""
    global _verbose, _color_mode
    if color not in {'auto', 'always', 'never'}:
        raise ValueError('Console color must be auto, always or never.')
    _verbose, _color_mode = bool(verbose), color


def is_verbose():
    return _verbose


def _use_color():
    global _windows_vt
    if _color_mode != 'auto':
        return _color_mode == 'always'
    if 'NO_COLOR' in os.environ or os.environ.get('TERM') == 'dumb' or not getattr(sys.stdout, 'isatty', lambda: False)():
        return False
    if os.name == 'nt' and _windows_vt is None:
        try:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.windll.kernel32
            kernel.GetStdHandle.argtypes = [wintypes.DWORD]
            kernel.GetStdHandle.restype = wintypes.HANDLE
            kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            handle = kernel.GetStdHandle(-11)
            mode = wintypes.DWORD()
            _windows_vt = bool(kernel.GetConsoleMode(handle, ctypes.byref(mode)) and kernel.SetConsoleMode(handle, mode.value | 0x0004))
        except (AttributeError, OSError):
            _windows_vt = False
    return os.name != 'nt' or bool(_windows_vt)


def style(message, tone='info'):
    text = str(message)
    return f'\x1b[{_TONES.get(tone, _TONES["info"])}m{text}\x1b[0m' if _use_color() else text


def section(title, tone='section'):
    print('\n' + style(title, tone) + '\n' + style('-' * min(len(title), 72), tone), flush=True)


def status(message, tone='info'):
    print_paragraph(message, tone=tone)


def detail(message):
    if is_verbose():
        print_paragraph(message, tone='muted')


def display_path(path):
    """Prefer a short, exact path from the operator's current directory."""
    resolved = Path(path).resolve()
    try:
        return '.' + os.sep + str(resolved.relative_to(Path.cwd()))
    except ValueError:
        return str(resolved)


def detail_path(label, path):
    if is_verbose():
        print(f"{label}: {style(display_path(path), 'muted')}")


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def print_collection_handoff(collection_input):
    if not collection_input:
        return
    path = display_path(collection_input)
    section('COLLECTION INPUT (--collection-input)')
    # Never wrap or shorten paths/commands: the entire value must remain copyable.
    print(style(path, 'path'))
    print('Use this saved file to rebuild or add admin exports.')
    script = Path(__file__).resolve().parents[1] / 'main.py'
    command = f"& {_ps_quote(display_path(sys.executable))} {_ps_quote(display_path(script))} --mode offline `"
    section('REBUILD OFFLINE')
    print(command)
    print(f"  --collection-input {_ps_quote(path)} `")
    print('  --open-html-report')
    print("To add exports, append: --reports-dir 'path-to-your-exports-folder'")
    print('More detail: --verbose')


def print_paragraph(message, *, indent='', tone=None):
    """Use explicit line breaks so copied terminal text preserves word boundaries."""
    width = max(60, min(110, shutil.get_terminal_size((110, 20)).columns - 2))
    for paragraph in str(message).splitlines() or ['']:
        line = textwrap.fill(paragraph, width=width, initial_indent=indent,
                             subsequent_indent=indent + '  ', break_long_words=False,
                             break_on_hyphens=False)
        # Windows console setup uses buffered text streams. Flush milestones
        # before a collector can open a browser or wait for user input.
        print(style(line, tone) if tone else line, flush=True)


def print_source_gaps(source_statuses):
    """Report failed/partial reads separately from optional sources and preflight."""
    gaps = []
    for name, state in (source_statuses or {}).items():
        if name.startswith('connection_') or not isinstance(state, dict):
            continue
        status = state.get('availability_status') or state.get('status')
        if not status:
            status = 'available' if state.get('available') else 'unavailable'
        status = str(status).lower()
        if state.get('available') is False and status == 'available':
            status = 'unavailable'
        elif (state.get('truncated') or state.get('complete') is False) and status == 'available':
            status = 'partial'
        if status in {'not_requested', 'not_selected', 'disabled', 'skipped'}:
            continue
        if status in {'unavailable', 'failed', 'error', 'partial', 'partial_success', 'permission_denied'} or state.get('truncated'):
            gaps.append((name, status, state.get('reason') or state.get('error') or 'The source did not return a complete usable result.'))
    if gaps:
        section(f'Source collection gaps: {len(gaps)}', 'warning')
        for name, status, reason in gaps:
            print_paragraph(f'{name}: {status}. {reason}', indent='  ', tone='warning')
        detail('Rebuilding uses the saved source states; it does not retry these reads.')
