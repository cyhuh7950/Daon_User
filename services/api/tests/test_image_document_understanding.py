import base64
import io
import json
import unittest
from PIL import Image

from daon_user_api.document_processing import DocumentProcessingContext, StoredSourceDocument
from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
from daon_user_api.workspace_model_defaults import ResolvedModel
from daon_user_api.source_ingest import SourceIngestor, SourceRejected
from daon_user_api.image_document_understanding import (
    OllamaImageDocumentAdapter, TesseractImageOcr, normalize_image, validate_image_region,
)


def image_bytes(size=(120, 60), kind="PNG", orientation=None):
    buffer = io.BytesIO()
    image = Image.new("RGB", size, "white")
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    image.save(buffer, format=kind, exif=exif)
    return buffer.getvalue()


class Transport:
    def __init__(self, events):
        self.events = events
        self.remote = False
        self.vision = True
        self.model_info = {"general.architecture": "qwen3vl"}
        self.digest = "a" * 64
        self.thinking = False
        self.model_id = "qwen3-vl:latest"
        self.native_request = None

    def get_json_no_auth(self, *, url, timeout_seconds):
        self.events.append("tags")
        return {"models": [{"name": self.model_id, "digest": self.digest,
                            "remote_host": "https://cloud.example" if self.remote else None}]}

    def post_json_no_auth(self, *, url, payload, timeout_seconds):
        if url.endswith("/api/show"):
            self.events.append("show")
            return {"capabilities": (["vision"] if self.vision else ["completion"]) + (["thinking"] if self.thinking else []), "model_info": self.model_info}
        self.events.append("native")
        self.native_request = (payload, timeout_seconds)
        image = Image.open(io.BytesIO(base64.b64decode(payload["messages"][1]["images"][0])))
        assert image.size == (120, 60)
        assert "OCR" not in payload["messages"][1]["content"]
        return {"model": self.model_id, "message": {"content": json.dumps({
            "title": "QA image", "summary": "ORANGE-42", "key_facts": ["ORANGE-42"],
        })}}


class Ocr:
    def __init__(self, events, mismatch=False):
        self.events, self.mismatch = events, mismatch

    def read(self, image):
        self.events.append("ocr")
        return ([{"text": "WRONG" if self.mismatch else "ORANGE-42", "x": 5, "y": 7,
                  "width": 80, "height": 20}], "tesseract-fixture")


