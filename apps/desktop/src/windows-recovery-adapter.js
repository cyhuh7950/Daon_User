const CLOUD_COMMANDS = Object.freeze({
  createBackup: "recovery_cloud_create_backup",
  listBackups: "recovery_cloud_list_backups",
  getBackup: "recovery_cloud_get_backup",
  previewRestore: "recovery_cloud_preview_restore",
  getRestore: "recovery_cloud_get_restore",
  executeRestore: "recovery_cloud_execute_restore",
  cancelRestore: "recovery_cloud_cancel_restore"
});

const LOCAL_COMMANDS = Object.freeze({
  startRecoveryScan: "recovery_local_start_scan",
  getRecoveryJob: "recovery_local_get_job",
  repairRecoveryJob: "recovery_local_repair_job"
});

const CLOUD_SAFE_ERROR_CODES = new Set([
  "AUTHENTICATION_REQUIRED", "FORBIDDEN", "CURRENT_ACCESS_DENIED", "STEP_UP_REQUIRED",
  "INVALID_REQUEST", "RESOURCE_UNAVAILABLE", "CONFLICT", "NOT_FOUND",
  "RESTORE_DESTINATION_NOT_ALLOWED", "PRECONDITION_FAILED", "CLOUD_RECOVERY_INPUT_INVALID",
  "CLOUD_RECOVERY_RESPONSE_REJECTED", "CLOUD_RECOVERY_REQUEST_FAILED"
]);
const LOCAL_SAFE_ERROR_CODES = new Set([
  "LOCAL_SERVICE_UNAVAILABLE", "LOCAL_COMMAND_NOT_ALLOWED", "LOCAL_RECOVERY_INPUT_INVALID",
  "LOCAL_RECOVERY_RESPONSE_REJECTED", "LOCAL_RECOVERY_REQUEST_FAILED"
]);

const BACKUP_FIELDS = [
  "backup_id", "tenant_id", "workspace_id", "state", "version", "trigger", "created_at",
  "verified_at", "schema_revision", "retention_watermark", "manifest_digest", "object_count",
  "transitions"
];
const RESTORE_FIELDS = [
  "request_id", "backup_id", "tenant_id", "workspace_id", "state", "version", "preview",
  "transitions", "verification_digest"
];
const PREVIEW_FIELDS = [
  "version", "included_object_ids", "excluded_object_ids", "exclusion_reasons", "destination",
  "created_at"
];
const DESTINATION_FIELDS = ["tenant_id", "workspace_id", "database_id", "bucket_id"];
const LOCAL_JOB_FIELDS = [
  "job_id", "version", "state", "target_id", "journal_present", "recorded_at",
  "previous_version", "integrity"
];

function nativeInvoke() {
  if (typeof window === "undefined") return null;
  return window.__TAURI_INTERNALS__?.invoke ?? null;
}

