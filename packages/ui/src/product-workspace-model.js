import { parseDocxBlock as validateConversationDocx } from "./shared-docx-block.js";
import { parsePptxBlock as validateConversationPptx } from "./shared-pptx-block.js";
import { parseAudioSegment as validateConversationAudio } from "./shared-audio-segment.js";
export { parseDocxBlock, isDocxBlockContent } from "./shared-docx-block.js";
export { parsePptxBlock, isPptxBlockContent } from "./shared-pptx-block.js";
export { parseAudioSegment, isAudioSegmentContent } from "./shared-audio-segment.js";
export const PRODUCT_WORKSPACE_STATES = Object.freeze([
  "loading",
  "empty",
  "ready",
  "error",
  "forbidden",
  "unavailable"
]);

function assertSafeError(safeError) {
  if (safeError !== null && (typeof safeError !== "string" || !/^[A-Z][A-Z0-9_]{2,63}$/u.test(safeError))) {
    throw new Error("WORKSPACE_SAFE_ERROR_INVALID");
  }
}

export function createProductWorkspaceState({ status = "loading", safeError = null } = {}) {
  if (!PRODUCT_WORKSPACE_STATES.includes(status)) throw new Error("WORKSPACE_STATE_INVALID");
  assertSafeError(safeError);
  return {
    status,
    sources: [],
    selectedSource: null,
    answer: null,
    answerIntent: null,
    studioOutputs: [],
    studioLocks: [],
    studioStatus: "loading",
    studioSafeError: null,
    conversationSafeError: null,
    safeError
  };
}

export function normalizeProductWorkspaceState(state) {
  if (!PRODUCT_WORKSPACE_STATES.includes(state?.status)) throw new Error("WORKSPACE_STATE_INVALID");
  assertSafeError(state.safeError ?? null);
  assertSafeError(state.studioSafeError ?? null);
  assertSafeError(state.conversationSafeError ?? null);
  return state;
}

export function questionFailureMessage(code) {
  if (code === "EGRESS_POLICY_DENIED") {
    return `조직 또는 Workspace의 외부 전송 정책으로 차단되었습니다. 정책 설정을 확인해 주세요. (${code})`;
  }
  if (code === "EGRESS_POLICY_UNAVAILABLE" || code === "EGRESS_POLICY_STALE") {
    return `조직 또는 Workspace의 외부 전송 정책을 확인할 수 없습니다. 관리자에게 정책 상태 확인을 요청해 주세요. (${code})`;
  }
  return `질문 실행에 실패했습니다. (${code}) 입력을 확인한 뒤 다시 제출해 주세요.`;
}

export function projectQuestionFailureState(current, error) {
  const code = typeof error?.message === "string" && /^[A-Z][A-Z0-9_]{2,63}$/u.test(error.message)
    ? error.message
    : "QUESTION_FAILED";
  return {
    ...current,
    safeError: null,
    conversationSafeError: code,
  };
}

const PRODUCT_WORKSPACE_ADAPTER_METHODS = Object.freeze([
  "listSources", "uploadPdf", "getProcessingStatus", "askQuestion",
  "citationUrl", "createReport", "listStudioOutputs"
]);

export function assertProductWorkspaceAdapter(adapter) {
  if (
    !adapter
    || PRODUCT_WORKSPACE_ADAPTER_METHODS.some((method) => typeof adapter[method] !== "function")
  ) throw new Error("WORKSPACE_ADAPTER_INVALID");
  return adapter;
}

export function canCreateGroundedReport(state) {
  return state?.status === "ready"
    && state.answer?.insufficient === false
    && typeof state.answer?.run_id === "string"
    && typeof state.answer?.run_result_id === "string"
    && Array.isArray(state.answer?.citations)
    && state.answer.citations.length > 0
    && state.answer.citations.every((citation) => (
      typeof citation?.source_id === "string" && citation.source_id.length > 0
      && typeof citation?.source_version_id === "string" && citation.source_version_id.length > 0
      && (citation.origin === "daon_knowledge" || (
        citation.origin === "raw_source"
        && citation.source_id === state.selectedSource?.sourceId
        && citation.source_version_id === state.selectedSource?.sourceVersionId
      ))
    ));
}
// Coordinates use the EXIF-transposed display basis, never encoded pixel order.
export function parseImageRegion(value) {
  if (typeof value !== "string" || !/^(0|[1-9][0-9]*)(,(0|[1-9][0-9]*)){5}$/u.test(value)) return null;
  const coordinates = value.split(",").map(Number);
  const [x, y, width, height, imageWidth, imageHeight] = coordinates;
  if (!coordinates.every(Number.isSafeInteger) || Math.min(width, height, imageWidth, imageHeight) < 1
      || imageWidth * imageHeight > 16_777_216 || x + width > imageWidth || y + height > imageHeight) return null;
  return { x, y, width, height, imageWidth, imageHeight };
}

