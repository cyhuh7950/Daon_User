from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from pypdf import PdfWriter

from daon_user_local_service.local_storage import LocalEncryptedStore, LocalStorageError
from daon_user_local_service.raw_source import RawSourceError, RawSourceService


WORKSPACE = "11111111-1111-4111-8111-111111111111"


def _text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, item in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(item)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def test_text_raw_source_is_encrypted_indexed_and_replayable(tmp_path) -> None:
    store = LocalEncryptedStore.open(tmp_path / "raw-source", bytes(range(32)))
    service = RawSourceService(store)
    content = "첫 근거입니다.\n\n둘째 근거입니다.".encode()
    digest = hashlib.sha256(content).hexdigest()

    created = service.import_source(
        workspace_id=WORKSPACE,
        filename="evidence.txt",
        content_type="text/plain",
        content=content,
        content_digest_sha256=digest,
        idempotency_key="raw-source-import-0001",
    )
    replay = service.import_source(
        workspace_id=WORKSPACE,
        filename="evidence.txt",
        content_type="text/plain",
        content=content,
        content_digest_sha256=digest,
        idempotency_key="raw-source-import-0001",
    )

    assert replay == created
    assert created.digest_sha256 == digest
    assert created.quality_state == "unverified"
    assert len(created.evidence_span_ids) == 2
    assert service.list_sources(WORKSPACE) == (created,)
    assert store.get_file(WORKSPACE, "source", created.object_id) == content
    assert set(store.list_canonical_types(WORKSPACE, "source")) >= {
        "SourceVersion", "IndexVersion", "EvidenceSpan",
    }
    spans = store.list_canonical_envelopes(WORKSPACE, "source", "EvidenceSpan")
    assert [span.payload["text"] for span in spans] == ["첫 근거입니다.", "둘째 근거입니다."]
    assert content not in (tmp_path / "raw-source" / "metadata.db").read_bytes()


@pytest.mark.parametrize(
    ("content_type", "content", "code"),
    [
        ("application/octet-stream", b"raw", "RAW_SOURCE_CONTENT_TYPE_UNSUPPORTED"),
        ("text/plain", b"\xff", "RAW_SOURCE_TEXT_INVALID"),
        ("text/markdown", b"   \n", "RAW_SOURCE_EVIDENCE_EMPTY"),
    ],
)
def test_invalid_raw_source_writes_nothing(tmp_path, content_type, content, code) -> None:
    store = LocalEncryptedStore.open(tmp_path / "raw-invalid", bytes(range(32)))
    service = RawSourceService(store)
    with pytest.raises(RawSourceError, match=code):
        service.import_source(
            workspace_id=WORKSPACE,
            filename="invalid.bin",
            content_type=content_type,
            content=content,
            content_digest_sha256=hashlib.sha256(content).hexdigest(),
            idempotency_key="raw-source-import-0002",
        )
    assert store.list_object_ids(WORKSPACE, "source") == []
    assert store.list_canonical_types(WORKSPACE, "source") == ()


def test_raw_source_digest_and_idempotency_conflicts_write_nothing_new(tmp_path) -> None:
    store = LocalEncryptedStore.open(tmp_path / "raw-conflict", bytes(range(32)))
    service = RawSourceService(store)
    content = b"trusted raw evidence"
    digest = hashlib.sha256(content).hexdigest()
    service.import_source(
        workspace_id=WORKSPACE, filename="one.txt", content_type="text/plain",
        content=content, content_digest_sha256=digest,
        idempotency_key="raw-source-import-0003",
    )
    before_objects = store.list_object_ids(WORKSPACE, "source")
    before_types = store.list_canonical_types(WORKSPACE, "source")

    with pytest.raises(RawSourceError, match="RAW_SOURCE_DIGEST_MISMATCH"):
        service.import_source(
            workspace_id=WORKSPACE, filename="bad.txt", content_type="text/plain",
            content=content, content_digest_sha256="0" * 64,
            idempotency_key="raw-source-import-0004",
        )
    with pytest.raises(RawSourceError, match="RAW_SOURCE_IDEMPOTENCY_CONFLICT"):
        service.import_source(
            workspace_id=WORKSPACE, filename="changed.txt", content_type="text/plain",
            content=b"changed", content_digest_sha256=hashlib.sha256(b"changed").hexdigest(),
            idempotency_key="raw-source-import-0003",
        )

    assert store.list_object_ids(WORKSPACE, "source") == before_objects
    assert store.list_canonical_types(WORKSPACE, "source") == before_types


