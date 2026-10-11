"""Linux descriptor-boundary regressions for local encrypted storage."""

from importlib import import_module
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX-only file boundary")
safe_file = import_module("daon_user_local_service_safe_file_posix")


def test_directory_validation_rejects_outside_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(safe_file.UnsafePathError, match="outside root"):
        safe_file.validate_directory_chain(root, outside)
    with pytest.raises(safe_file.UnsafePathError, match="link"):
        safe_file.validate_directory_chain(root, root / "linked")
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(root, target_is_directory=True)
    with pytest.raises(safe_file.UnsafePathError, match="link"):
        safe_file.validate_directory_chain(linked_root, linked_root)


def test_atomic_write_rejects_paths_outside_selected_directory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(safe_file.UnsafePathError, match="temporary outside"):
        safe_file.atomic_write(root, root, outside / "tmp", root / "output", b"payload")
    with pytest.raises(safe_file.UnsafePathError, match="destination outside"):
        safe_file.atomic_write(root, root, root / "tmp", outside / "output", b"payload")
    assert list(root.iterdir()) == []
    assert list(outside.iterdir()) == []


def test_atomic_write_removes_temporary_after_short_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(safe_file.os, "write", lambda _fd, _data: 0)

    with pytest.raises(OSError, match="short write"):
        safe_file.atomic_write(root, root, root / "tmp", root / "output", b"payload")
    assert list(root.iterdir()) == []


def test_atomic_write_removes_published_file_if_final_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original = safe_file._validate_name_matches_descriptor
    checks = 0

    def fail_after_publish(directory_descriptor: int, name: str, descriptor: int) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise safe_file.UnsafePathError("post-publish verification failed")
        original(directory_descriptor, name, descriptor)

    monkeypatch.setattr(safe_file, "_validate_name_matches_descriptor", fail_after_publish)
    with pytest.raises(safe_file.UnsafePathError, match="post-publish"):
        safe_file.atomic_write(root, root, root / "tmp", root / "output", b"payload")
    assert checks == 2
    assert list(root.iterdir()) == []


def test_atomic_write_tolerates_destination_removed_before_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original = safe_file._validate_name_matches_descriptor
    checks = 0

    def remove_after_publish(directory_descriptor: int, name: str, descriptor: int) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            os.unlink(name, dir_fd=directory_descriptor)
            raise safe_file.UnsafePathError("destination removed during verification")
        original(directory_descriptor, name, descriptor)

    monkeypatch.setattr(safe_file, "_validate_name_matches_descriptor", remove_after_publish)
    with pytest.raises(safe_file.UnsafePathError, match="destination removed"):
        safe_file.atomic_write(root, root, root / "tmp", root / "output", b"payload")
    assert list(root.iterdir()) == []


def test_file_descriptor_rejects_non_regular_and_replaced_name(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    getattr(os, "mkfifo")(root / "pipe")
    directory_descriptor = safe_file._open_directory(root, root)
    try:
        with pytest.raises(safe_file.UnsafePathError, match="unexpected file type"):
            safe_file._open_file(directory_descriptor, "pipe", safe_file._FILE_READ_FLAGS | getattr(os, "O_NONBLOCK"))

        original = root / "original"
        original.write_bytes(b"old")
        descriptor = safe_file._open_file(directory_descriptor, original.name, safe_file._FILE_READ_FLAGS)
        try:
            original.rename(root / "retained")
            with pytest.raises(FileNotFoundError):
                safe_file._validate_name_matches_descriptor(directory_descriptor, original.name, descriptor)
            getattr(os, "mkfifo")(original)
            with pytest.raises(safe_file.UnsafePathError, match="unexpected file type"):
                safe_file._validate_name_matches_descriptor(directory_descriptor, original.name, descriptor)
            original.unlink()
            original.write_bytes(b"replacement")
            with pytest.raises(safe_file.UnsafePathError, match="file replaced"):
                safe_file._validate_name_matches_descriptor(directory_descriptor, original.name, descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)
