"""Exclusive artifact creation with guarded directory handles; no source mutation."""
from contextlib import contextmanager
import os
from pathlib import Path


def linked_path(path):
    """Treat Windows junctions and all reparse points as unverified aliases."""
    path=Path(path)
    if path.is_symlink(): return True
    try: return bool(getattr(path.lstat(),'st_file_attributes',0) & 0x400)
    except FileNotFoundError: return False


def nested_run_path(relative):
    """A numbered child execution, distinct from its completed parent package."""
    parts = Path(relative).parts
    return len(parts)>=3 and parts[0]=='Runs' and parts[1].isascii() and parts[1].isdecimal() and int(parts[1])>0


def validate_destination(path, *, artifact_kind=None):
    target = Path(os.path.abspath(path))
    for part in (target, *target.parents):
        if linked_path(part):
            raise ValueError('Publication destination contains a symbolic link.')
    if target.exists():
        raise FileExistsError('Publication cannot overwrite an existing destination.')
    for parent in target.parents:
        if (parent / 'assessment-run.json').is_file():
            relative = target.relative_to(parent)
            allowed={'Renders', 'Builds', 'deliverables', 'Rebuilds'}
            if artifact_kind=='run-seed':allowed.add('run-seeds')
            if artifact_kind=='operator-log' and relative.parts==('operator-log.jsonl',):continue
            if relative.parts[0]=='Runs':
                if relative.parts==('Runs','.publication-boundary'):
                    continue
                if nested_run_path(relative):
                    continue
            if relative.parts[0] not in allowed:
                raise ValueError('Publication cannot modify a completed assessment package.')
    return target


def validate_container(folder):
    """Check an output directory before allocation, including completed parents."""
    validate_destination(Path(folder)/'.publication-boundary')


def _create_exclusive(target, directory_fd=None, windows_directory=None, artifact_kind=None):
    """Create relative to a pinned directory, even if its path is replaced."""
    from .repository_provenance import require_publication
    require_publication()
    validate_destination(target,artifact_kind=artifact_kind)
    if os.name!='nt':
        return os.open(target.name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=directory_fd)
    import ctypes
    from ctypes import wintypes
    import msvcrt
    class UnicodeString(ctypes.Structure):
        _fields_=[('Length',wintypes.USHORT),('MaximumLength',wintypes.USHORT),('Buffer',wintypes.LPWSTR)]
    class ObjectAttributes(ctypes.Structure):
        _fields_=[('Length',wintypes.ULONG),('RootDirectory',wintypes.HANDLE),('ObjectName',ctypes.POINTER(UnicodeString)),
                  ('Attributes',wintypes.ULONG),('SecurityDescriptor',wintypes.LPVOID),('SecurityQualityOfService',wintypes.LPVOID)]
    class StatusUnion(ctypes.Union):
        _fields_=[('Status',wintypes.LONG),('Pointer',wintypes.LPVOID)]
    class IoStatus(ctypes.Structure):
        _fields_=[('Value',StatusUnion),('Information',ctypes.c_size_t)]
    buffer=ctypes.create_unicode_buffer(target.name)
    name=UnicodeString(len(target.name.encode('utf-16-le')),ctypes.sizeof(buffer),ctypes.cast(buffer,wintypes.LPWSTR))
    attributes=ObjectAttributes(ctypes.sizeof(ObjectAttributes),windows_directory,ctypes.pointer(name),0x40,None,None)
    handle=wintypes.HANDLE();status_block=IoStatus()
    library=ctypes.WinDLL('ntdll')
    create=library.NtCreateFile
    create.argtypes=[ctypes.POINTER(wintypes.HANDLE),wintypes.ULONG,ctypes.POINTER(ObjectAttributes),ctypes.POINTER(IoStatus),
                     wintypes.LPVOID,wintypes.ULONG,wintypes.ULONG,wintypes.ULONG,wintypes.ULONG,wintypes.LPVOID,wintypes.ULONG]
    create.restype=wintypes.LONG
    status=create(ctypes.byref(handle),0x40100000,ctypes.byref(attributes),ctypes.byref(status_block),None,0x80,3,2,0x200060,None,0)
    if status<0:
        library.RtlNtStatusToDosError.restype=wintypes.ULONG
        raise ctypes.WinError(library.RtlNtStatusToDosError(status))
    try: return msvcrt.open_osfhandle(handle.value,os.O_WRONLY|os.O_BINARY)
    except BaseException:
        ctypes.WinDLL('kernel32').CloseHandle(handle)
        raise