function failure(code, retryable = false, traceId = null) {
  const error = new Error(code);
  error.code = code;
  error.retryable = retryable;
  if (traceId) error.traceId = traceId;
  return error;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasExactFields(value, fields) {
  if (!isPlainObject(value)) return false;
  const keys = Object.keys(value).sort();
  return keys.length === fields.length && keys.every((key, index) => key === [...fields].sort()[index]);
}

function safeString(value, max = 512) {
  return typeof value === "string" && value.length > 0 && value.length <= max && !/[\u0000-\u001f\u007f]/u.test(value);
}

function requireString(value, max = 256, code = "INVALID_REQUEST") {
  if (!safeString(value, max)) throw failure(code);
  return value;
}

function requireObject(value, fields, code = "INVALID_REQUEST") {
  if (!hasExactFields(value, fields)) throw failure(code);
  return value;
}

function safeError(error, allowedCodes, fallback) {
  if (!hasExactFields(error, ["code", "trace_id", "retryable"])
    || !allowedCodes.has(error.code)
    || typeof error.retryable !== "boolean"
    || typeof error.trace_id !== "string"
    || !/^[0-9a-f]{32}$/u.test(error.trace_id)) {
    return failure(fallback);
  }
  return failure(error.code, error.retryable, error.trace_id);
}

function validStringArray(value) {
  return Array.isArray(value) && value.every((item) => safeString(item));
}

function validDestination(value) {
  return hasExactFields(value, DESTINATION_FIELDS)
    && DESTINATION_FIELDS.every((field) => safeString(value[field]));
}

function validPreview(value) {
  return hasExactFields(value, PREVIEW_FIELDS)
    && Number.isSafeInteger(value.version) && value.version > 0
    && validStringArray(value.included_object_ids) && validStringArray(value.excluded_object_ids)
    && Array.isArray(value.exclusion_reasons)
    && value.exclusion_reasons.every((item) => Array.isArray(item) && item.length === 2 && item.every((part) => safeString(part)))
    && validDestination(value.destination) && safeString(value.created_at);
}

function validBackup(value) {
  return hasExactFields(value, BACKUP_FIELDS)
    && ["backup_id", "tenant_id", "workspace_id", "state", "trigger", "created_at", "schema_revision", "retention_watermark", "manifest_digest"]
      .every((field) => safeString(value[field]))
    && (value.verified_at === null || safeString(value.verified_at))
    && Number.isSafeInteger(value.version) && value.version > 0
    && Number.isSafeInteger(value.object_count) && value.object_count >= 0
    && validStringArray(value.transitions);
}

function validRestore(value) {
  return hasExactFields(value, RESTORE_FIELDS)
    && ["request_id", "backup_id", "tenant_id", "workspace_id", "state"].every((field) => safeString(value[field]))
    && Number.isSafeInteger(value.version) && value.version > 0
    && validPreview(value.preview) && validStringArray(value.transitions)
    && (value.verification_digest === null || safeString(value.verification_digest));
}

function projectCloudResponse(value) {
  if (!hasExactFields(value, ["data", "etag"])) throw failure("CLOUD_RECOVERY_RESPONSE_REJECTED");
  const validData = Array.isArray(value.data)
    ? value.data.every(validBackup)
    : validBackup(value.data) || validRestore(value.data);
  if (!validData || !(value.etag === null || safeString(value.etag))) {
    throw failure("CLOUD_RECOVERY_RESPONSE_REJECTED");
  }
  return { payload: { data: structuredClone(value.data) }, etag: value.etag };
}

function projectLocalResponse(value) {
  if (!hasExactFields(value, LOCAL_JOB_FIELDS)
    || !["job_id", "state", "target_id", "recorded_at", "integrity"].every((field) => safeString(value[field]))
    || !Number.isSafeInteger(value.version) || value.version < 1
    || typeof value.journal_present !== "boolean"
    || !(value.previous_version === null || (Number.isSafeInteger(value.previous_version) && value.previous_version > 0))) {
    throw failure("LOCAL_RECOVERY_RESPONSE_REJECTED");
  }
  return structuredClone(value);
}

export class WindowsRecoveryAdapter {
  constructor({ invoke = nativeInvoke() } = {}) {
    this.invoke = typeof invoke === "function" ? invoke : null;
  }

  async cloud(command, input) {
    if (!this.invoke) throw failure("AUTHENTICATION_REQUIRED");
    try {
      return projectCloudResponse(await this.invoke(command, { input }));
    } catch (error) {
      if (error instanceof Error && ["INVALID_REQUEST", "CLOUD_RECOVERY_RESPONSE_REJECTED"].includes(error.code)) throw error;
      throw safeError(error, CLOUD_SAFE_ERROR_CODES, "CLOUD_RECOVERY_RESPONSE_REJECTED");
    }
  }

  async local(command, input) {
    if (!this.invoke) throw failure("LOCAL_SERVICE_UNAVAILABLE", true);
    try {
      return projectLocalResponse(await this.invoke(command, { input }));
    } catch (error) {
      if (error instanceof Error && ["LOCAL_RECOVERY_INPUT_INVALID", "LOCAL_RECOVERY_RESPONSE_REJECTED"].includes(error.code)) throw error;
      throw safeError(error, LOCAL_SAFE_ERROR_CODES, "LOCAL_RECOVERY_RESPONSE_REJECTED");
    }
  }

  async listBackups(workspaceId) {
    return this.cloud(CLOUD_COMMANDS.listBackups, { workspace_id: requireString(workspaceId, 64) });
  }

  async createBackup(input, idempotencyKey) {
    requireObject(input, ["workspace_id", "trigger", "schema_revision", "retention_watermark", "objects"]);
    if (!Array.isArray(input.objects) || input.objects.length === 0) throw failure("INVALID_REQUEST");
    return this.cloud(CLOUD_COMMANDS.createBackup, { ...structuredClone(input), idempotency_key: requireString(idempotencyKey, 128) });
  }

  async getBackup(backupId) {
    return this.cloud(CLOUD_COMMANDS.getBackup, { backup_id: requireString(backupId) });
  }

  async previewRestore(backupId, input, idempotencyKey) {
    requireObject(input, ["destination", "step_up_authorization_id"]);
    requireObject(input.destination, DESTINATION_FIELDS);
    return this.cloud(CLOUD_COMMANDS.previewRestore, {
      backup_id: requireString(backupId), destination: structuredClone(input.destination),
      step_up_authorization_id: requireString(input.step_up_authorization_id, 256),
      idempotency_key: requireString(idempotencyKey, 128)
    });
  }

  async getRestore(restoreRequestId) {
    return this.cloud(CLOUD_COMMANDS.getRestore, { restore_request_id: requireString(restoreRequestId) });
  }

  async executeRestore(restoreRequestId, input, etag, idempotencyKey) {
    requireObject(input, ["preview_version", "step_up_authorization_id"]);
    if (!Number.isSafeInteger(input.preview_version) || input.preview_version < 1) throw failure("INVALID_REQUEST");
    return this.cloud(CLOUD_COMMANDS.executeRestore, {
      restore_request_id: requireString(restoreRequestId), preview_version: input.preview_version,
      step_up_authorization_id: requireString(input.step_up_authorization_id, 256),
      idempotency_key: requireString(idempotencyKey, 128), if_match: requireString(etag, 256)
    });
  }

  async cancelRestore(restoreRequestId, etag, idempotencyKey) {
    return this.cloud(CLOUD_COMMANDS.cancelRestore, {
      restore_request_id: requireString(restoreRequestId), idempotency_key: requireString(idempotencyKey, 128),
      if_match: requireString(etag, 256)
    });
  }

  async startRecoveryScan(input) {
    requireObject(input, ["workspace_id", "target_id", "snapshot_checksum", "metadata_checksum", "actual_checksum", "journal_present"], "LOCAL_RECOVERY_INPUT_INVALID");
    if (typeof input.journal_present !== "boolean") throw failure("LOCAL_RECOVERY_INPUT_INVALID");
    return this.local(LOCAL_COMMANDS.startRecoveryScan, structuredClone(input));
  }

  async getRecoveryJob(jobId) {
    return this.local(LOCAL_COMMANDS.getRecoveryJob, { job_id: requireString(jobId, 256, "LOCAL_RECOVERY_INPUT_INVALID") });
  }

  async repairRecoveryJob(jobId, input) {
    requireObject(input, ["workspace_id", "expected_version"], "LOCAL_RECOVERY_INPUT_INVALID");
    if (!Number.isSafeInteger(input.expected_version) || input.expected_version < 1) throw failure("LOCAL_RECOVERY_INPUT_INVALID");
    return this.local(LOCAL_COMMANDS.repairRecoveryJob, {
      job_id: requireString(jobId, 256, "LOCAL_RECOVERY_INPUT_INVALID"),
      workspace_id: requireString(input.workspace_id, 64, "LOCAL_RECOVERY_INPUT_INVALID"),
      expected_version: input.expected_version
    });
  }
}
