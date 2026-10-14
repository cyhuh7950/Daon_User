from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import io
import json
import struct
import unittest
import wave

from daon_user_api.source_ingest import SourceRejected


def wav_bytes(*, rate=16000, channels=1, frames=None):
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b'\0\0' * channels * (rate if frames is None else frames))
    return output.getvalue()


def riff(*chunks):
    body = b'WAVE' + b''.join(kind + struct.pack('<I', len(data)) + data + b'\0' * (len(data) % 2) for kind, data in chunks)
    return b'RIFF' + struct.pack('<I', len(body)) + body


FMT = struct.pack('<HHIIHH', 1, 1, 16000, 32000, 2, 16)
DATA = b'\0\0' * 16000


class AudioSourceTests(unittest.TestCase):
    def setUp(self):
        # Import during execution: a missing feature is not a collection PASS.
        from daon_user_api import audio_source
        self.audio = audio_source

    def transcript(self, document, segments=None, **changes):
        values = dict(model_id='faster-whisper-base', model_digest='a' * 64,
                      source_sha256=document.source_sha256,
                      segments=tuple(segments if segments is not None else [
                          {'segment_id': 's:1', 'start_ms': 0, 'end_ms': 1000, 'text': '가😀나'}]))
        values.update(changes)
        return self.audio.AudioTranscript(**values)

    def test_pcm_wav_bounds(self):
        for rate in (16000, 22050, 24000, 44100, 48000):
            for channels in (1, 2):
                for seconds in (1, 120):
                    raw = wav_bytes(rate=rate, channels=channels, frames=rate * seconds)
                    with self.subTest(rate=rate, channels=channels, seconds=seconds):
                        document = self.audio.read_wav(raw)
                        self.assertEqual(document.duration_ms, seconds * 1000)
                        self.assertEqual((document.sample_rate, document.channels), (rate, channels))
                        self.assertEqual(document.content, raw)
                        self.assertEqual(document.source_sha256, hashlib.sha256(raw).hexdigest())
        for raw in (wav_bytes(frames=15999), wav_bytes(frames=16000 * 120 + 1),
                    wav_bytes(frames=16000 * 121), wav_bytes(rate=8000),
                    wav_bytes(channels=3), wav_bytes(frames=0), b'0' * (25 * 1024 * 1024 + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(SourceRejected):
                self.audio.read_wav(raw)
        self.assertEqual(self.audio.read_wav(wav_bytes(rate=22050, frames=22051)).duration_ms, 1000)

    def test_corrupted_chunks(self):
        valid = wav_bytes()
        altered = []
        for offset, fmt, value in ((4, '<I', len(valid)), (20, '<H', 3),
                                   (28, '<I', 31999), (32, '<H', 4), (34, '<H', 32)):
            raw = bytearray(valid); struct.pack_into(fmt, raw, offset, value); altered.append(bytes(raw))
        invalids = [valid[:-1], b'RF64' + valid[4:], b'RIFX' + valid[4:],
                    riff((b'fmt ', FMT), (b'fmt ', FMT), (b'data', DATA)),
                    riff((b'fmt ', FMT), (b'data', DATA), (b'data', DATA)),
                    riff((b'fmt ', FMT)), riff((b'data', DATA)),
                    riff((b'fmt ', FMT), (b'data', DATA + b'x')),
                    riff((b'fmt ', FMT + b'\0\0'), (b'data', DATA)),
                    riff((b'fmt ', FMT), (b'url ', b'https://external'), (b'data', DATA)),
                    riff((b'fmt ', FMT), (b'LIST', b'adtl'), (b'data', DATA)),
                    riff((b'fmt ', FMT), (b'JUNK', b'0' * 65537), (b'data', DATA)),
                    riff((b'fmt ', FMT), *((b'JUNK', b'') for _ in range(63)), (b'data', DATA)),
                    *altered]
        for raw in invalids:
            with self.subTest(size=len(raw)), self.assertRaises(SourceRejected): self.audio.read_wav(raw)
        accepted = riff((b'fmt ', FMT), (b'LIST', b'INFO'), (b'JUNK', b'x'), (b'data', DATA))
        self.assertEqual(self.audio.read_wav(accepted).duration_ms, 1000)
        self.assertEqual(self.audio.read_wav(riff((b'fmt ', FMT), (b'JUNK', b'0' * 65536), (b'data', DATA))).duration_ms, 1000)
        self.assertEqual(self.audio.read_wav(riff((b'fmt ', FMT), *((b'JUNK', b'') for _ in range(62)), (b'data', DATA))).duration_ms, 1000)

    def test_data_before_fmt_and_corrupted_info_are_rejected(self):
        for raw in (riff((b'data', DATA), (b'fmt ', FMT)),
                    riff((b'fmt ', FMT), (b'LIST', b'INFOINAM' + struct.pack('<I', 65536) + b'x'), (b'data', DATA)),
                    riff((b'fmt ', FMT), (b'LIST', b'INFOINAM' + struct.pack('<I', 1) + b'x'), (b'data', DATA)),
                    riff((b'fmt ', FMT), (b'LIST', b'INFOx'), (b'data', DATA))):
            with self.subTest(size=len(raw)), self.assertRaises(SourceRejected): self.audio.read_wav(raw)
        valid = riff((b'fmt ', FMT), (b'LIST', b'INFOINAM' + struct.pack('<I', 4) + b'QA\0\0'), (b'data', DATA))
        self.assertEqual(self.audio.read_wav(valid).duration_ms, 1000)
        self.assertEqual(self.audio.read_wav(riff((b'fmt ', FMT), (b'LIST', b'INFO' + b'INAM\0\0\0\0' * 61), (b'data', DATA))).duration_ms, 1000)
        with self.assertRaises(SourceRejected):
            self.audio.read_wav(riff((b'fmt ', FMT), (b'LIST', b'INFO' + b'INAM\0\0\0\0' * 62), (b'data', DATA)))

    def test_transcript_hash_and_exact_segments(self):
        document = self.audio.read_wav(wav_bytes())
        transcript = self.transcript(document)
        self.assertIsNone(self.audio.validate_transcript(document, transcript))
        with self.assertRaises(FrozenInstanceError): document.duration_ms = 2000
        with self.assertRaises(FrozenInstanceError): transcript.model_id = 'changed'
        for changes in (dict(source_sha256='b' * 64), dict(model_digest='A' * 64),
                        dict(model_digest='a' * 63), dict(model_id=''), dict(segments=())):
            with self.subTest(changes=changes), self.assertRaises(SourceRejected):
                self.audio.validate_transcript(document, self.transcript(document, **changes))
        with self.assertRaises(SourceRejected):
            self.audio.validate_transcript(document, replace(transcript, segments=list(transcript.segments)))
        for changes in (dict(segment_id='s:2'), dict(segment_id='s:01'), dict(start_ms=True),
                        dict(start_ms=-1), dict(start_ms=1.0), dict(end_ms=1001),
                        dict(end_ms=0), dict(text=' '), dict(text='x' * 2001),
                        dict(extra='value'), dict(text='\ud800')):
            segment = dict(transcript.segments[0]); segment.update(changes)
            with self.subTest(changes=changes), self.assertRaises(SourceRejected):
                self.audio.validate_transcript(document, self.transcript(document, [segment]))
        for changed in (replace(document, duration_ms=120000), replace(document, source_sha256='b' * 64),
                        replace(document, channels=2), replace(document, content=wav_bytes(frames=16001))):
            with self.subTest(document=changed.source_sha256), self.assertRaises(SourceRejected):
                self.audio.validate_transcript(changed, transcript)

    def test_transcript_order_overlap_counts_and_text_limits(self):
        document = self.audio.read_wav(wav_bytes(frames=16000 * 120))
        segments = [{'segment_id': f's:{n}', 'start_ms': (n - 1) * 1000,
                     'end_ms': n * 1000, 'text': 'text'} for n in range(1, 65)]
        self.audio.validate_transcript(document, self.transcript(document, segments))
        invalids = [segments + [{'segment_id': 's:65', 'start_ms': 64000, 'end_ms': 65000, 'text': 'x'}],
                    [segments[0], {**segments[1], 'segment_id': 's:1'}],
                    [segments[0], {**segments[1], 'start_ms': 999}]]
        exact = [{**segment, 'text': 'x' * 2000} for segment in segments[:8]]
        self.audio.validate_transcript(document, self.transcript(document, exact))
        invalids.append(exact + [{**segments[8], 'text': 'x'}])
        for values in invalids:
            with self.subTest(count=len(values)), self.assertRaises(SourceRejected):
                self.audio.validate_transcript(document, self.transcript(document, values))
        # Escaped controls fit the code-point limit but exceed the JSON byte limit.
        with self.assertRaises(SourceRejected):
            self.audio.validate_transcript(document, self.transcript(document, [{**s, 'text': '\x01' * 2000} for s in segments[:8]]))

    def test_audio_segment_contract(self):
        exact = '{"segment":"s:1","start_ms":0,"end_ms":1000,"start":1,"end":2}'
        self.assertEqual(self.audio.encode_audio_segment('s:1', 0, 1000, 1, 2), exact)
        self.assertEqual(self.audio.decode_audio_segment(exact), ('s:1', 0, 1000, 1, 2))
        invalids = [exact + ' ', exact.replace('"start":1', '"start":1,"start":0'),
                    exact.replace('"start":1', '"start":true'), exact.replace('"start":1', '"start":1.0'),
                    exact.replace('s:1', 's:01'), exact.replace('s:1', 's:65'),
                    exact.replace('"start_ms":0', '"start_ms":-1'),
                    exact.replace('"end_ms":1000', '"end_ms":120001'),
                    exact.replace('"end_ms":1000', '"end_ms":0'),
                    exact.replace('"end":2', '"end":1'), exact.replace('"end":2', '"end":2001'),
                    exact.replace('"end":2', '"end":2,"extra":0'), '[]', 'x' * 257]
        for value in invalids:
            with self.subTest(locator=value), self.assertRaises(SourceRejected): self.audio.decode_audio_segment(value)
        for args in (('s:1', True, 1000, 0, 1), ('s:1', 0, 1000, False, 1), ('s:0', 0, 1000, 0, 1)):
            with self.subTest(args=args), self.assertRaises(SourceRejected): self.audio.encode_audio_segment(*args)

    def test_view_binds_codepoints_time_original_hash_and_canonical_transcript(self):
        document = self.audio.read_wav(wav_bytes(frames=16000 * 4))
        segments = [{'segment_id': 's:1', 'start_ms': 0, 'end_ms': 1000, 'text': '앞'},
                    {'segment_id': 's:2', 'start_ms': 1000, 'end_ms': 2000, 'text': '가😀나'},
                    {'segment_id': 's:3', 'start_ms': 2000, 'end_ms': 3000, 'text': '뒤'},
                    {'segment_id': 's:4', 'start_ms': 3000, 'end_ms': 4000, 'text': '마지막'}]
        transcript = self.transcript(document, segments)
        locator = self.audio.encode_audio_segment('s:2', 1000, 2000, 1, 2)
        view = self.audio.read_audio_transcript_view(document, transcript, locator)
        self.assertEqual(set(view), {'source_sha256', 'transcript_sha256', 'duration_ms', 'selected_segment_id', 'selected_start', 'selected_end', 'segments'})
        self.assertEqual((view['selected_start'], view['selected_end'], view['duration_ms']), (1, 2, 4000))
        self.assertEqual(view['source_sha256'], document.source_sha256)
        self.assertEqual([s['segment_id'] for s in view['segments']], ['s:1', 's:2', 's:3'])
        self.assertEqual(view['segments'][1]['text'][1:2], '😀')
        payload = {'model_id': 'faster-whisper-base', 'model_digest': 'a' * 64,
                   'source_sha256': document.source_sha256, 'segments': segments}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(view['transcript_sha256'], expected)
        for value in (self.audio.encode_audio_segment('s:2', 999, 2000, 1, 2),
                      self.audio.encode_audio_segment('s:2', 1000, 2000, 1, 4),
                      self.audio.encode_audio_segment('s:5', 0, 1000, 0, 1)):
            with self.subTest(locator=value), self.assertRaises(SourceRejected):
                self.audio.read_audio_transcript_view(document, transcript, value)
        view['segments'][1]['text'] = 'changed by client'
        self.assertEqual(transcript.segments[1]['text'], '가😀나')

    def test_view_rejects_oversized_json_without_clipping(self):
        document = self.audio.read_wav(wav_bytes(frames=16000 * 3))
        segments = [{'segment_id': f's:{n}', 'start_ms': (n - 1) * 1000,
                     'end_ms': n * 1000, 'text': '\x01' * 2000} for n in range(1, 4)]
        transcript = self.transcript(document, segments)
        self.audio.validate_transcript(document, transcript)
        with self.assertRaises(SourceRejected):
            self.audio.read_audio_transcript_view(document, transcript, self.audio.encode_audio_segment('s:2', 1000, 2000, 0, 1))


if __name__ == '__main__': unittest.main()
