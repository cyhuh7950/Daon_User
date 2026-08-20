from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from daon_user_api.license import (
    LicenseContext,
    LicenseError,
    LicenseService,
    ReferenceLicenseRepository,
    RsaPublicKey,
    RsaSha256LicenseVerifier,
    UnavailableLicenseVerifier,
)


NOW = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


@pytest.fixture()
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _service(signing_key, *, usage=None):
    numbers = signing_key.public_key().public_numbers()
    repository = ReferenceLicenseRepository()
    verifier = RsaSha256LicenseVerifier({"release-1": RsaPublicKey(numbers.n, numbers.e)})
    service = LicenseService(
        repository,
        verifier,
        product_code="daon-user",
        clock=lambda: NOW,
        usage_reader=lambda _context: dict(usage or {"notebooks": 2, "generation_runs": 4}),
    )
    return service, repository


def _document(signing_key, **overrides):
    claims = {
        "schema_version": 1,
        "license_id": "license-release-1-001",
        "product": "daon-user",
        "edition": "enterprise",
        "organization_id": "tenant-001",
        "issued_at": (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (NOW + timedelta(days=20)).isoformat().replace("+00:00", "Z"),
        "features": ["citation", "studio_generation"],
        "resource_limits": {"generation_runs": 10, "notebooks": 5},
    }
    claims.update(overrides)
    payload = json.dumps(claims, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    signature = signing_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    return {
        "schema_version": 1,
        "key_id": "release-1",
        "algorithm": "RS256",
        "claims": claims,
        "signature": _b64url(signature),
    }


def _context(*, tenant_id="tenant-001"):
    return LicenseContext(tenant_id, "workspace-001", "user-001", "trace-license-001", "policy-v1")


def test_apply_verifies_signature_and_projects_safe_read_only_status(signing_key):
    service, repository = _service(signing_key)
    view, replayed = service.apply(_context(), _document(signing_key), "license-apply-idem-0001")

    assert replayed is False
    assert repository.count(_context()) == 1
    assert view == {
        "product": "daon-user",
        "edition": "enterprise",
        "license_id_hint": "…1-001",
        "issued_at": "2026-08-14T08:00:00Z",
        "expires_at": "2026-09-04T08:00:00Z",
        "status": "expiring_soon",
        "features": ["citation", "studio_generation"],
        "resources": [
            {"resource": "generation_runs", "limit": 10, "used": 4, "remaining": 6, "status": "available"},
            {"resource": "notebooks", "limit": 5, "used": 2, "remaining": 3, "status": "available"},
        ],
        "warning": {"code": "LICENSE_EXPIRES_WITHIN_30_DAYS", "action": "조직 관리자에게 라이선스 갱신을 요청하세요."},
        "creation_allowed": True,
        "existing_read_allowed": True,
        "existing_export_allowed": True,
    }


@pytest.mark.parametrize(
    "mutation,code",
    [
        ({"product": "other-product"}, "LICENSE_PRODUCT_MISMATCH"),
        ({"organization_id": "tenant-002"}, "LICENSE_ORGANIZATION_MISMATCH"),
        ({"license_id": "x"}, "LICENSE_DOCUMENT_INVALID"),
        ({"schema_version": 2}, "LICENSE_SCHEMA_UNSUPPORTED"),
        ({"expires_at": (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")}, "LICENSE_EXPIRED"),
    ],
)
def test_invalid_claims_fail_before_repository_write(signing_key, mutation, code):
    service, repository = _service(signing_key)
    with pytest.raises(LicenseError) as denied:
        service.apply(_context(), _document(signing_key, **mutation), "license-apply-idem-0002")
    assert denied.value.code == code
    assert repository.count(_context()) == 0


def test_invalid_signature_and_replay_conflict_write_zero(signing_key):
    service, repository = _service(signing_key)
    document = _document(signing_key)
    service.apply(_context(), document, "license-apply-idem-0003")
    replay, replayed = service.apply(_context(), document, "license-apply-idem-0003")
    assert replayed is True
    assert replay["license_id_hint"] == "…1-001"
    assert repository.count(_context()) == 1

    tampered = _document(signing_key, license_id="license-release-1-002")
    with pytest.raises(LicenseError) as conflict:
        service.apply(_context(), tampered, "license-apply-idem-0003")
    assert conflict.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert repository.count(_context()) == 1

    tampered["signature"] = _b64url(b"invalid")
    with pytest.raises(LicenseError) as denied:
        service.apply(_context(), tampered, "license-apply-idem-0004")
    assert denied.value.code == "LICENSE_SIGNATURE_INVALID"
    assert repository.count(_context()) == 1


@pytest.mark.parametrize("field,value", [
    ("key_id", "release-2"),
    ("algorithm", "RS512"),
    ("signature", "changed-signature"),
])
def test_idempotency_fingerprint_covers_full_envelope_before_verification(
    signing_key, field, value,
):
    service, repository = _service(signing_key)
    document = _document(signing_key)
    service.apply(_context(), document, "license-envelope-idem-0001")

    changed = dict(document)
    changed[field] = value
    service._verifier = UnavailableLicenseVerifier()
    with pytest.raises(LicenseError) as conflict:
        service.apply(_context(), changed, "license-envelope-idem-0001")

    assert conflict.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert repository.count(_context()) == 1


def test_idempotency_fingerprint_covers_workspace_scope(signing_key):
    service, repository = _service(signing_key)
    document = _document(signing_key)
    service.apply(_context(), document, "license-workspace-idem-0001")

    other_workspace = LicenseContext(
        "tenant-001", "workspace-002", "user-001", "trace-license-002", "policy-v1",
    )
    with pytest.raises(LicenseError) as conflict:
        service.apply(other_workspace, document, "license-workspace-idem-0001")

    assert conflict.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert repository.count(_context()) == 1


def test_rs256_rejects_signature_integer_at_or_above_modulus():
    for counter in range(128):
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        document = _document(signing_key, license_id=f"license-release-malleable-{counter:03d}")
        numbers = signing_key.public_key().public_numbers()
        width = (numbers.n.bit_length() + 7) // 8
        signature = int.from_bytes(base64.urlsafe_b64decode(document["signature"] + "=="), "big")
        malleable = signature + numbers.n
        if malleable < 1 << (width * 8):
            document["signature"] = _b64url(malleable.to_bytes(width, "big"))
            verifier = RsaSha256LicenseVerifier({"release-1": RsaPublicKey(numbers.n, numbers.e)})
            with pytest.raises(LicenseError) as denied:
                verifier.verify(document)
            assert denied.value.code == "LICENSE_SIGNATURE_INVALID"
            return
    pytest.fail("test key generation did not produce a same-width malleable signature")


def test_limit_reached_blocks_only_new_generation(signing_key):
    service, _ = _service(signing_key, usage={"notebooks": 2, "generation_runs": 10})
    service.apply(_context(), _document(signing_key), "license-apply-idem-0005")
    view = service.get(_context())
    assert view["status"] == "limit_reached"
    assert view["creation_allowed"] is False
    assert view["existing_read_allowed"] is True
    assert view["existing_export_allowed"] is True
    with pytest.raises(LicenseError) as denied:
        service.require_new_generation(_context())
    assert denied.value.code == "LICENSE_RESOURCE_LIMIT_REACHED"


def test_creation_action_requires_mapped_feature_and_each_resource_capacity(signing_key):
    service, _ = _service(signing_key, usage={"generation_runs": 9, "studio_outputs": 1})
    document = _document(
        signing_key,
        resource_limits={"generation_runs": 10, "studio_outputs": 2},
    )
    service.apply(_context(), document, "license-apply-idem-action-0001")
    service.require_creation(
        _context(), "studio.generate", {"generation_runs": 1, "studio_outputs": 1},
    )

    service._usage_reader = lambda _context: {"generation_runs": 10, "studio_outputs": 1}
    with pytest.raises(LicenseError) as limit_denied:
        service.require_creation(
            _context(), "studio.generate", {"generation_runs": 1, "studio_outputs": 1},
        )
    assert limit_denied.value.code == "LICENSE_RESOURCE_LIMIT_REACHED"

    no_feature_service, _ = _service(signing_key)
    no_feature_service.apply(
        _context(), _document(signing_key, features=["citation"]),
        "license-apply-idem-action-0002",
    )
    with pytest.raises(LicenseError) as feature_denied:
        no_feature_service.require_creation(
            _context(), "studio.generate", {"generation_runs": 1},
        )
    assert feature_denied.value.code == "LICENSE_FEATURE_NOT_ALLOWED"


def test_source_creation_maps_citation_feature_to_source_and_storage_resources(signing_key):
    service, _ = _service(signing_key, usage={"source_versions": 1, "storage_bytes": 90})
    service.apply(
        _context(),
        _document(
            signing_key,
            features=["citation"],
            resource_limits={"source_versions": 2, "storage_bytes": 100},
        ),
        "license-apply-idem-action-0003",
    )
    service.require_creation(
        _context(), "source.create", {"source_versions": 1, "storage_bytes": 10},
    )
    with pytest.raises(LicenseError) as denied:
        service.require_creation(
            _context(), "source.create", {"source_versions": 1, "storage_bytes": 11},
        )
    assert denied.value.code == "LICENSE_RESOURCE_LIMIT_REACHED"


def test_notebook_creation_maps_management_feature_to_notebook_resource(signing_key):
    service, _ = _service(signing_key, usage={"notebooks": 1})
    service.apply(
        _context(),
        _document(
            signing_key,
            features=["notebook_management"],
            resource_limits={"notebooks": 2},
        ),
        "license-apply-idem-action-0004",
    )
    service.require_creation(_context(), "notebook.create", {"notebooks": 1})
    with pytest.raises(LicenseError) as denied:
        service.require_creation(_context(), "notebook.create", {"notebooks": 2})
    assert denied.value.code == "LICENSE_RESOURCE_LIMIT_REACHED"
