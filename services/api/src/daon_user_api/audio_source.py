"""한정 PCM WAV와 전사·시간 근거 계약. 전사·모델 실행은 수행하지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import struct

from .source_ingest import SourceRejected

_MAX_BYTES = 25 * 1024 * 1024
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_SEGMENT = re.compile(r's:([1-9][0-9]?)\Z')


@dataclass(frozen=True)
class AudioDocument:
    source_sha256: str
    duration_ms: int
    sample_rate: int
    channels: int
    content: bytes


@dataclass(frozen=True)
class AudioTranscript:
    model_id: str
    model_digest: str
    source_sha256: str
    segments: tuple[dict, ...]


def _reject() -> None:
    raise SourceRejected('CORRUPTED_SOURCE')


def _dump(value, *, sort_keys=False) -> str:
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=sort_keys,
                            separators=(',', ':'), allow_nan=False)
        result.encode('utf-8')
        return result
    except (ValueError, TypeError, UnicodeError):
        _reject()


def read_wav(content: bytes) -> AudioDocument:
    if not isinstance(content, bytes) or not 44 <= len(content) <= _MAX_BYTES:
        _reject()
    if (content[:4] != b'RIFF' or content[8:12] != b'WAVE'
            or struct.unpack_from('<I', content, 4)[0] + 8 != len(content)):
        _reject()
    offset, count, metadata = 12, 0, 0
    fmt, data_size = None, None
    while offset < len(content):
        count += 1
        if count > 64 or offset + 8 > len(content):
            _reject()
        kind = content[offset:offset + 4]
        size = struct.unpack_from('<I', content, offset + 4)[0]
        start, end = offset + 8, offset + 8 + size
        if end + size % 2 > len(content):
            _reject()
        if kind == b'fmt ':
            if fmt is not None or size != 16:
                _reject()
            fmt = struct.unpack_from('<HHIIHH', content, start)
        elif kind == b'data':
            if fmt is None or data_size is not None:
                _reject()
            data_size = size
        elif kind in (b'JUNK', b'LIST'):
            metadata += size
            if metadata > 65536 or (kind == b'LIST' and (size < 4 or content[start:start + 4] != b'INFO')):
                _reject()
            if kind == b'LIST':
                inner = start + 4
                while inner < end:
                    count += 1
                    if count > 64 or inner + 8 > end:
                        _reject()
                    item_size = struct.unpack_from('<I', content, inner + 4)[0]
                    inner += 8 + item_size + item_size % 2
                    if inner > end:
                        _reject()
        else:
            _reject()
        offset = end + size % 2
    if fmt is None or data_size is None:
        _reject()
    encoding, channels, rate, byte_rate, alignment, bits = fmt
    if (encoding != 1 or channels not in (1, 2) or bits != 16
            or rate not in (16000, 22050, 24000, 44100, 48000)
            or alignment != channels * 2 or byte_rate != rate * alignment
            or data_size % alignment):
        _reject()
    frames = data_size // alignment
    if not rate <= frames <= 120 * rate:
        _reject()
    return AudioDocument(hashlib.sha256(content).hexdigest(), frames * 1000 // rate,
                         rate, channels, content)


def _hash_valid(value) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _transcript_payload(transcript: AudioTranscript) -> dict:
    return {'model_id': transcript.model_id, 'model_digest': transcript.model_digest,
            'source_sha256': transcript.source_sha256,
            'segments': [dict(segment) for segment in transcript.segments]}


def validate_transcript(document: AudioDocument, transcript: AudioTranscript) -> None:
    # Re-read actual bytes rather than trusting caller-supplied hash/duration.
    if (not isinstance(document, AudioDocument)
            or any(type(value) is not int for value in (document.duration_ms, document.sample_rate, document.channels))
            or document != read_wav(document.content)):
        _reject()
    if (not isinstance(transcript, AudioTranscript)
            or not isinstance(transcript.model_id, str) or not transcript.model_id.strip()
            or not _hash_valid(transcript.model_digest) or not _hash_valid(transcript.source_sha256)
            or transcript.source_sha256 != document.source_sha256
            or not isinstance(transcript.segments, tuple) or not 1 <= len(transcript.segments) <= 64):
        _reject()
    previous_end, total = 0, 0
    for ordinal, segment in enumerate(transcript.segments, 1):
        if not isinstance(segment, dict) or set(segment) != {'segment_id', 'start_ms', 'end_ms', 'text'}:
            _reject()
        start, end, text = segment['start_ms'], segment['end_ms'], segment['text']
        if (segment['segment_id'] != f's:{ordinal}'
                or type(start) is not int or type(end) is not int
                or not previous_end <= start < end <= document.duration_ms
                or not isinstance(text, str) or not text.strip() or len(text) > 2000):
            _reject()
        previous_end = end
        total += len(text)
        if total > 16000:
            _reject()
    if len(_dump(_transcript_payload(transcript), sort_keys=True).encode('utf-8')) > 65536:
        _reject()


def _validate_locator(segment_id, start_ms, end_ms, start, end) -> None:
    if not isinstance(segment_id, str):
        _reject()
    match = _SEGMENT.fullmatch(segment_id)
    if match is None or int(match[1]) > 64:
        _reject()
    if (any(type(value) is not int for value in (start_ms, end_ms, start, end))
            or not 0 <= start_ms < end_ms <= 120000 or not 0 <= start < end <= 2000):
        _reject()


def encode_audio_segment(segment_id: str, start_ms: int, end_ms: int, start: int, end: int) -> str:
    _validate_locator(segment_id, start_ms, end_ms, start, end)
    value = _dump({'segment': segment_id, 'start_ms': start_ms, 'end_ms': end_ms, 'start': start, 'end': end})
    if len(value.encode('utf-8')) > 256:
        _reject()
    return value


def decode_audio_segment(value: str) -> tuple[str, int, int, int, int]:
    try:
        if not isinstance(value, str) or len(value.encode('utf-8')) > 256:
            _reject()

        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    _reject()
                result[key] = item
            return result

        parsed = json.loads(value, object_pairs_hook=unique)
        if not isinstance(parsed, dict) or set(parsed) != {'segment', 'start_ms', 'end_ms', 'start', 'end'}:
            _reject()
        result = parsed['segment'], parsed['start_ms'], parsed['end_ms'], parsed['start'], parsed['end']
        if encode_audio_segment(*result) != value:
            _reject()
        return result
    except SourceRejected:
        raise
    except (ValueError, TypeError, UnicodeError):
        _reject()


def read_audio_transcript_view(document: AudioDocument, transcript: AudioTranscript, locator: str) -> dict:
    validate_transcript(document, transcript)
    ident, start_ms, end_ms, start, end = decode_audio_segment(locator)
    index = int(ident[2:]) - 1
    if index >= len(transcript.segments):
        _reject()
    selected = transcript.segments[index]
    if (selected['start_ms'] != start_ms or selected['end_ms'] != end_ms
            or end > len(selected['text'])):
        _reject()
    canonical = _dump(_transcript_payload(transcript), sort_keys=True).encode('utf-8')
    view = {'source_sha256': document.source_sha256,
            'transcript_sha256': hashlib.sha256(canonical).hexdigest(),
            'duration_ms': document.duration_ms, 'selected_segment_id': ident,
            'selected_start': start, 'selected_end': end,
            'segments': [dict(segment) for segment in transcript.segments[max(0, index - 1):index + 2]]}
    if len(_dump(view).encode('utf-8')) > 32768:
        _reject()
    return view