function cellBounds(value) {
  if (typeof value !== "string" || !/^[A-Z]{1,3}[1-9][0-9]{0,6}(:[A-Z]{1,3}[1-9][0-9]{0,6})?$/u.test(value)) return null;
  const coordinate = (address) => {
    const [, letters, digits] = /^([A-Z]+)([0-9]+)$/u.exec(address);
    const col = [...letters].reduce((total, letter) => total * 26 + letter.charCodeAt(0) - 64, 0);
    return [col, Number(digits)];
  };
  const parts = value.split(":");
  const [a, b] = coordinate(parts[0]), [c, d] = coordinate(parts.at(-1));
  if (c > 16384 || d > 1048576 || c < a || d < b || (parts.length === 2 && a === c && b === d)) return null;
  return [a, b, c, d];
}

export function parseSheetCell(value) {
  if (typeof value !== "string" || new TextEncoder().encode(value).length > 512) return null;
  try {
    const item = JSON.parse(value);
    if (!item || Object.keys(item).join(",") !== "sheet,range" || typeof item.sheet !== "string"
        || [...item.sheet].length < 1 || [...item.sheet].length > 31
        || /[\x00-\x1f\x7f\[\]:*?/\\\uD800-\uDFFF]/u.test(item.sheet)
        || JSON.stringify({ sheet: item.sheet, range: item.range }) !== value) return null;
    const bounds = cellBounds(item.range);
    if (!bounds || (bounds[2] - bounds[0] + 1) * (bounds[3] - bounds[1] + 1) > 256) return null;
    return { sheet: item.sheet, range: item.range };
  } catch { return null; }
}

export function isSheetCellContent(value, locatorValue, sourceVersionId) {
  const exact = (item, keys) => item && typeof item === "object" && !Array.isArray(item)
    && Object.keys(item).length === keys.length && keys.every(key => Object.hasOwn(item, key));
  if (!exact(value, ["source_version_id", "source_sha256", "sheet_name", "sheet_visibility", "selected_range", "view_range", "cells", "merged_ranges"])
      || typeof value.source_version_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u.test(value.source_version_id)
      || typeof value.source_sha256 !== "string" || !/^[0-9a-f]{64}$/u.test(value.source_sha256)
      || !["visible", "hidden", "veryHidden"].includes(value.sheet_visibility)) return false;
  const selected = parseSheetCell(JSON.stringify({ sheet: value.sheet_name, range: value.selected_range }));
  const view = cellBounds(value.view_range);
  if (!selected || !view || (sourceVersionId !== undefined && value.source_version_id !== sourceVersionId)) return false;
  if (locatorValue !== undefined && JSON.stringify({ sheet: value.sheet_name, range: value.selected_range }) !== locatorValue) return false;
  const [a, b, c, d] = cellBounds(selected.range);
  const expected = [Math.max(1, a - 1), Math.max(1, b - 1), Math.min(16384, c + 1), Math.min(1048576, d + 1)];
  if (view.some((coordinate, index) => coordinate !== expected[index])) return false;
  const [x, y, endX, endY] = view;
  const count = (endX - x + 1) * (endY - y + 1);
  if (count > 324 || !Array.isArray(value.cells) || value.cells.length !== count) return false;
  const addresses = new Set();
  for (const cell of value.cells) {
    if (!exact(cell, ["address", "value_type", "raw_value", "display_value", "formula", "cached_value", "number_format", "hidden_row", "hidden_column"])
        || !["blank", "string", "number", "date", "boolean", "error"].includes(cell.value_type)
        || !["raw_value", "display_value", "formula", "cached_value", "number_format"].every(key => cell[key] === null || typeof cell[key] === "string")
        || typeof cell.hidden_row !== "boolean" || typeof cell.hidden_column !== "boolean"
        || addresses.has(cell.address)) return false;
    const bounds = cellBounds(cell.address);
    if (!bounds || bounds[0] !== bounds[2] || bounds[1] !== bounds[3] || bounds[0] < x || bounds[2] > endX || bounds[1] < y || bounds[3] > endY) return false;
    addresses.add(cell.address);
  }
  if (!Array.isArray(value.merged_ranges) || new Set(value.merged_ranges).size !== value.merged_ranges.length) return false;
  return value.merged_ranges.every(range => {
    const bounds = cellBounds(range);
    return bounds && bounds[0] <= endX && bounds[2] >= x && bounds[1] <= endY && bounds[3] >= y;
  });
}

