from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final


class UnsafePathError(OSError):
    """A file-system object escaped the approved local-storage boundary."""


_REPARSE_ATTRIBUTE: Final = 0x400


def _contained(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _CREATE_NEW = 1
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_WRITE_THROUGH = 0x80000000
    _FILE_RENAME_INFO = 3
    _FILE_DISPOSITION_INFO = 4
    _FILE_NAME_NORMALIZED = 0
    _VOLUME_NAME_DOS = 0

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _FILE_DISPOSITION_INFO_STRUCT(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    class _FILE_RENAME_INFO_STRUCT(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", ctypes.c_ubyte),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = _kernel32.ReadFile.argtypes
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL

    def _raise_last_error(message: str) -> None:
        raise OSError(ctypes.get_last_error(), message)

    def _open(
        path: Path, access: int, creation: int, flags: int, *, share: int = _FILE_SHARE_READ
    ) -> int:
        handle = _kernel32.CreateFileW(
            str(path),
            access,
            share,
            None,
            creation,
            flags | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == _INVALID_HANDLE:
            _raise_last_error(f"CreateFileW failed: {path.name}")
        return int(handle)

    def _close(handle: int) -> None:
        if not _kernel32.CloseHandle(wintypes.HANDLE(handle)):
            _raise_last_error("CloseHandle failed")

    def _information(handle: int) -> _BY_HANDLE_FILE_INFORMATION:
        value = _BY_HANDLE_FILE_INFORMATION()
        if not _kernel32.GetFileInformationByHandle(wintypes.HANDLE(handle), ctypes.byref(value)):
            _raise_last_error("GetFileInformationByHandle failed")
        return value

    def _final_path(handle: int) -> Path:
        size = _kernel32.GetFinalPathNameByHandleW(
            wintypes.HANDLE(handle), None, 0, _FILE_NAME_NORMALIZED | _VOLUME_NAME_DOS
        )
        if not size:
            _raise_last_error("GetFinalPathNameByHandleW sizing failed")
        buffer = ctypes.create_unicode_buffer(size + 1)
        if not _kernel32.GetFinalPathNameByHandleW(
            wintypes.HANDLE(handle), buffer, len(buffer), _FILE_NAME_NORMALIZED | _VOLUME_NAME_DOS
        ):
            _raise_last_error("GetFinalPathNameByHandleW failed")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value).resolve()

    def _validate_handle(handle: int, root: Path, *, require_single_link: bool) -> None:
        information = _information(handle)
        if information.dwFileAttributes & _REPARSE_ATTRIBUTE:
            raise UnsafePathError("reparse point")
        if require_single_link and information.nNumberOfLinks != 1:
            raise UnsafePathError("hardlink")
        if not _contained(root.resolve(), _final_path(handle)):
            raise UnsafePathError("outside root")

    def _validate_directory(path: Path, root: Path) -> None:
        handle = _open(
            path,
            _GENERIC_READ,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS,
            share=_FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        )
        try:
            _validate_handle(handle, root, require_single_link=False)
        finally:
            _close(handle)

    def _open_validated_directory(path: Path, root: Path) -> int:
        handle = _open(
            path,
            _GENERIC_READ,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS,
            share=_FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        )
        try:
            _validate_handle(handle, root, require_single_link=False)
            return handle
        except BaseException:
            _close(handle)
            raise

    def validate_directory_chain(root: Path, directory: Path) -> None:
        root = root.resolve()
        directory = directory.resolve()
        if not _contained(root, directory):
            raise UnsafePathError("outside root")
        _validate_directory(root, root)
        relative = directory.relative_to(root)
        current = root
        for part in relative.parts:
            current /= part
            _validate_directory(current, root)

    def read_file(root: Path, path: Path) -> bytes:
        validate_directory_chain(root, path.parent)
        directory_handle = _open_validated_directory(path.parent, root.resolve())
        try:
            handle = _open(path, _GENERIC_READ, _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL)
            try:
                _validate_handle(handle, root.resolve(), require_single_link=True)
                information = _information(handle)
                size = (information.nFileSizeHigh << 32) | information.nFileSizeLow
                result = bytearray()
                remaining = size
                while remaining:
                    chunk_size = min(remaining, 1024 * 1024)
                    buffer = ctypes.create_string_buffer(chunk_size)
                    read = wintypes.DWORD()
                    if not _kernel32.ReadFile(
                        wintypes.HANDLE(handle), buffer, chunk_size, ctypes.byref(read), None
                    ):
                        _raise_last_error("ReadFile failed")
                    if read.value == 0:
                        raise OSError("short read")
                    result.extend(buffer.raw[: read.value])
                    remaining -= read.value
                return bytes(result)
            finally:
                _close(handle)
        finally:
            _close(directory_handle)

    def delete_file(root: Path, path: Path) -> None:
        validate_directory_chain(root, path.parent)
        directory_handle = _open_validated_directory(path.parent, root.resolve())
        try:
            handle = _open(path, _DELETE, _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL)
            try:
                _validate_handle(handle, root.resolve(), require_single_link=True)
                _dispose_handle(handle)
            finally:
                _close(handle)
        finally:
            _close(directory_handle)

    def _rename_handle(handle: int, destination: Path) -> None:
        encoded = str(destination).encode("utf-16-le")
        name_offset = _FILE_RENAME_INFO_STRUCT.FileName.offset
        raw = ctypes.create_string_buffer(ctypes.sizeof(_FILE_RENAME_INFO_STRUCT) + len(encoded))
        ctypes.memset(raw, 0, len(raw))
        information = ctypes.cast(raw, ctypes.POINTER(_FILE_RENAME_INFO_STRUCT)).contents
        information.ReplaceIfExists = 0
        information.RootDirectory = None
        information.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(raw) + name_offset, encoded, len(encoded))
        if not _kernel32.SetFileInformationByHandle(
            wintypes.HANDLE(handle), _FILE_RENAME_INFO, raw, len(raw)
        ):
            _raise_last_error("handle rename failed")

    def _dispose_handle(handle: int) -> None:
        value = _FILE_DISPOSITION_INFO_STRUCT(True)
        if not _kernel32.SetFileInformationByHandle(
            wintypes.HANDLE(handle), _FILE_DISPOSITION_INFO, ctypes.byref(value), ctypes.sizeof(value)
        ):
            _raise_last_error("handle disposition failed")

    def atomic_write(root: Path, directory: Path, temporary: Path, destination: Path, payload: bytes) -> None:
        validate_directory_chain(root, directory)
        directory_handle = _open_validated_directory(directory, root.resolve())
        try:
            handle = _open(
                temporary,
                _GENERIC_READ | _GENERIC_WRITE | _DELETE,
                _CREATE_NEW,
                _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_WRITE_THROUGH,
            )
            try:
                _validate_handle(handle, root.resolve(), require_single_link=True)
                offset = 0
                while offset < len(payload):
                    written = wintypes.DWORD()
                    chunk = payload[offset : offset + 1024 * 1024]
                    if not _kernel32.WriteFile(
                        wintypes.HANDLE(handle), chunk, len(chunk), ctypes.byref(written), None
                    ):
                        _raise_last_error("WriteFile failed")
                    if written.value == 0:
                        raise OSError("short write")
                    offset += written.value
                if not _kernel32.FlushFileBuffers(wintypes.HANDLE(handle)):
                    _raise_last_error("FlushFileBuffers failed")
                _validate_handle(handle, root.resolve(), require_single_link=True)
                _rename_handle(handle, destination)
                _validate_handle(handle, root.resolve(), require_single_link=True)
            except BaseException:
                try:
                    _dispose_handle(handle)
                except OSError:
                    pass
                raise
            finally:
                _close(handle)
        finally:
            _close(directory_handle)

elif TYPE_CHECKING:
    def validate_directory_chain(root: Path, directory: Path) -> None: ...

    def read_file(root: Path, path: Path) -> bytes: ...

    def delete_file(root: Path, path: Path) -> None: ...

    def atomic_write(
        root: Path,
        directory: Path,
        temporary: Path,
        destination: Path,
        payload: bytes,
    ) -> None: ...
