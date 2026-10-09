"""Windows credential opens: no reparses, stable ancestor locks, same-handle I/O."""
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
from pathlib import Path

_OPEN_REPARSE = 0x00200000
_BACKUP_SEMANTICS = 0x02000000


class _AttributeTag(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]


@lru_cache(maxsize=1)
def _api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateFileW": ([wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE),
        "GetFileInformationByHandleEx": ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                          wintypes.DWORD], wintypes.BOOL),
        "GetFileType": ([wintypes.HANDLE], wintypes.DWORD),
        "GetDriveTypeW": ([wintypes.LPCWSTR], wintypes.UINT),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def _open_handle(path, access, share, creation, flags):
    handle = _api().CreateFileW(str(path), access, share, None, creation, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _validate_handle(handle, *, directory):
    info = _AttributeTag()
    api = _api()
    if not api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if (api.GetFileType(handle) != 1 or info.attributes & 0x400 or
            bool(info.attributes & 0x10) != directory):
        raise RuntimeError("managed API key path must contain no reparse points")


@contextmanager
def open_key_file(path):
    """Yield a stream whose validated file and local ancestors cannot be replaced."""
    import msvcrt

    path = Path(os.path.abspath(path))
    # Never authenticate to a remote share or open a device namespace for a key.
    if (len(path.drive) != 2 or path.drive[1] != ":" or
            _api().GetDriveTypeW(path.anchor) != 3):
        raise RuntimeError("managed API key requires a local fixed-drive path")
    with ExitStack() as stack:
        for parent in reversed(path.parents):
            # Ancestors already held deny write/delete access. Avoid mkdir's
            # exist_ok stat fallback, which could follow a newly inserted junction.
            if str(parent) != path.anchor:
                try:
                    os.mkdir(parent)
                except FileExistsError:
                    pass
            handle = _open_handle(parent, 0x80, 1, 3, _OPEN_REPARSE | _BACKUP_SEMANTICS)
            stack.callback(_api().CloseHandle, handle)
            _validate_handle(handle, directory=True)

        created = True
        try:
            handle = _open_handle(path, 0xC0000000, 0, 1, _OPEN_REPARSE)
        except OSError as exc:
            if exc.winerror not in (80, 183):
                raise
            created = False
            handle = _open_handle(path, 0xC0000000, 0, 3, _OPEN_REPARSE)
        try:
            _validate_handle(handle, directory=False)
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            _api().CloseHandle(handle)
            raise
        # open_osfhandle transfers ownership; closing this fd closes that same
        # validated handle. Ancestor locks live until the stream is closed.
        try:
            os.set_inheritable(fd, False)
            stream = os.fdopen(fd, "w" if created else "r",
                               encoding="utf-8" if created else "utf-8-sig")
        except BaseException:
            os.close(fd)
            raise
        stack.enter_context(stream)
        yield stream, created

