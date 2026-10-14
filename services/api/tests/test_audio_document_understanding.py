from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from unittest.mock import patch

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
        self.source_checks = 0
        self.deny_source_after = None
        self.transport = Transport()
        self.transport.value = {'title': '메모', 'summary': 'Code REF-DELTA-17 57000 KRW',
                                'segment_coverage': ['s:1'],
                                'key_facts': [{'segment_id': 's:1', 'text': 'Code REF-DELTA-17 57000 KRW', 'start': 0, 'end': 27}]}
        self.text = 'Code REF-DELTA-17 57000 KRW'
        outer = self
        class Asr:
            calls = 0
            response = None
            def transcribe(self, document, selection, context, *, before_send):
                before_send()
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
        return self.adapter.understand(self.document, self.asr_model, self.text_model, self.context, authorize_source=self.authorize_source)

    def authorize_source(self):
        self.source_checks += 1
        if self.deny_source_after is not None and self.source_checks >= self.deny_source_after:
            raise DocumentUnderstandingError('CURRENT_ACCESS_DENIED', status=403)

    def test_permission_revoked_between_asr_and_llm_prevents_transcript_send(self):
        self.deny_source_after = 3
        with self.assertRaisesRegex(DocumentUnderstandingError, 'CURRENT_ACCESS_DENIED'): self.run_adapter()
        self.assertEqual(self.asr.calls, 1)
        self.assertNotIn('native', self.transport.events)

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
        def timed_out(*args, **kwargs):
            kwargs['before_send']()
            raise TimeoutError()
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

    def test_hallucinated_title_is_needs_review_without_evidence(self):
        self.transport.value['title'] = '99999 USD ALPHA-999'
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
            def authorize_audio_source(self, context, document, processing_run_id):
                outer.authorize_source()
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


class AsrHttpWireTests(unittest.TestCase):
    def test_late_asr_response_is_rejected_even_if_socket_returns_success(self):
        from daon_user_api.audio_document_understanding import LocalBaseAsrTranscriptionPort
        document = read_wav(wav_bytes())
        metadata = {'model_id': 'faster-whisper-base',
            'model_digest': 'd01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9',
            'revision': 'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66',
            'capability': 'speech_to_text', 'runtime': 'cpu-int8-local'}
        response = {k: metadata[k] for k in ('model_id', 'model_digest')}
        response.update(source_sha256=document.source_sha256,
            segments=[{'segment_id': 's:1', 'start_ms': 0, 'end_ms': 1000, 'text': 'fixture'}])
        selection = ResolvedModel('asr', 'CUSTOM', 'faster-whisper-base', 'speech_to_text',
            'http://asr-base:8080', 0, 1, 1, 'server_internal', 'local_runtime', False, None)
        with patch.object(LocalBaseAsrTranscriptionPort, '_request', side_effect=[metadata, response]), \
                patch('daon_user_api.audio_document_understanding.time.monotonic', side_effect=[0, 0, 0, 0, 125]):
            with self.assertRaises(DocumentUnderstandingError) as error:
                LocalBaseAsrTranscriptionPort().transcribe(document, selection, None, before_send=lambda: None)
            self.assertTrue(error.exception.retryable)

    def test_audio_text_transport_never_uses_environment_proxy(self):
        import os
        import urllib.request
        from daon_user_api.document_understanding_adapter import UrlLibDocumentUnderstandingTransport
        captured = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'{}'
        def open_no_network(opener, request, **kwargs):
            captured.extend(h.proxies for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler))
            return Response()
        with patch.dict(os.environ, {'http_proxy': 'http://unapproved-proxy.invalid'}, clear=True), patch.object(urllib.request.OpenerDirector, 'open', open_no_network):
            # The test becomes the dedicated internal constructor once provided.
            UrlLibDocumentUnderstandingTransport(disable_environment_proxy=True).post_json_no_auth(
                url='http://internal.example/api/chat', payload={'private': 'synthetic'}, timeout_seconds=90)
        self.assertFalse(any(captured))
    def test_invalid_stored_lineage_is_safe_mismatch(self):
        from daon_user_api.audio_document_understanding import read_canonical_audio_transcript
        for value in (None, [], 'invalid'):
            with self.subTest(value=value), self.assertRaisesRegex(DocumentUnderstandingError, 'UNDERSTANDING_RESULT_MISMATCH'):
                read_canonical_audio_transcript(value)

    def test_actual_http_wire_bytes_metadata_and_redirect_boundary(self):
        from daon_user_api.audio_document_understanding import LocalBaseAsrTranscriptionPort
        raw = wav_bytes();document = read_wav(raw);calls = []
        metadata = {'model_id': 'faster-whisper-base',
            'model_digest': 'd01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9',
            'revision': 'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66', 'capability': 'speech_to_text', 'runtime': 'cpu-int8-local'}
        class Handler(BaseHTTPRequestHandler):
            redirect = False
            def log_message(self, *args): pass
            def reply(self, value):
                body = json.dumps(value).encode()
                self.send_response(200);self.send_header('Content-Type', 'application/json');self.send_header('Content-Length', str(len(body)));self.end_headers();self.wfile.write(body)
            def do_GET(self):
                calls.append(self.path)
                if self.redirect:
                    self.send_response(302);self.send_header('Location', '/unapproved');self.send_header('Content-Length', '0');self.end_headers()
                else: self.reply(metadata)
            def do_POST(self):
                content = self.rfile.read(int(self.headers['Content-Length']))
                calls.append((self.path, self.headers.get('Content-Type'), content))
                # Synthetic HTTP response only: never represents actual speech recognition.
                self.reply({**{k: metadata[k] for k in ('model_id', 'model_digest')}, 'source_sha256': document.source_sha256,
                    'segments': [{'segment_id': 's:1', 'start_ms': 0, 'end_ms': 1000, 'text': 'HTTP contract fixture'}]})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        selection = ResolvedModel('asr', 'CUSTOM', 'faster-whisper-base', 'speech_to_text', base, 0, 1, 1, 'server_internal', 'provider', False, None)
        context = DocumentProcessingContext('tenant-a', 'workspace-a', 'actor-a', 'trace-a', 'policy-v1')
        try:
            with patch('daon_user_api.audio_document_understanding._ASR_BASE', base):
                transcript = LocalBaseAsrTranscriptionPort().transcribe(document, selection, context, before_send=lambda: None)
                self.assertEqual(calls, ['/v1/metadata', ('/v1/transcribe', 'audio/wav', raw)])
                self.assertEqual(transcript.source_sha256, document.source_sha256)
                self.assertEqual(transcript.model_digest, metadata['model_digest'])
                calls.clear();Handler.redirect = True
                with self.assertRaises(DocumentUnderstandingError): LocalBaseAsrTranscriptionPort().transcribe(document, selection, context, before_send=lambda: None)
                self.assertEqual(calls, ['/v1/metadata'])
                calls.clear()
                with self.assertRaises(DocumentUnderstandingError):
                    LocalBaseAsrTranscriptionPort().transcribe(document, replace(selection, base_url=base + '/other'), context, before_send=lambda: None)
                self.assertEqual(calls, [])
        finally: server.shutdown();server.server_close();thread.join(2)


def ready_audio_result():
    fixture = AudioUnderstandingTests()
    fixture.setUp()
    fixture.document = replace(fixture.document, source_id='source-cp3', source_version_id='source-version-cp3')
    fixture.context = DocumentProcessingContext('tenant-cp3', 'workspace-cp3', 'actor-cp3', 'trace-cp3', 'policy-v1')
    return fixture.run_adapter()


if __name__ == '__main__': unittest.main()
