from __future__ import annotations

import unittest
from contextlib import contextmanager

from daon_user_api.document_processing import (
    DocumentProcessingContext,
    DocumentProcessingService,
    DocumentProcessingSubmissionService,
    StoredSourceDocument,
)
from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingError,
    DocumentUnderstandingResult,
    ParserValidation,
    SemanticUnderstanding,
)
from daon_user_api.workspace_model_defaults import ResolvedModel
from daon_user_api.egress_policy import (
    EgressPolicyContext, EgressPolicyError, EgressPolicyPayload,
    EgressPolicyService, ReferenceEgressPolicyRepository,
)
from daon_user_api.cloud_storage import CloudDatabaseError
try:
    from tests.test_document_understanding_adapter import provider_snapshot
except ModuleNotFoundError:
    # unittest discovery adds the test directory itself to sys.path.
    from test_document_understanding_adapter import provider_snapshot


PDF = b"%PDF-1.4\n% processing fixture\n%%EOF\n"


class ReprocessingModeSubmissionTests(unittest.TestCase):
    def test_explicit_mode_is_validated_and_forwarded_without_extending_legacy_call(self):
        class Repository:
            def __init__(self):
                self.calls = []

            def reprocess(self, context, source_id, source_version_id, **options):
                self.calls.append(options)
                return {"processing_run_id": "run-a"}

        repository = Repository()
        service = DocumentProcessingSubmissionService(repository)
        context = DocumentProcessingContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a")
        args = dict(notebook_id="notebook-a", idempotency_key="request-a", authorization_snapshot={"acl_version": 1})
        service.reprocess(context, "source-a", "version-a", **args)
        self.assertNotIn("processing_mode", repository.calls[0])
        for mode in ("auto", "local_only", "pinned"):
            with self.subTest(mode=mode):
                service.reprocess(context, "source-a", "version-a", processing_mode=mode, **args)
                self.assertEqual(repository.calls[-1]["processing_mode"], mode)
        self.assertEqual(len(repository.calls), 4)
        for invalid in (None, True, 1, "legacy_unknown", "AUTO", "", [], {}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(DocumentUnderstandingError):
                    service.reprocess(context, "source-a", "version-a", processing_mode=invalid, **args)
        self.assertEqual(len(repository.calls), 4)


class FailureEvidenceServiceTests(unittest.TestCase):
    def test_optional_sink_also_collects_unclassified_resolver_exit_without_changing_mapping(self):
        from daon_user_api.workspace_model_defaults import WorkspaceModelUnavailable
        class Resolver(ModelResolver):
            @contextmanager
            def resolve(inner, context, capability):
                with super().resolve(context, capability) as model:
                    yield model
                raise WorkspaceModelUnavailable("WORKSPACE_MODEL_DEFAULT_UNAVAILABLE", retryable=True)
        class Repository(RecordingRepository):
            def fail_with_readiness(inner, context, run_id, code, *, retryable, failure_evidence):
                inner.events.append(("readiness-fail", (run_id, code, retryable, failure_evidence)))
        repository = Repository()
        service = DocumentProcessingService(repository, Resolver(), AdapterFactory(Adapter()),
                                            egress_guard=FixtureOutboundGuard())
        context = DocumentProcessingContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1")
        with self.assertRaises(DocumentUnderstandingError) as caught:
            service.process_existing(context, source_id="source-cp3", processing_run_id="run-bound")
        self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.retryable),
                         ("WORKSPACE_MODEL_DEFAULT_UNAVAILABLE", 503, True))
        self.assertEqual(repository.events[-1], ("readiness-fail",
            ("run-bound", "WORKSPACE_MODEL_DEFAULT_UNAVAILABLE", True, None)))

    def test_optional_failure_sink_collects_unknown_without_inventing_role(self):
        error = DocumentUnderstandingError("UNDERSTANDING_PROVIDER_UNAVAILABLE", status=503, retryable=True)
        class Repository(RecordingRepository):
            def fail_with_readiness(inner, context, run_id, code, *, retryable, failure_evidence):
                inner.events.append(("readiness-fail", (run_id, code, retryable, failure_evidence)))
        repository = Repository()
        service = DocumentProcessingService(repository, ModelResolver(), AdapterFactory(Adapter(error)),
                                            egress_guard=FixtureOutboundGuard())
        context = DocumentProcessingContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1")
        with self.assertRaises(DocumentUnderstandingError) as caught:
            service.process_existing(context, source_id="source-cp3", processing_run_id="run-bound")
        self.assertIs(caught.exception, error)
        self.assertEqual(repository.events[-1], ("readiness-fail",
            ("run-bound", "UNDERSTANDING_PROVIDER_UNAVAILABLE", True, None)))

    def test_optional_failure_sink_gets_stage_evidence_without_changing_public_failure(self):
        from daon_user_api.document_understanding_adapter import DocumentFailureEvidence
        evidence = DocumentFailureEvidence("document_parsing", "parser-a", "role_runtime")
        error = DocumentUnderstandingError("UNDERSTANDING_PROVIDER_UNAVAILABLE", status=503,
                                           retryable=True, failure_evidence=evidence)
        class Repository(RecordingRepository):
            def fail_with_readiness(inner, context, run_id, code, *, retryable, failure_evidence):
                inner.events.append(("readiness-fail", (run_id, code, retryable, failure_evidence)))
        repository = Repository()
        service = DocumentProcessingService(repository, ModelResolver(), AdapterFactory(Adapter(error)),
                                            egress_guard=FixtureOutboundGuard())
        context = DocumentProcessingContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1")
        with self.assertRaises(DocumentUnderstandingError) as caught:
            service.process_existing(context, source_id="source-cp3", processing_run_id="run-bound")
        self.assertIs(caught.exception, error)
        self.assertEqual(repository.events[-1], ("readiness-fail",
            ("run-bound", "UNDERSTANDING_PROVIDER_UNAVAILABLE", True, evidence)))

    def test_single_role_adapter_failures_bind_actual_selection_not_unbound_transport_evidence(self):
        from test_document_understanding_adapter import FailureEvidenceTransportTests
        transport_error = FailureEvidenceTransportTests().upstream_error(503)
        for filename, mime, role in (("qa.txt", "text/plain", "text_generation"),
            ("qa.png", "image/png", "image_understanding"),
            ("qa.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text_generation"),
            ("qa.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text_generation"),
            ("qa.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "text_generation")):
            with self.subTest(filename=filename):
                calls = []
                class Repository(RecordingRepository):
                    def load_source_document(inner, context, source_id, *, processing_run_id=None):
                        return StoredSourceDocument(source_id, "version-a", filename, b"synthetic port input", mime)
                class Resolver:
                    @contextmanager
                    def resolve(inner, context, capability):
                        calls.append(capability)
                        model = ResolvedModel("selected-connection", "UPSTAGE", "synthetic-model", capability,
                            "https://api.upstage.ai/v1", 1, 1, 1, "external_api", "provider", True, bytearray(b"synthetic-key"))
                        try: yield model
                        finally: model.release()
                class FailingAdapter:
                    def understand(inner, *args): raise transport_error
                repository = Repository()
                adapter = FailingAdapter()
                service = DocumentProcessingService(repository, Resolver(), AdapterFactory(Adapter()),
                    egress_guard=FixtureOutboundGuard(), text_adapter=adapter, image_adapter=adapter,
                    xlsx_adapter=adapter, docx_adapter=adapter, pptx_adapter=adapter)
                context = DocumentProcessingContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1")
                with self.assertRaises(DocumentUnderstandingError) as caught:
                    service.process_existing(context, source_id="source-a", processing_run_id="run-a")
                self.assertEqual(calls, [role])
                evidence = getattr(caught.exception, "failure_evidence", None)
                self.assertIsNotNone(evidence)
                self.assertEqual((evidence.capability, evidence.dependency_connection_id, evidence.failure_kind),
                                 (role, "selected-connection", "role_runtime"))
                self.assertEqual(repository.events, [("fail", ("run-a", "UNDERSTANDING_PROVIDER_UNAVAILABLE", True))])
                self.assertIsNone(transport_error.failure_evidence.capability)

    def test_actual_resolver_entry_capability_is_preserved_with_unknown_dependency(self):
        from daon_user_api.workspace_model_defaults import WorkspaceModelUnavailable
        for failing, expected_calls in (("image_understanding", ["image_understanding"]),
                                       ("document_parsing", ["image_understanding", "document_parsing"])):
            with self.subTest(failing=failing):
                calls = []
                class Resolver(ModelResolver):
                    @contextmanager
                    def resolve(inner, context, capability):
                        calls.append(capability)
                        if capability == failing:
                            raise WorkspaceModelUnavailable("WORKSPACE_MODEL_DEFAULT_UNAVAILABLE")
                        with super().resolve(context, capability) as model:
                            yield model
                repository = RecordingRepository()
                service = DocumentProcessingService(repository, Resolver(), AdapterFactory(Adapter()), egress_guard=FixtureOutboundGuard())
                context = DocumentProcessingContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1")
                with self.assertRaises(DocumentUnderstandingError) as caught:
                    service.process_existing(context, source_id="source-cp3", processing_run_id="run-bound")
                self.assertEqual(calls, expected_calls)
                evidence = getattr(caught.exception, "failure_evidence", None)
                self.assertIsNotNone(evidence, "실 resolver 진입 역할 근거 미구현")
                self.assertEqual((evidence.capability, evidence.dependency_connection_id, evidence.failure_kind), (failing, None, "unknown"))
                self.assertEqual(repository.events[-1], ("fail", ("run-bound", "WORKSPACE_MODEL_DEFAULT_UNAVAILABLE", False)))

    def test_unclassified_error_is_not_upgraded_by_code_or_retryable_and_fail_contract_stays_legacy(self):
        error = DocumentUnderstandingError("UNDERSTANDING_PROVIDER_UNAVAILABLE", status=503, retryable=True)
        repository = RecordingRepository()
        service = DocumentProcessingService(repository, ModelResolver(), AdapterFactory(Adapter(error)), egress_guard=FixtureOutboundGuard())
        context = DocumentProcessingContext("tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1")
        with self.assertRaises(DocumentUnderstandingError) as caught:
            service.process_existing(context, source_id="source-cp3", processing_run_id="run-bound")
        self.assertIsNone(getattr(caught.exception, "failure_evidence", None))
        self.assertEqual(repository.events[-1], ("fail", ("run-bound", "UNDERSTANDING_PROVIDER_UNAVAILABLE", True)))


class RecordingRepository:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def load_source_document(self, context: DocumentProcessingContext, source_id: str, *, processing_run_id=None) -> StoredSourceDocument:
        self.events.append(("load", source_id))
        return StoredSourceDocument(source_id, "source-version-cp3", "contract.pdf", PDF)

    def start(self, context: DocumentProcessingContext, source_version_id: str) -> str:
        self.events.append(("start", source_version_id))
        return "processing-run-cp3"

    def complete(self, context: DocumentProcessingContext, processing_run_id: str, result: DocumentUnderstandingResult) -> None:
        self.events.append(("complete", (processing_run_id, result.status)))

    def fail(self, context: DocumentProcessingContext, processing_run_id: str, code: str, *, retryable: bool) -> None:
        self.events.append(("fail", (processing_run_id, code, retryable)))


class ModelResolver:
    def __init__(self) -> None:
        self.calls = 0

        self.capabilities = []

    @contextmanager
    def resolve(self, context, capability):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.capabilities.append(capability)
        semantic = capability == "image_understanding"
        model = ResolvedModel(
            connection_id="upstage-vision" if semantic else "upstage-parser",
            provider_code="UPSTAGE",
            model_id="information-extract" if semantic else "document-parse",
            capability=capability, base_url="https://api.upstage.ai/v1",
            credential_version=4 if semantic else 9,
            default_version=2 if semantic else 3, catalog_version=8,
            provider_kind="external_api", routing_owner="provider",
            daon_fallback_allowed=True,
            _credential=bytearray(b"vision-secret" if semantic else b"parser-secret"),
        )
        try:
            yield model
        finally:
            model.release()


class Adapter:
    def __init__(self, error: DocumentUnderstandingError | None = None) -> None:
        self.error = error
        self.calls = 0

    def understand(self, request, selection):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.error:
            raise self.error
        return DocumentUnderstandingResult(
            request.source_id, request.source_version_id, "ready",
            ("vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation"),
            SemanticUnderstanding("Daon", "Vision first", ("Vision first",)),
            ParserValidation("Vision first", "Vision first", "<p>Vision first</p>", (1,)),
            {"provider_code": selection.provider_code, "parser_role": "validation_only"},
        )


class AdapterFactory:
    def __init__(self, adapter: Adapter) -> None:
        self.adapter = adapter
        self.calls: list[tuple[str, str]] = []

    def create(self, selection, semantic_credential: str, parser_credential: str):  # type: ignore[no-untyped-def]
        self.calls.append((
            selection.semantic_connection_id, selection.parser_connection_id,
            selection.semantic_credential_version, selection.parser_credential_version,
            semantic_credential == "vision-secret", parser_credential == "parser-secret",
        ))
        return self.adapter


class FixtureOutboundGuard:
    """Only for fake adapter contract tests; never permits a real HTTP call."""
    def authorize(self, context, selections):
        if context.tenant_id != "tenant-cp3" or any(model.provider_code != "UPSTAGE" for model in selections):
            raise AssertionError("unexpected fake-adapter security fixture")


class DocumentProcessingServiceTests(unittest.TestCase):
    def test_bound_xlsx_uses_text_selection_and_original_egress_guard(self):
        from test_xlsx_document_understanding import Transport, original
        from daon_user_api.xlsx_document_understanding import OllamaXlsxDocumentAdapter
        class Repository(RecordingRepository):
            bound_run = None
            def load_source_document(self, context, source_id, *, processing_run_id=None):
                self.events.append(('load', source_id)); self.bound_run = processing_run_id
                return StoredSourceDocument(source_id, 'source-version-cp3', 'qa.xlsx', original(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        class Resolver:
            def __init__(self, kind): self.kind = kind; self.capabilities = []
            @contextmanager
            def resolve(self, context, capability):
                self.capabilities.append(capability)
                yield ResolvedModel('ollama-1', 'OLLAMA', 'llama3.2:3b', capability, 'https://ollama.example', 0, 1, 1, self.kind, 'provider', True, None)
        for kind in ('server_internal', 'external_api', 'unknown'):
            with self.subTest(kind=kind):
                repository = Repository(); resolver = Resolver(kind); transport = Transport(); factory = AdapterFactory(Adapter())
                service = DocumentProcessingService(repository, resolver, factory, xlsx_adapter=OllamaXlsxDocumentAdapter(transport))
                if kind == 'server_internal':
                    self.assertEqual(service.process_existing(self.context, source_id='source-cp3', processing_run_id='run-bound').status, 'ready')
                    self.assertEqual(repository.events[-1], ('complete', ('run-bound', 'ready')))
                else:
                    with self.assertRaises(DocumentUnderstandingError): service.process_existing(self.context, source_id='source-cp3', processing_run_id='run-bound')
                    self.assertEqual(transport.events, []); self.assertEqual(repository.events[-1][0], 'fail')
                self.assertEqual(repository.bound_run, 'run-bound'); self.assertEqual(resolver.capabilities, ['text_generation']); self.assertEqual(factory.calls, [])

    def setUp(self) -> None:
        self.context = DocumentProcessingContext(
            "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1",
        )

    def test_frozen_selection_processes_original_pdf_and_persists_completion(self) -> None:
        repository = RecordingRepository()
        snapshots = ModelResolver()
        adapter = Adapter()
        factory = AdapterFactory(adapter)
        service = DocumentProcessingService(repository, snapshots, factory, egress_guard=FixtureOutboundGuard())

        result = service.process(self.context, source_id="source-cp3")

        self.assertEqual(result.status, "ready")
        self.assertEqual(snapshots.calls, 2)
        self.assertEqual(snapshots.capabilities, ["image_understanding", "document_parsing"])
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(factory.calls, [(
            "upstage-vision", "upstage-parser", 4, 9, True, True,
        )])
        self.assertNotIn("vision-secret", repr(result))
        self.assertNotIn("parser-secret", repr(result))
        self.assertEqual([event[0] for event in repository.events], ["load", "start", "complete"])

    def test_retryable_semantic_failure_is_persisted_without_false_completion(self) -> None:
        repository = RecordingRepository()
        adapter = Adapter(DocumentUnderstandingError(
            "UNDERSTANDING_PROVIDER_UNAVAILABLE", status=503, retryable=True,
        ))
        service = DocumentProcessingService(
            repository, ModelResolver(), AdapterFactory(adapter), egress_guard=FixtureOutboundGuard(),
        )

        with self.assertRaisesRegex(DocumentUnderstandingError, "UNDERSTANDING_PROVIDER_UNAVAILABLE"):
            service.process(self.context, source_id="source-cp3")

        self.assertEqual([event[0] for event in repository.events], ["load", "start", "fail"])
        self.assertEqual(repository.events[-1][1], (
            "processing-run-cp3", "UNDERSTANDING_PROVIDER_UNAVAILABLE", True,
        ))

    def test_worker_processes_an_existing_run_without_creating_a_second_run(self) -> None:
        repository = RecordingRepository()
        service = DocumentProcessingService(
            repository, ModelResolver(), AdapterFactory(Adapter()), egress_guard=FixtureOutboundGuard(),
        )

        result = service.process_existing(
            self.context, source_id="source-cp3", processing_run_id="processing-run-existing",
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual([event[0] for event in repository.events], ["load", "complete"])
        self.assertEqual(repository.events[-1][1], ("processing-run-existing", "ready"))

    def test_existing_run_passes_its_binding_to_original_loader(self) -> None:
        class BoundRepository(RecordingRepository):
            bound_run_id = None

            def load_source_document(self, context, source_id, *, processing_run_id=None):
                self.bound_run_id = processing_run_id
                return super().load_source_document(context, source_id)

        repository = BoundRepository()
        service = DocumentProcessingService(repository, ModelResolver(), AdapterFactory(Adapter()), egress_guard=FixtureOutboundGuard())
        service.process_existing(self.context, source_id="source-cp3", processing_run_id="run-bound")
        self.assertEqual(repository.bound_run_id, "run-bound")

    def test_default_guard_rejects_external_before_factory_or_understanding(self) -> None:
        repository = RecordingRepository()
        adapter = Adapter()
        factory = AdapterFactory(adapter)
        service = DocumentProcessingService(repository, ModelResolver(), factory)
        with self.assertRaisesRegex(DocumentUnderstandingError, "EGRESS_POLICY_UNAVAILABLE"):
            service.process_existing(self.context, source_id="source-cp3", processing_run_id="run-bound")
        self.assertEqual(factory.calls, [])
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(repository.events, [("load", "source-cp3"), ("fail", ("run-bound", "EGRESS_POLICY_UNAVAILABLE", True))])

    def test_external_policy_denials_and_absent_approval_never_reach_adapter(self) -> None:
        from daon_user_api.document_processing import OriginalDocumentEgressGuard
        for scenario, expected_code, retryable in (
            ("deny", "EGRESS_POLICY_DENIED", False),
            ("missing", "EGRESS_POLICY_UNAVAILABLE", True),
            ("stale", "EGRESS_POLICY_STALE", True),
            ("database", "EGRESS_POLICY_UNAVAILABLE", True),
            ("allow_without_original_approval", "EGRESS_POLICY_DENIED", False),
        ):
            with self.subTest(scenario=scenario):
                policy_repository = ReferenceEgressPolicyRepository()
                policy_context = EgressPolicyContext("tenant-cp3", "tenant-cp3", "workspace-cp3", "actor-cp3", "trace-cp3", "policy-v1")
                policy = EgressPolicyPayload.deny_external() if scenario != "allow_without_original_approval" else EgressPolicyPayload(
                    "allow_approved_external", ("external_api",), ("api.upstage.ai",), "internal", 1000000, True, True, "workspace_manager",
                )
                if scenario != "missing":
                    for scope in ("organization", "workspace"):
                        policy_repository.seed(policy_context, scope_type=scope, payload=policy)
                if scenario == "stale":
                    policy_repository.corrupt_current(policy_context, "workspace", active=False)
                if scenario == "database":
                    def unavailable(*args):
                        raise CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True)
                    policy_repository.current = unavailable
                repository = RecordingRepository()
                adapter = Adapter()
                factory = AdapterFactory(adapter)
                service = DocumentProcessingService(repository, ModelResolver(), factory,
                    egress_guard=OriginalDocumentEgressGuard(EgressPolicyService(policy_repository)))
                with self.assertRaisesRegex(DocumentUnderstandingError, expected_code):
                    service.process_existing(self.context, source_id="source-cp3", processing_run_id="run-bound")
                self.assertEqual(factory.calls, [])
                self.assertEqual(adapter.calls, 0)
                self.assertEqual(repository.events, [("load", "source-cp3"), ("fail", ("run-bound", expected_code, retryable))])

    def test_both_pdf_selections_are_guarded_and_unknown_kind_is_denied(self) -> None:
        for semantic_kind, parser_kind in (
            ("external_api", "server_internal"), ("server_internal", "external_api"),
            ("unknown", "server_internal"), ("server_internal", "unknown"),
        ):
            with self.subTest(semantic=semantic_kind, parser=parser_kind):
                class MixedResolver(ModelResolver):
                    @contextmanager
                    def resolve(self, context, capability):
                        with super().resolve(context, capability) as model:
                            model.provider_kind = semantic_kind if capability == "image_understanding" else parser_kind
                            yield model
                repository = RecordingRepository()
                resolver = MixedResolver()
                adapter = Adapter()
                factory = AdapterFactory(adapter)
                service = DocumentProcessingService(repository, resolver, factory)
                with self.assertRaises(DocumentUnderstandingError):
                    service.process_existing(self.context, source_id="source-cp3", processing_run_id="run-bound")
                self.assertEqual(resolver.capabilities, ["image_understanding", "document_parsing"])
                self.assertEqual(factory.calls, [])
                self.assertEqual(adapter.calls, 0)
                self.assertEqual([event[0] for event in repository.events], ["load", "fail"])

    def test_text_preserves_existing_internal_kinds_but_denies_external_and_unknown(self) -> None:
        for kind in ("local", "server_internal", "local_runtime", "external_api", "unknown"):
            with self.subTest(kind=kind):
                class TextRepository(RecordingRepository):
                    def load_source_document(self, context, source_id, *, processing_run_id=None):
                        self.events.append(("load", source_id))
                        return StoredSourceDocument(source_id, "source-version-cp3", "notes.txt", b"Real TXT fact", "text/plain")
                class TextResolver:
                    @contextmanager
                    def resolve(self, context, capability):
                        yield ResolvedModel("ollama-public", "OLLAMA", "llama3.2:3b", capability,
                            "https://ollama.example.test", 0, 1, 1, kind, "provider", True, None)
                class TextAdapter:
                    calls = 0
                    def understand(self, document, selection, context):
                        self.calls += 1
                        return DocumentUnderstandingResult(document.source_id, document.source_version_id,
                            "ready", (), SemanticUnderstanding("notes", "Real TXT fact", ("Real TXT fact",)),
                            ParserValidation("Real TXT fact", "", "", (1,)), {"source_format": "txt"})
                repository = TextRepository()
                text_adapter = TextAdapter()
                service = DocumentProcessingService(repository, TextResolver(), AdapterFactory(Adapter()), text_adapter=text_adapter)
                if kind in {"local", "server_internal", "local_runtime"}:
                    result = service.process_existing(self.context, source_id="source-cp3", processing_run_id="run-bound")
                    self.assertEqual(result.status, "ready")
                    self.assertEqual(text_adapter.calls, 1)
                    self.assertEqual([event[0] for event in repository.events], ["load", "complete"])
                else:
                    expected = "EGRESS_POLICY_UNAVAILABLE" if kind == "external_api" else "EGRESS_POLICY_DENIED"
                    with self.assertRaisesRegex(DocumentUnderstandingError, expected):
                        service.process_existing(self.context, source_id="source-cp3", processing_run_id="run-bound")
                    self.assertEqual(text_adapter.calls, 0)
                    self.assertEqual([event[0] for event in repository.events], ["load", "fail"])

    def test_txt_uses_only_selected_text_model_and_persists_real_result(self) -> None:
        class TextRepository(RecordingRepository):
            def load_source_document(self, context, source_id, *, processing_run_id=None):  # type: ignore[no-untyped-def]
                self.events.append(("load", source_id))
                return StoredSourceDocument(source_id, "source-version-txt", "notes.txt", b"Real TXT fact", "text/plain")

        class TextResolver:
            def __init__(self):
                self.capabilities = []

            @contextmanager
            def resolve(self, context, capability):  # type: ignore[no-untyped-def]
                self.capabilities.append(capability)
                if capability != "text_generation":
                    raise AssertionError(capability)
                yield ResolvedModel(
                    "ollama-public", "OLLAMA", "llama3.2:3b", capability,
                    "http://ollama.internal:11434", 0, 1, 1, "local", "provider", True, None,
                )

        class TextAdapter:
            def understand(self, document, selection, context):  # type: ignore[no-untyped-def]
                return DocumentUnderstandingResult(
                    document.source_id, document.source_version_id, "ready", (),
                    SemanticUnderstanding("notes", "Real TXT fact", ("Real TXT fact",)),
                    ParserValidation("Real TXT fact", "Real TXT fact", "", (1,), ((1, "Real TXT fact"),)),
                    {"provider_code": selection.provider_code, "source_format": "txt"},
                )

        repository = TextRepository()
        resolver = TextResolver()
        service = DocumentProcessingService(repository, resolver, AdapterFactory(Adapter()), text_adapter=TextAdapter())
        result = service.process_existing(
            self.context, source_id="source-txt", processing_run_id="processing-run-txt",
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(resolver.capabilities, ["text_generation"])
        self.assertEqual([event[0] for event in repository.events], ["load", "complete"])

    def test_markdown_and_csv_use_text_generation_not_pdf_capabilities(self) -> None:
        for filename, mime in (("notes.md", "text/markdown"), ("notes.csv", "text/csv")):
            with self.subTest(filename=filename):
                class TextRepository(RecordingRepository):
                    def load_source_document(self, context, source_id, *, processing_run_id=None):  # type: ignore[no-untyped-def]
                        self.events.append(("load", source_id))
                        return StoredSourceDocument(source_id, "source-version-text", filename, b"Real source fact", mime)

                class TextResolver:
                    def __init__(self):
                        self.capabilities = []

                    @contextmanager
                    def resolve(self, context, capability):  # type: ignore[no-untyped-def]
                        self.capabilities.append(capability)
                        if capability != "text_generation":
                            raise AssertionError(capability)
                        yield ResolvedModel(
                            "ollama-public", "OLLAMA", "llama3.2:3b", capability,
                            "http://ollama.internal:11434", 0, 1, 1, "local", "provider", True, None,
                        )

                class TextAdapter:
                    def understand(self, document, selection, context):  # type: ignore[no-untyped-def]
                        return DocumentUnderstandingResult(
                            document.source_id, document.source_version_id, "ready", (),
                            SemanticUnderstanding("notes", "Real source fact", ("Real source fact",)),
                            ParserValidation("Real source fact", "Real source fact", "", (1,), ((1, "Real source fact"),)),
                            {"provider_code": selection.provider_code, "source_format": filename.rsplit(".", 1)[1]},
                        )

                repository = TextRepository()
                resolver = TextResolver()
                service = DocumentProcessingService(repository, resolver, AdapterFactory(Adapter()), text_adapter=TextAdapter())
                result = service.process_existing(
                    self.context, source_id="source-text", processing_run_id="processing-run-text",
                )
                self.assertEqual(result.status, "ready")
                self.assertEqual(resolver.capabilities, ["text_generation"])
                self.assertEqual([event[0] for event in repository.events], ["load", "complete"])


if __name__ == "__main__":
    unittest.main()