@contextmanager
def exclusive_output(path, *, binary=False, newline=None, artifact_kind=None):
    """Check eligibility before directories/open; O_EXCL prevents final-path races."""
    from .repository_provenance import require_publication
    require_publication()
    target = validate_destination(path,artifact_kind=artifact_kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    handles = []
    directory_fd = None
    try:
        if os.name == 'nt':
            # Denying FILE_SHARE_DELETE prevents ancestor replacement while writing.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            create = kernel.CreateFileW
            create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            create.restype = wintypes.HANDLE
            close = kernel.CloseHandle
            close.argtypes = [wintypes.HANDLE]
            for parent in (target.parent,):
                if parent.parent == parent:
                    continue
                if linked_path(parent):
                    raise ValueError('Publication directory changed to a symbolic link.')
                handle = create(str(parent), 0, 3, None, 3, 0x02000000 | 0x00200000, None)
                if handle == wintypes.HANDLE(-1).value:
                    raise OSError(ctypes.get_last_error(), 'Cannot lock publication directory.')
                handles.append(handle)
                final_path=kernel.GetFinalPathNameByHandleW
                final_path.argtypes=[wintypes.HANDLE,wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD]
                physical=ctypes.create_unicode_buffer(32768)
                length=final_path(handle,physical,len(physical),0)
                if not length or length>=len(physical):
                    raise OSError('Cannot verify publication directory handle.')
                actual=physical.value.removeprefix('\\\\?\\')
                if os.path.normcase(actual)!=os.path.normcase(str(parent)):
                    raise ValueError('Publication directory changed to an alias.')
            validate_destination(target,artifact_kind=artifact_kind)
            descriptor = _create_exclusive(target,windows_directory=handles[-1],artifact_kind=artifact_kind)
        else:
            directory_fd = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY)
            for component in target.parent.parts[1:]:
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            validate_destination(target,artifact_kind=artifact_kind)
            descriptor = _create_exclusive(target,directory_fd=directory_fd,artifact_kind=artifact_kind)
        with os.fdopen(descriptor, 'wb' if binary else 'w',
                       **({} if binary else {'encoding': 'utf-8', 'newline': newline})) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if handles:
            for handle in reversed(handles):
                close(handle)


def write_text(path, text, *, artifact_kind=None):
    with exclusive_output(path,artifact_kind=artifact_kind) as handle:
        handle.write(text)
    return str(path)


def copy_file(source, destination):
    """Copy bytes to a new guarded destination, retaining evidence timestamps."""
    from .repository_provenance import require_publication
    require_publication()
    import shutil
    stat=Path(source).stat()
    with exclusive_output(destination,binary=True) as output:
        with Path(source).open('rb') as original:
            shutil.copyfileobj(original,output)
        output.flush()
        if os.utime in os.supports_fd:
            os.utime(output.fileno(),ns=(stat.st_atime_ns,stat.st_mtime_ns))
        else:
            os.utime(destination,ns=(stat.st_atime_ns,stat.st_mtime_ns))


def append_receipt(path, receipt):
    """Append only to a verified operator log; never truncate or follow aliases."""
    from .repository_provenance import require_publication
    import json
    require_publication()
    target=Path(os.path.abspath(path))
    for part in (target,*target.parents):
        if linked_path(part):raise ValueError('Receipt destination contains an alias.')
    line=json.dumps(receipt,ensure_ascii=False,allow_nan=False)+'\n'
    if not target.exists():
        write_text(target,line,artifact_kind='operator-log')
        return
    before=target.stat()
    descriptor=os.open(target,os.O_RDWR|os.O_APPEND|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    with os.fdopen(descriptor,'r+',encoding='utf-8') as handle:
        opened=os.fstat(handle.fileno())
        if (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino) or opened.st_nlink!=1:
            raise ValueError('Receipt destination changed or has another file alias.')
        for part in (target,*target.parents):
            if linked_path(part):raise ValueError('Receipt destination changed to an alias.')
        for existing in handle:
            value=json.loads(existing)
            if not isinstance(value,dict) or 'identity' in value or not value.get('run_id') or not value.get('mode') or not isinstance(value.get('deliverables'),list):
                raise ValueError('Receipt update cannot modify an immutable artifact.')
        handle.write(line)
        handle.flush();os.fsync(handle.fileno())
