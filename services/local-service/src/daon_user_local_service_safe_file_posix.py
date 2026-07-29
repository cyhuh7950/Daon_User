from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import sys
from typing import TYPE_CHECKING


if sys.platform != "win32":
    class UnsafePathError(OSError):
        """A POSIX file-system object escaped the approved storage boundary."""

    _DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    _FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW
    _FILE_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    _UNSAFE_OPEN_ERRORS = frozenset({errno.ELOOP, errno.ENOTDIR})

    def _relative_directory(root: Path, directory: Path) -> Path:
        try:
            return directory.absolute().relative_to(root.absolute())
        except ValueError as error:
            raise UnsafePathError("outside root") from error

    def _unsafe_open(error: OSError) -> None:
        if error.errno in _UNSAFE_OPEN_ERRORS:
            raise UnsafePathError("link") from error
        raise error

    def _validate_descriptor(descriptor: int, *, directory: bool) -> os.stat_result:
        value = os.fstat(descriptor)
        expected = stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
        if not expected:
            raise UnsafePathError("unexpected file type")
        if not directory and value.st_nlink != 1:
            raise UnsafePathError("hardlink")
        return value

    def _open_directory(root: Path, directory: Path) -> int:
        relative = _relative_directory(root, directory)
        try:
            descriptor = os.open(root, _DIRECTORY_FLAGS)
        except OSError as error:
            _unsafe_open(error)
        try:
            _validate_descriptor(descriptor, directory=True)
            for part in relative.parts:
                try:
                    child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                except OSError as error:
                    _unsafe_open(error)
                os.close(descriptor)
                descriptor = child
                _validate_descriptor(descriptor, directory=True)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _open_file(directory_descriptor: int, name: str, flags: int, mode: int = 0o600) -> int:
        try:
            descriptor = os.open(name, flags, mode, dir_fd=directory_descriptor)
        except OSError as error:
            _unsafe_open(error)
        try:
            _validate_descriptor(descriptor, directory=False)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _validate_name_matches_descriptor(
        directory_descriptor: int, name: str, descriptor: int
    ) -> None:
        opened = _validate_descriptor(descriptor, directory=False)
        try:
            named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        except OSError as error:
            _unsafe_open(error)
        if not stat.S_ISREG(named.st_mode):
            raise UnsafePathError("unexpected file type")
        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
            raise UnsafePathError("file replaced")

    def validate_directory_chain(root: Path, directory: Path) -> None:
        descriptor = _open_directory(root, directory)
        os.close(descriptor)

    def read_file(root: Path, path: Path) -> bytes:
        validate_directory_chain(root, path.parent)
        directory_descriptor = _open_directory(root, path.parent)
        try:
            descriptor = _open_file(directory_descriptor, path.name, _FILE_READ_FLAGS)
            try:
                chunks: list[bytes] = []
                while chunk := os.read(descriptor, 1024 * 1024):
                    chunks.append(chunk)
                return b"".join(chunks)
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_descriptor)

    def delete_file(root: Path, path: Path) -> None:
        validate_directory_chain(root, path.parent)
        directory_descriptor = _open_directory(root, path.parent)
        try:
            descriptor = _open_file(directory_descriptor, path.name, _FILE_READ_FLAGS)
            try:
                _validate_name_matches_descriptor(directory_descriptor, path.name, descriptor)
                os.unlink(path.name, dir_fd=directory_descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_descriptor)

    def atomic_write(
        root: Path,
        directory: Path,
        temporary: Path,
        destination: Path,
        payload: bytes,
    ) -> None:
        if temporary.parent.absolute() != directory.absolute():
            raise UnsafePathError("temporary outside directory")
        if destination.parent.absolute() != directory.absolute():
            raise UnsafePathError("destination outside directory")
        validate_directory_chain(root, directory)
        directory_descriptor = _open_directory(root, directory)
        descriptor: int | None = None
        published = False
        try:
            descriptor = _open_file(
                directory_descriptor,
                temporary.name,
                _FILE_WRITE_FLAGS,
            )
            view = memoryview(payload)
            written = 0
            while written < len(view):
                chunk_size = os.write(descriptor, view[written:])
                if chunk_size == 0:
                    raise OSError("short write")
                written += chunk_size
            os.fsync(descriptor)
            _validate_name_matches_descriptor(directory_descriptor, temporary.name, descriptor)
            os.replace(
                temporary.name,
                destination.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            published = True
            _validate_name_matches_descriptor(directory_descriptor, destination.name, descriptor)
            os.fsync(directory_descriptor)
        except BaseException:
            if published:
                try:
                    os.unlink(destination.name, dir_fd=directory_descriptor)
                except FileNotFoundError:
                    pass
            try:
                os.unlink(temporary.name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_descriptor)
elif TYPE_CHECKING:
    class UnsafePathError(OSError):
        """Static-only POSIX contract declaration on Windows."""

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
