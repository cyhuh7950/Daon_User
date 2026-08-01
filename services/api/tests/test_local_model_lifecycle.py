from __future__ import annotations

import unittest

from daon_user_api.local_model import (
    ArtifactManifest,
    HardwareSnapshot,
    ManagedLocalModel,
)


class LocalModelLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hardware = HardwareSnapshot(
            cpu_cores=8,
            gpu_vendor="NVIDIA",
            gpu_memory_gb=8,
            memory_gb=16,
            disk_free_gb=40,
        )
        self.manifest = ArtifactManifest(
            artifact_id="local-vision-001",
            version="1.0.0",
            download_uri="https://models.daon.example/local-vision-001.bin",
            digest="sha256:" + "b" * 64,
            signature_valid=True,
            license_id="license-001",
            required_memory_gb=8,
            required_disk_gb=20,
        )

    def test_hardware_incompatibility_is_fail_closed(self) -> None:
        model = ManagedLocalModel()
        readiness = model.diagnose(self.manifest, HardwareSnapshot(2, "NONE", 0, 4, 5))
        self.assertEqual(readiness, "incompatible")
        with self.assertRaises(ValueError):
            model.install(self.manifest, HardwareSnapshot(2, "NONE", 0, 4, 5))

    def test_manifest_requires_allowlisted_signed_digest_and_license(self) -> None:
        model = ManagedLocalModel()
        invalid = ArtifactManifest(**{**self.manifest.__dict__, "signature_valid": False})
        with self.assertRaises(ValueError):
            model.verify_manifest(invalid)
        self.assertEqual(model.verify_manifest(self.manifest), "verified")

    def test_install_update_failure_rolls_back_previous_ready_version(self) -> None:
        model = ManagedLocalModel()
        model.install(self.manifest, self.hardware)
        bad_update = ArtifactManifest(**{**self.manifest.__dict__, "version": "2.0.0", "digest": "bad"})
        with self.assertRaises(ValueError):
            model.update(bad_update, self.hardware)
        self.assertEqual(model.active_version, "1.0.0")
        self.assertEqual(model.status, "ready")

    def test_uninstall_removes_ready_artifact_without_exposing_deployment(self) -> None:
        model = ManagedLocalModel()
        model.install(self.manifest, self.hardware)
        self.assertEqual(model.uninstall(), "not_installed")
        self.assertEqual(model.status, "not_installed")


if __name__ == "__main__":
    unittest.main()
