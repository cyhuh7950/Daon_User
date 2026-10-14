from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import unittest
from contextlib import contextmanager

from daon_user_api.audio_source import AudioTranscript, read_wav, read_audio_transcript_view
from daon_user_api.document_processing import DocumentProcessingContext, StoredSourceDocument
from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
from daon_user_api.workspace_model_defaults import ResolvedModel
from test_audio_source import wav_bytes
from test_docx_document_understanding import Transport


class AudioUnderstandingTests(unittest.TestCase):
    def setUp(self):
        from daon_user_api.audio_document_understanding import AudioDocumentAdapter
        self.adapter_type = AudioDocumentAdapter
        self.context = DocumentProcessingContext('tenant-a', 'workspace-a', 'actor-a', 'trace-a', 'policy-v1')
        self.document = StoredSourceDocument('src-a', 'sv-a', 'qa.wav', wav_bytes(), 'audio/wav')
        self.asr_model = ResolvedModel('asr', 'CUSTOM', 'faster-whisper-base', 'speech_to_text', 'http://asr-base:8080', 0, 1, 1, 'server_internal', 'provider', False, None)
        self.text_model = ResolvedModel('text', 'OLLAMA', 'fixture-model', 'text_generation', 'https://models.example.com', 0, 1, 1, 'server_internal', 'provider', True, None)
        self.events = []
        self.transport = Transport()
        self.transport.value = {'title': '메모', 'summary': 'Code REF-DELTA-17 57000 KRW',
                                'segment_coverage': ['s:1'],
                                'key_facts': [{'segment_id': 's:1', 'text': 'Code REF-DELTA-17 57000 KRW', 'start': 0, 'end': 27}]}
        self.text = 'Code REF-DELTA-17 57000 KRW'
        outer = self
        class Asr:
            calls = 0
            response = None
            def transcribe(self, document, selection, context):
                self.calls += 1;outer.events.append('asr')
                self.response = self.response or AudioTranscript(selection.model_id, 'b' * 64, document.source_sha256,
                    ({'segment_id': 's:1', 'start_ms': 0, 'end_ms': 1000, 'text': outer.text},))
                return self.response
        class Guard:
            denied = False
            def authorize(self, context, selections):
                outer.events.append('guard:' + selections[0].capability)
                if self.denied: raise DocumentUnderstandingError('EGRESS_POLICY_DENIED', status=403)
        self.asr = Asr();self.guard = Guard()
        self.adapter = self.adapter_type(self.asr, self.transport, egress_guard=self.guard)

    def run_adapter(self):
        return self.adapter.understand(self.document, self.asr_model, self.text_model, self.context)

    def test_asr_then_semantics_then_original_transcript_validation(self):
        self.assertEqual(len(self.text), 27)
        result = self.run_adapter()
        self.assertEqual(result.status, 'ready')
        self.assertEqual(self.events, ['guard:speech_to_text', 'asr', 'guard:text_generation'])
        self.assertEqual(result.semantic.key_facts, (self.text,))
        self.assertEqual((result.parser.pages, result.parser.regions, result.parser.slide_evidence), ((), (), ()))
        evidence = result.parser.audio_evidence[0]
        view = read_audio_transcript_view(read_wav(self.document.content), self.asr.response, evidence['locator_value'])
        self.assertEqual(view['segments'][0]['text'], evidence['text'])
        self.assertEqual(result.lineage['transcript_sha256'], view['transcript_sha256'])
        self.assertEqual(result.lineage['source_format'], 'wav')
        self.assertEqual(self.transport.requests[-1]['timeout_seconds'], 90)
        self.assertEqual(self.transport.requests[-1]['payload']['options']['num_predict'], 512)

    def test_bad_asr_hash_time_or_model_never_calls_llm(self):
        baseline = AudioTranscript('faster-whisper-base', 'b' * 64, read_wav(self.document.content).source_sha256,
                                   ({'segment_id': 's:1', 'start_ms': 0, 'end_ms': 1000, 'text': self.text},))
        for response in (replace(baseline, source_sha256='c' * 64), replace(baseline, model_id='other'),
                         replace(baseline, segments=()), replace(baseline, segments=({**baseline.segments[0], 'end_ms': 1001},))):
            with self.subTest(response=response.model_id):
                self.asr.response = response
                with self.assertRaises(DocumentUnderstandingError): self.run_adapter()
                self.assertEqual(self.transport.events, [])

    def test_guard_denial_calls_no_asr_and_model_failure_is_not_ready(self):
        self.guard.denied = True
        with self.assertRaisesRegex(DocumentUnderstandingError, 'EGRESS_POLICY_DENIED'): self.run_adapter()
        self.assertEqual(self.asr.calls, 0)
        self.guard.denied = False;self.transport.error = TimeoutError()
        with self.assertRaises(DocumentUnderstandingError): self.run_adapter()

    def test_asr_timeout_is_safe_retryable_failure(self):
        def timed_out(*args): raise TimeoutError()
        self.asr.transcribe = timed_out
        with self.assertRaisesRegex(DocumentUnderstandingError, 'UNDERSTANDING_PROVIDER_UNAVAILABLE') as error:
            self.run_adapter()
        self.assertTrue(error.exception.retryable)
        self.assertEqual(self.transport.events, [])

    def test_hallucinated_fact_or_currency_is_needs_review_without_evidence(self):
        baseline = deepcopy(self.transport.value)
        for change in ('fact', 'summary'):
            self.transport.value = deepcopy(baseline)
            if change == 'fact': self.transport.value['key_facts'][0]['text'] = 'USD'
            else: self.transport.value['summary'] = '99999 USD'
            result = self.run_adapter()
            self.assertEqual((result.status, result.parser.audio_evidence), ('needs_review', ()))

    def test_invalid_coverage_or_missing_semantics_is_not_ready(self):
        baseline = deepcopy(self.transport.value)
        for change in ({'segment_coverage': []}, {'segment_coverage': ['s:1', 's:1']}, {'key_facts': []}, {'extra': 0}):
            self.transport.value = {**deepcopy(baseline), **change}
            with self.assertRaises(DocumentUnderstandingError): self.run_adapter()

    def test_worker_service_selects_asr_and_text_and_completes_bound_run(self):
        from daon_user_api.document_processing import DocumentProcessingService
        from test_document_processing import RecordingRepository, AdapterFactory, Adapter
        outer = self
        class Repository(RecordingRepository):
            def load_source_document(self, context, source_id, *, processing_run_id=None):
                self.bound_run = processing_run_id
                return outer.document
        class Resolver:
            capabilities = []
            @contextmanager
            def resolve(self, context, capability):
                self.capabilities.append(capability)
                yield outer.asr_model if capability == 'speech_to_text' else outer.text_model
        repository = Repository();resolver = Resolver()
        service = DocumentProcessingService(repository, resolver, AdapterFactory(Adapter()), audio_adapter=self.adapter)
        result = service.process_existing(self.context, source_id='src-a', processing_run_id='pr-a')
        self.assertEqual(result.status, 'ready')
        self.assertEqual(resolver.capabilities, ['speech_to_text', 'text_generation'])
        self.assertEqual(repository.bound_run, 'pr-a')
        self.assertEqual(repository.events, [('complete', ('pr-a', 'ready'))])


def ready_audio_result():
    fixture = AudioUnderstandingTests()
    fixture.setUp()
    fixture.document = replace(fixture.document, source_id='source-cp3', source_version_id='source-version-cp3')
    fixture.context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
    return fixture.run_adapter()


if __name__ == '__main__': unittest.main()