class ImageDocumentTests(unittest.TestCase):
    def setUp(self):
        self.context = DocumentProcessingContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1")
        self.document = StoredSourceDocument("source-1", "sv-1", "image.png", image_bytes(), "image/png")
        self.model = ResolvedModel("ollama-1", "OLLAMA", "qwen3-vl:latest", "image_understanding",
            "https://ollama.example", 0, 1, 1, "server_internal", "provider", True, None)

    def test_native_precedes_ocr_and_region_preserves_source_digest(self):
        events = []
        result = OllamaImageDocumentAdapter(Transport(events), Ocr(events)).understand(self.document, self.model, self.context)
        self.assertEqual(events, ["tags", "show", "tags", "native", "ocr"])
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.parser.regions[0]["value"], "5,7,80,20,120,60")
        self.assertEqual(result.lineage["semantic_model_digest"], "a" * 64)
        self.assertEqual(result.lineage["parser_role"], "validation_only")

    def test_thinking_model_native_payload_disables_thinking_before_strict_ocr_result(self):
        from dataclasses import replace
        events = []
        transport = Transport(events)
        transport.thinking = True
        transport.model_id = "qwen3.5:latest"
        model = replace(self.model, model_id="qwen3.5:latest")
        result = OllamaImageDocumentAdapter(transport, Ocr(events)).understand(self.document, model, self.context)
        payload, timeout = transport.native_request
        self.assertIs(payload.get("think"), False)
        self.assertEqual(payload["model"], "qwen3.5:latest")
        self.assertEqual(timeout, 90)
        self.assertEqual(payload["options"], {"temperature": 0, "num_predict": 1024})
        self.assertEqual(events, ["tags", "show", "tags", "native", "ocr"])
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.semantic.key_facts, ("ORANGE-42",))
        self.assertEqual(result.parser.regions, ({"text": "ORANGE-42", "value": "5,7,80,20,120,60"},))

    def test_nonthinking_model_omits_think_without_changing_native_budget(self):
        events = []
        transport = Transport(events)
        result = OllamaImageDocumentAdapter(transport, Ocr(events)).understand(self.document, self.model, self.context)
        payload, timeout = transport.native_request
        self.assertNotIn("think", payload)
        self.assertEqual(timeout, 90)
        self.assertEqual(payload["options"], {"temperature": 0, "num_predict": 1024})
        self.assertEqual(result.status, "ready")

    def test_empty_content_never_uses_thinking_json_as_native_semantics(self):
        events = []
        class EmptyContent(Transport):
            def post_json_no_auth(self, **kwargs):
                response = super().post_json_no_auth(**kwargs)
                if kwargs["url"].endswith("/api/chat"):
                    response["message"] = {"content": "", "thinking": response["message"]["content"]}
                return response
        transport = EmptyContent(events)
        transport.thinking = True
        with self.assertRaisesRegex(DocumentUnderstandingError, "^IMAGE_UNDERSTANDING_RESPONSE_INVALID$"):
            OllamaImageDocumentAdapter(transport, Ocr(events)).understand(self.document, self.model, self.context)
        self.assertEqual(events, ["tags", "show", "tags", "native"])

    def test_remote_nonvision_missing_metadata_or_digest_never_send_image(self):
        for field, value in (("remote", True), ("vision", False), ("model_info", {}), ("digest", "")):
            with self.subTest(field=field):
                events = []
                transport = Transport(events)
                setattr(transport, field, value)
                with self.assertRaises(DocumentUnderstandingError):
                    OllamaImageDocumentAdapter(transport, Ocr(events)).understand(self.document, self.model, self.context)
                self.assertNotIn("native", events)
                self.assertNotIn("ocr", events)

    def test_changed_model_digest_between_show_and_payload_rejects_without_inference(self):
        events = []
        class Changed(Transport):
            def post_json_no_auth(self, **kwargs):
                result = super().post_json_no_auth(**kwargs)
                self.digest = "b" * 64
                return result
        with self.assertRaisesRegex(DocumentUnderstandingError, "IMAGE_MODEL_METADATA_INVALID"):
            OllamaImageDocumentAdapter(Changed(events), Ocr(events)).understand(self.document, self.model, self.context)
        self.assertEqual(events, ["tags", "show", "tags"])

    def test_ocr_failure_is_safe_and_never_returns_ready(self):
        from unittest.mock import patch
        import subprocess
        events = []
        with patch("daon_user_api.image_document_understanding.subprocess.run", side_effect=subprocess.TimeoutExpired("tesseract", 5)):
            with self.assertRaisesRegex(DocumentUnderstandingError, "IMAGE_OCR_UNAVAILABLE"):
                OllamaImageDocumentAdapter(Transport(events)).understand(self.document, self.model, self.context)
        self.assertEqual(events, ["tags", "show", "tags", "native"])

    def test_ocr_mismatch_requires_review_without_ready(self):
        events = []
        result = OllamaImageDocumentAdapter(Transport(events), Ocr(events, True)).understand(self.document, self.model, self.context)
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.parser.regions, ())
        self.assertEqual(result.conflict, "UNDERSTANDING_PARSER_CONFLICT")

    def test_malformed_metadata_and_native_response_fail_safely(self):
        for payload in ({"capabilities": None, "model_info": {"architecture": "x"}},
                        {"capabilities": "vision", "model_info": {"architecture": "x"}},
                        None, [], {"message": None}, {"message": {"content": "[]"}, "model": "qwen3-vl:latest"}):
            with self.subTest(payload=payload):
                events = []
                class Malformed(Transport):
                    def post_json_no_auth(self, **kwargs):
                        is_metadata = kwargs["url"].endswith("/api/show")
                        if isinstance(payload, dict) and "capabilities" in payload:
                            return payload
                        if is_metadata:
                            return super().post_json_no_auth(**kwargs)
                        return payload
                with self.assertRaises(DocumentUnderstandingError):
                    OllamaImageDocumentAdapter(Malformed(events), Ocr(events)).understand(self.document, self.model, self.context)
                self.assertNotIn("ocr", events)

    def test_invalid_region_is_rejected(self):
        for region in ("-1,0,1,1,10,10", "0,0,0,1,10,10", "9,0,2,1,10,10", "0,0,1.5,1,10,10", "0,0,1,1,10"):
            with self.subTest(region=region), self.assertRaises(DocumentUnderstandingError):
                validate_image_region(region)

    def test_valid_png_jpeg_and_exif_display_basis(self):
        normalized = normalize_image(image_bytes((60, 120), "JPEG", 6), "image/jpeg")
        self.assertEqual((normalized.width, normalized.height, normalized.orientation), (120, 60, 6))
        for kind, suffix, mime in (("PNG", "png", "image/png"), ("JPEG", "jpg", "image/jpeg")):
            self.assertEqual(SourceIngestor().register_file("qa." + suffix, mime, image_bytes(kind=kind)).status, "accepted")

    def test_corrupt_or_oversized_image_is_rejected_before_storage(self):
        for content in (b"\x89PNG\r\n\x1a\ncorrupt", image_bytes((4097, 4097))):
            with self.subTest(size=len(content)), self.assertRaises(SourceRejected):
                SourceIngestor().register_file("qa.png", "image/png", content)

    def test_multiframe_image_is_rejected(self):
        buffer = io.BytesIO()
        Image.new("RGB", (10, 10), "red").save(buffer, format="PNG", save_all=True,
            append_images=[Image.new("RGB", (10, 10), "blue")])
        with self.assertRaises(DocumentUnderstandingError):
            normalize_image(buffer.getvalue(), "image/png")

    def test_service_image_consumer_resolves_only_image_and_persists_native_regions(self):
        from contextlib import contextmanager
        from daon_user_api.document_processing import DocumentProcessingService
        events = []
        outer = self
        class Repository:
            def load_source_document(self, context, source_id, **kwargs): return outer.document
            def start(self, context, version): return "run-image"
            def complete(self, context, run_id, result): events.append(("complete", run_id, result.status, result.parser.regions))
            def fail(self, *args, **kwargs): events.append("fail")
        class Resolver:
            @contextmanager
            def resolve(self, context, capability):
                events.append(capability)
                yield outer.model
        result = DocumentProcessingService(Repository(), Resolver(), None, image_adapter=OllamaImageDocumentAdapter(Transport(events), Ocr(events))).process(self.context, source_id="source-1")
        self.assertEqual(events[:6], ["image_understanding", "tags", "show", "tags", "native", "ocr"])
        self.assertEqual(events[6], ("complete", "run-image", "ready", result.parser.regions))
        self.assertNotIn("document_parsing", events)

    def test_local_ocr_uses_korean_english_bounded_tsv_and_cleans_tempfiles(self):
        from pathlib import Path
        from unittest.mock import patch
        from types import SimpleNamespace
        paths = []
        def run(argv, **kwargs):
            if argv == ["tesseract", "--version"]:
                return SimpleNamespace(stdout=b"tesseract 5.fixture\n")
            self.assertEqual(argv[3:], ["-l", "kor+eng", "tsv"])
            self.assertEqual(kwargs["timeout"], 60)
            source = Path(argv[1]); paths.append(source)
            self.assertEqual(source.read_bytes(), self.document.content)
            Path(argv[2] + ".tsv").write_text("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t5\t7\t80\t20\t90\tORANGE-42\n", encoding="utf-8")
            return SimpleNamespace()
        with patch("daon_user_api.image_document_understanding.subprocess.run", side_effect=run):
            words, version = TesseractImageOcr().read(normalize_image(self.document.content, "image/png"))
        self.assertEqual(words[0]["text"], "ORANGE-42")
        self.assertEqual(version, "tesseract 5.fixture")
        self.assertFalse(paths[0].parent.exists())
