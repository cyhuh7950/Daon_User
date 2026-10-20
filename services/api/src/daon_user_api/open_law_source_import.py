"""법령 전용 멱등 등록. 모델 이해가 아닌 검증 원문의 결정적 저장이다."""
from __future__ import annotations

from dataclasses import dataclass,replace
import hashlib
import re
import time

from .open_law_client import OpenLawDocument,OpenLawError,OpenLawSelection,decode_document,encode_document
from .website_source_import import WebsiteObjectPromoter,WebsiteImportError,_store_deadline


_SAFE_ERROR_STATUS={
    'OPEN_LAW_INPUT_INVALID':400,'OPEN_LAW_ACCESS_DENIED':403,'OPEN_LAW_EGRESS_BLOCKED':403,
    'OPEN_LAW_NOT_FOUND':404,'OPEN_LAW_PREVIEW_CHANGED':409,'OPEN_LAW_IMPORT_CONFLICT':409,
    'OPEN_LAW_IMPORT_IN_PROGRESS':409,'OPEN_LAW_BINDING_CONFLICT':409,'OPEN_LAW_CONTENT_TOO_LARGE':413,
    'OPEN_LAW_TEXT_UNAVAILABLE':422,'OPEN_LAW_PROTOCOL_INVALID':502,'OPEN_LAW_UPSTREAM_FAILED':502,
    'OPEN_LAW_CREDENTIAL_REQUIRED':503,'OPEN_LAW_UNAVAILABLE':503,'OPEN_LAW_BUSY':503,
    'OPEN_LAW_STORAGE_UNAVAILABLE':503,'OPEN_LAW_TIMEOUT':504,
}


class OpenLawImportError(ValueError):
    def __init__(self,code,status=503):
        if code not in _SAFE_ERROR_STATUS:code='OPEN_LAW_STORAGE_UNAVAILABLE'
        self.code,self.status=code,_SAFE_ERROR_STATUS[code]
        super().__init__(code)


@dataclass(frozen=True)
class OpenLawImportResult:
    snapshot_id:str
    source_id:str
    source_version_id:str
    source_state:str
    replayed:bool


@dataclass(frozen=True)
class OpenLawImportClaim:
    request_id:str
    fingerprint:str
    replayed:bool
    result:OpenLawImportResult|None


@dataclass(frozen=True)
class OpenLawSnapshotView:
    snapshot_id:str
    connector_id:str
    source_id:str
    source_version_id:str
    mst:str
    law_id:str
    title:str
    effective_date:str
    promulgation_date:str|None
    ministry:str|None
    article_code:str|None
    origin_uri:str
    fetched_at:str
    raw_sha256:str
    text_sha256:str
    extraction_version:str
    license_status:str


def object_id(claim,kind):return hashlib.sha256(f'{claim.request_id}|{kind}'.encode()).hexdigest()[:32]


def checked_document(document,selection):
    if (not isinstance(document,OpenLawDocument) or type(document.raw_content) is not bytes
            or type(document.text_content) is not bytes or not 0<len(document.raw_content)<=2097152
            or not 0<len(document.text_content)<=524288):raise OpenLawImportError('OPEN_LAW_PROTOCOL_INVALID',502)
    try:validated=decode_document(encode_document(document))
    except (ValueError,TypeError,UnicodeError):raise OpenLawImportError('OPEN_LAW_PROTOCOL_INVALID',502) from None
    if any(validated.law[k]!=getattr(selection,k) for k in ('mst','effective_date','article_code')):
        raise OpenLawImportError('OPEN_LAW_PROTOCOL_INVALID',502)
    return validated


