from __future__ import annotations

import sys
from pathlib import Path


if sys.platform == "win32":
    import daon_user_local_service_safe_file_win32 as _implementation
else:
    import daon_user_local_service_safe_file_posix as _implementation


UnsafePathError = _implementation.UnsafePathError


def validate_directory_chain(root: Path, directory: Path) -> None:
    _implementation.validate_directory_chain(root, directory)


def read_file(root: Path, path: Path) -> bytes:
    validate_directory_chain(root, path.parent)
    return _implementation.read_file(root, path)


def delete_file(root: Path, path: Path) -> None:
    validate_directory_chain(root, path.parent)
    _implementation.delete_file(root, path)


def atomic_write(
    root: Path,
    directory: Path,
    temporary: Path,
    destination: Path,
    payload: bytes,
) -> None:
    validate_directory_chain(root, directory)
    _implementation.atomic_write(root, directory, temporary, destination, payload)


__all__ = [
    "UnsafePathError",
    "atomic_write",
    "delete_file",
    "read_file",
    "validate_directory_chain",
]