def test_pdf_without_extractable_evidence_writes_nothing(tmp_path) -> None:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(output)
    content = output.getvalue()
    store = LocalEncryptedStore.open(tmp_path / "raw-empty-pdf", bytes(range(32)))

    with pytest.raises(RawSourceError, match="RAW_SOURCE_EVIDENCE_EMPTY"):
        RawSourceService(store).import_source(
            workspace_id=WORKSPACE,
            filename="empty.pdf",
            content_type="application/pdf",
            content=content,
            content_digest_sha256=hashlib.sha256(content).hexdigest(),
            idempotency_key="raw-source-import-0005",
        )

    assert store.list_object_ids(WORKSPACE, "source") == []
    assert store.list_canonical_types(WORKSPACE, "source") == ()


def test_pdf_text_is_extracted_into_unverified_evidence(tmp_path) -> None:
    content = _text_pdf("Verified local PDF evidence")
    store = LocalEncryptedStore.open(tmp_path / "raw-pdf", bytes(range(32)))
    created = RawSourceService(store).import_source(
        workspace_id=WORKSPACE,
        filename="evidence.pdf",
        content_type="application/pdf",
        content=content,
        content_digest_sha256=hashlib.sha256(content).hexdigest(),
        idempotency_key="raw-source-import-0007",
    )

    spans = store.list_canonical_envelopes(WORKSPACE, "source", "EvidenceSpan")
    indexes = store.list_canonical_envelopes(WORKSPACE, "source", "IndexVersion")
    assert created.content_type == "application/pdf"
    assert [item.payload["text"] for item in spans] == ["Verified local PDF evidence"]
    assert spans[0].payload["unverified"] is True
    assert indexes[0].payload["extractor"] == "pypdf-6.16.1"
    store.close()


def test_raw_source_survives_encrypted_store_restart(tmp_path) -> None:
    root = tmp_path / "raw-restart"
    key = bytes(range(32))
    content = b"restart evidence"
    first = LocalEncryptedStore.open(root, key)
    created = RawSourceService(first).import_source(
        workspace_id=WORKSPACE,
        filename="restart.txt",
        content_type="text/plain",
        content=content,
        content_digest_sha256=hashlib.sha256(content).hexdigest(),
        idempotency_key="raw-source-import-0006",
    )
    first.close()

    reopened = LocalEncryptedStore.open(root, key)
    assert RawSourceService(reopened).list_sources(WORKSPACE) == (created,)
    assert reopened.get_file(WORKSPACE, "source", created.object_id) == content
    assert RawSourceService(reopened).list_sources(
        "22222222-2222-4222-8222-222222222222"
    ) == ()
    reopened.close()


def test_bundle_builder_failure_removes_only_new_encrypted_object(tmp_path) -> None:
    store = LocalEncryptedStore.open(tmp_path / "raw-builder-failure", bytes(range(32)))

    def fail_builder(_object_id: str):
        raise ValueError("fixture failure")

    with pytest.raises(LocalStorageError, match="LOCAL_CANON_IMMUTABLE"):
        store.put_raw_source_bundle(
            WORKSPACE,
            b"must not remain",
            content_type="text/plain",
            build_envelopes=fail_builder,
        )

    assert store.list_object_ids(WORKSPACE, "source") == []
    assert store.list_canonical_types(WORKSPACE, "source") == ()
    store.close()