// Merge only exact, server-bound request IDs. Text/time never identify a turn.
export function mergeNotebookConversationTurns(current, incoming) {
  const keys=['turn_id','question','response','state','submitted_at','model','client_request_id'];
  const safeId=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.test(value);
  const exact=(value,names)=>value&&typeof value==='object'&&!Array.isArray(value)
    && Object.keys(value).length===names.length&&names.every(key=>Object.hasOwn(value,key));
  const valid=turn=>exact(turn,keys)&&safeId(turn.turn_id)&&typeof turn.question==='string'&&turn.question.length>=1&&turn.question.length<=32768
    && ['pending','awaiting_authorization','completed','failed','uncertain','unavailable'].includes(turn.state)
    && typeof turn.submitted_at==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/u.test(turn.submitted_at)&&!Number.isNaN(Date.parse(turn.submitted_at))
    && (turn.client_request_id===null||(safeId(turn.client_request_id)&&turn.client_request_id.length>=8))
    && (turn.model===null||(exact(turn.model,['provider_code','model_id'])&&Object.values(turn.model).every(v=>typeof v==='string'&&v.length>=1&&v.length<=255&&!/[\x00-\x20\x7f]/u.test(v))))
    && (turn.response===null||exact(turn.response,['kind','text','insufficient','next_actions','citations'])
      && ['answer','system'].includes(turn.response.kind)&&typeof turn.response.text==='string'&&turn.response.text.length>=1&&turn.response.text.length<=8000
      && typeof turn.response.insufficient==='boolean'&&Array.isArray(turn.response.next_actions)&&turn.response.next_actions.length<=8&&turn.response.next_actions.every(v=>typeof v==='string'&&v.length<=255)
      && Array.isArray(turn.response.citations)&&turn.response.citations.length<=10
      && turn.response.citations.every(c=>exact(c,['citation_id','source_id','source_version_id','evidence_span_id','page','origin','context_item_id','locator'])
        && ['citation_id','source_id','source_version_id','evidence_span_id','context_item_id'].every(k=>safeId(c[k]))
        && Number.isSafeInteger(c.page)&&c.page>=1&&['raw_source','daon_knowledge'].includes(c.origin)
        && exact(c.locator,['kind','value'])&&['page','section','image_region','sheet_cell','docx_block','pptx_block','audio_segment'].includes(c.locator.kind)
        && typeof c.locator.value==='string'&&c.locator.value.length>=1&&c.locator.value.length<=255
        && (c.locator.kind!=='page'||c.locator.value===String(c.page))
        && (c.locator.kind!=='image_region'||parseImageRegion(c.locator.value)!==null)
        && (c.locator.kind!=='sheet_cell'||parseSheetCell(c.locator.value)!==null)
        && (c.locator.kind!=='docx_block'||validateConversationDocx(c.locator.value)!==null)
        && (c.locator.kind!=='pptx_block'||validateConversationPptx(c.locator.value)!==null)
        && (c.locator.kind!=='audio_segment'||validateConversationAudio(c.locator.value)!==null)))
    && (turn.state==='completed'?turn.response?.kind==='answer'
      :turn.state==='pending'?turn.response===null
      :turn.response?.kind==='system'&&turn.response.insufficient===true&&turn.response.citations.length===0)
    && (turn.state==='completed'||turn.model===null);
  if (!Array.isArray(current)||!Array.isArray(incoming)||!current.every(valid)||!incoming.every(valid)) throw new Error('NOTEBOOK_CONVERSATION_INVALID');
  for(const page of [current,incoming]){
    const requests=page.map(turn=>turn.client_request_id).filter(id=>id!==null);
    if(new Set(page.map(turn=>turn.turn_id)).size!==page.length||new Set(requests).size!==requests.length)throw new Error('NOTEBOOK_CONVERSATION_INVALID');
  }
  const merged=new Map();
  const owners=new Map();
  for(const turn of [...current,...incoming]) {
    const id=turn.client_request_id===null?`turn:${turn.turn_id}`:`request:${turn.client_request_id}`;
    if(owners.has(turn.turn_id)&&owners.get(turn.turn_id)!==id)throw new Error("NOTEBOOK_CONVERSATION_INVALID");
    owners.set(turn.turn_id,id);
    const previous=merged.get(id);
    if(previous&&previous.turn_id!==turn.turn_id&&!previous.turn_id.startsWith('pending-')) throw new Error('NOTEBOOK_CONVERSATION_INVALID');
    merged.set(id,turn);
  }
  const micros=value=>Date.parse(value)*1000+Number((/\.(\d+)Z$/u.exec(value)?.[1]??'').padEnd(6,'0').slice(3,6)||0);
  return [...merged.values()].sort((a,b)=>micros(a.submitted_at)-micros(b.submitted_at)||(a.turn_id<b.turn_id?-1:a.turn_id>b.turn_id?1:0));
}
