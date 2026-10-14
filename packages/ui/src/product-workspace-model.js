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

export function projectQuestionFailureState(current, error) {
  const code = typeof error?.message === "string" && /^[A-Z][A-Z0-9_]{2,63}$/u.test(error.message)
    ? error.message
    : "QUESTION_FAILED";
  return {
    ...current,
    safeError: null,
    conversationSafeError: code,
    answer: null,
    answerIntent: null,
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
