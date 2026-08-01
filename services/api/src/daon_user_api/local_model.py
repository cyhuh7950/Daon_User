from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final


_DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_MODEL_URI_PREFIX: Final = "https://models.daon.example/"


@dataclass(frozen=True)
class HardwareSnapshot:
    cpu_cores: int
    gpu_vendor: str
    gpu_memory_gb: int
    memory_gb: int
    disk_free_gb: int


@dataclass(frozen=True)
class ArtifactManifest:
    artifact_id: str
    version: str
    download_uri: str
    digest: str
    signature_valid: bool
    license_id: str
    required_memory_gb: int
    required_disk_gb: int


class ManagedLocalModel:
    def __init__(self) -> None:
        self.status = "not_installed"
        self.active_version: str | None = None
        self._active_manifest: ArtifactManifest | None = None

    def diagnose(self, manifest: ArtifactManifest, hardware: HardwareSnapshot) -> str:
        if hardware.memory_gb < manifest.required_memory_gb:
            return "incompatible"
        if hardware.disk_free_gb < manifest.required_disk_gb:
            return "incompatible"
        if hardware.cpu_cores < 2:
            return "incompatible"
        return "compatible"

    def verify_manifest(self, manifest: ArtifactManifest) -> str:
        if not manifest.download_uri.startswith(_MODEL_URI_PREFIX):
            raise ValueError("model source is not allowlisted")
        if not _DIGEST.fullmatch(manifest.digest):
            raise ValueError("model digest is invalid")
        if not manifest.signature_valid:
            raise ValueError("model signature is invalid")
        if not manifest.license_id:
            raise ValueError("model license is missing")
        return "verified"

    def install(self, manifest: ArtifactManifest, hardware: HardwareSnapshot) -> str:
        if self.diagnose(manifest, hardware) != "compatible":
            self.status = "failed"
            raise ValueError("hardware is incompatible")
        self.status = "verifying"
        self.verify_manifest(manifest)
        self.status = "installing"
        self._active_manifest = manifest
        self.active_version = manifest.version
        self.status = "ready"
        return self.status

    def update(self, manifest: ArtifactManifest, hardware: HardwareSnapshot) -> str:
        previous = self._active_manifest
        previous_version = self.active_version
        try:
            self.status = "updating"
            self.install(manifest, hardware)
        except ValueError:
            self.status = "rollback"
            self._active_manifest = previous
            self.active_version = previous_version
            self.status = "ready" if previous is not None else "failed"
            raise
        return self.status

    def uninstall(self) -> str:
        self.status = "uninstalling"
        self._active_manifest = None
        self.active_version = None
        self.status = "not_installed"
        return self.status