class OpenLawObjectPromoter:
    """bounded Object queue/lease만 재사용한다. Source identity는 독립이다."""
    def __init__(self,queue_store,storage):self._promoter=WebsiteObjectPromoter(queue_store,storage)
    def promote(self,*args):
        try:return self._promoter.promote(*args)
        except WebsiteImportError as error:
            raise OpenLawImportError('OPEN_LAW_TIMEOUT' if error.code=='WEBSITE_FETCH_TIMEOUT' else 'OPEN_LAW_STORAGE_UNAVAILABLE',
                504 if error.code=='WEBSITE_FETCH_TIMEOUT' else 503) from None


class OpenLawSourceImportService:
    def __init__(self,repository,promoter,*,client,authorize_context,clock=None):
        self._repository,self._promoter,self._client,self._authorize=repository,promoter,client,authorize_context
        self._clock=clock or time.monotonic

    def _check(self,context,deadline):
        if self._clock()>=deadline:raise OpenLawImportError('OPEN_LAW_TIMEOUT',504)
        try:
            if self._authorize(context) is not None:raise PermissionError
        except OpenLawImportError:raise
        except Exception:raise OpenLawImportError('OPEN_LAW_ACCESS_DENIED',403) from None

    def preview(self,context,selection):
        if not isinstance(selection,OpenLawSelection):raise OpenLawImportError('OPEN_LAW_INPUT_INVALID',400)
        deadline=self._clock()+25
        self._check(context,deadline)
        try:
            value=self._client.read(context,selection)
        except OpenLawImportError:raise
        except OpenLawError as error:
            code=str(error)
            raise OpenLawImportError(code if code in _SAFE_ERROR_STATUS else 'OPEN_LAW_UPSTREAM_FAILED') from None
        except Exception:raise OpenLawImportError('OPEN_LAW_UPSTREAM_FAILED') from None
        document=checked_document(value,selection)
        self._check(context,deadline)
        return document

    def import_selection(self,context,selection,expected_raw_sha256,expected_text_sha256,idempotency_key):
        if (not isinstance(selection,OpenLawSelection) or not isinstance(idempotency_key,str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,255}',idempotency_key)
                or any(not isinstance(v,str) or not re.fullmatch(r'[0-9a-f]{64}',v) for v in (expected_raw_sha256,expected_text_sha256))):
            raise OpenLawImportError('OPEN_LAW_INPUT_INVALID',400)
        api_deadline=self._clock()+25;self._check(context,api_deadline)
        claim=self._repository.claim(context,selection,expected_raw_sha256,expected_text_sha256,idempotency_key)
        if claim.replayed:
            self._check(context,api_deadline)
            if claim.result is None:raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE')
            return replace(claim.result,replayed=True)
        token=_store_deadline.set(api_deadline);phase='fetch'
        try:
            self._check(context,api_deadline)
            document=checked_document(self._client.read(context,selection),selection)
            self._check(context,api_deadline)
            if (document.raw_sha256,document.text_sha256)!=(expected_raw_sha256,expected_text_sha256):
                raise OpenLawImportError('OPEN_LAW_PREVIEW_CHANGED',409)
            phase='store';deadline=min(api_deadline,self._clock()+12);_store_deadline.set(deadline)
            self._repository.mark_storing(context,claim)
            raw=self._promoter.promote(context,claim,'raw',document.raw_content,deadline)
            self._check(context,deadline)
            text=self._promoter.promote(context,claim,'text',document.text_content,deadline)
            self._check(context,deadline)
            return self._repository.complete(context,claim,document,raw,text)
        except Exception as error:
            if isinstance(error,OpenLawImportError):safe=error
            elif isinstance(error,OpenLawError):
                code=str(error)
                safe=OpenLawImportError(code if code in _SAFE_ERROR_STATUS else 'OPEN_LAW_UPSTREAM_FAILED')
            else:safe=OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE' if phase=='store' else 'OPEN_LAW_UPSTREAM_FAILED',503 if phase=='store' else 502)
            try:self._repository.fail(context,claim,safe.code)
            except Exception:raise OpenLawImportError('OPEN_LAW_STORAGE_UNAVAILABLE') from None
            raise safe from None
        finally:_store_deadline.reset(token)
