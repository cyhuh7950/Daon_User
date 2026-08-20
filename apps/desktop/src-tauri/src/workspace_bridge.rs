use crate::native_session::{
    workspace_idempotency_key, NativeSessionRuntime, NativeWorkspaceOperation,
    NativeWorkspaceResponse, MAX_WORKSPACE_PDF_BYTES,
};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use zeroize::{Zeroize, Zeroizing};

const SAFE_TEXT_MAX: usize = 20_000;

fn valid_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value
            .bytes()
            .next()
            .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
}

fn valid_filename(value: &str) -> bool {
    !value.is_empty()
        && value.chars().count() <= 255
        && value.to_ascii_lowercase().ends_with(".pdf")
        && !value.chars().any(char::is_control)
        && !value.contains(['/', '\\'])
}

fn safe_text(value: &str, maximum: usize) -> bool {
    !value.is_empty() && value.chars().count() <= maximum && !value.contains('\0')
}

fn valid_license_timestamp(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() != 20
        || bytes[4] != b'-' || bytes[7] != b'-' || bytes[10] != b'T'
        || bytes[13] != b':' || bytes[16] != b':' || bytes[19] != b'Z'
        || bytes.iter().enumerate().any(|(index, byte)| {
            !matches!(index, 4 | 7 | 10 | 13 | 16 | 19) && !byte.is_ascii_digit()
        })
    {
        return false;
    }
    let number = |start: usize, end: usize| {
        value[start..end].parse::<u32>().ok()
    };
    let (Some(year), Some(month), Some(day), Some(hour), Some(minute), Some(second)) = (
        number(0, 4), number(5, 7), number(8, 10), number(11, 13),
        number(14, 16), number(17, 19),
    ) else { return false; };
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let max_day = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => if leap { 29 } else { 28 },
        _ => return false,
    };
    (1..=max_day).contains(&day) && hour < 24 && minute < 60 && second < 60
}

fn valid_license_feature(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value.bytes().enumerate().all(|(index, byte)| {
            if index == 0 { byte.is_ascii_lowercase() }
            else { byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_' }
        })
}

fn valid_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_optional_state(value: &Option<String>) -> bool {
    value.as_deref().is_none_or(|state| safe_text(state, 64))
}

fn valid_idempotency_fingerprint(value: &str) -> bool {
    (16..=128).contains(&value.len())
        && value
            .bytes()
            .next()
            .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
}

#[derive(Debug, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceError {
    pub code: &'static str,
    pub retryable: bool,
}

impl WorkspaceError {
    fn new(code: &'static str, retryable: bool) -> Self {
        Self { code, retryable }
    }
}

fn map_error(code: &'static str) -> WorkspaceError {
    match code {
        "AUTHENTICATION_REQUIRED" => WorkspaceError::new(code, false),
        "FORBIDDEN" | "INVALID_REQUEST" | "REQUEST_TOO_LARGE" | "CONFLICT" => {
            WorkspaceError::new(code, false)
        }
        "RESOURCE_UNAVAILABLE" | "WORKSPACE_REQUEST_FAILED" => WorkspaceError::new(code, true),
        _ => WorkspaceError::new("WORKSPACE_RESPONSE_REJECTED", false),
    }
}

#[derive(Clone, Copy)]
pub enum WorkspaceOperation {
    ListSources,
    UploadPdf,
    ProcessingStatus,
    AskQuestion,
    CitationContent,
    CreateReport,
    ListStudioOutputs,
    GetLicense,
    ApplyLicense,
}

impl WorkspaceOperation {
    pub fn names_for_contract() -> [&'static str; 9] {
        [
            "list_sources",
            "upload_pdf",
            "processing_status",
            "ask_question",
            "citation_content",
            "create_report",
            "list_studio_outputs",
            "get_license",
            "apply_license",
        ]
    }
}

pub fn valid_workspace_id_for_contract(value: &str) -> bool {
    valid_id(value)
}
pub fn valid_pdf_upload_for_contract(filename: &str, mime_type: &str, bytes: &[u8]) -> bool {
    valid_filename(filename)
        && mime_type == "application/pdf"
        && (5..=MAX_WORKSPACE_PDF_BYTES).contains(&bytes.len())
        && bytes.starts_with(b"%PDF-")
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceListSourcesInput {
    pub workspace_id: String,
    pub notebook_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUploadPdfInput {
    pub workspace_id: String,
    pub notebook_id: String,
    pub filename: String,
    pub mime_type: String,
    pub bytes: Vec<u8>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceProcessingStatusInput {
    pub workspace_id: String,
    pub notebook_id: String,
    pub processing_run_id: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceAskQuestionInput {
    pub workspace_id: String,
    pub notebook_id: String,
    pub source_id: Option<String>,
    pub source_version_id: Option<String>,
    pub question: String,
}

fn general_conversation_intent(value: &str) -> bool {
    if value
        .chars()
        .any(|character| character == '\u{3000}' || ('\u{ff01}'..='\u{ff5e}').contains(&character))
    {
        return false;
    }
    let normalized = value.trim().trim_end_matches(['.', '!', '?', '。'])
        .trim().to_lowercase();
    matches!(normalized.as_str(),
        "안녕" | "안녕하세요" | "반가워" | "반갑습니다" | "고마워" | "고마워요" | "감사합니다"
        | "도움말" | "daon 사용법 알려줘" | "daon 사용법을 알려줘" | "다온 사용법 알려줘"
        | "다온 사용법을 알려줘" | "이 제품 사용법 알려줘" | "이 제품 사용법을 알려줘")
}

pub fn valid_workspace_question_input_for_contract(input: &WorkspaceAskQuestionInput) -> bool {
    if !valid_id(&input.workspace_id) || !valid_id(&input.notebook_id)
        || !safe_text(input.question.trim(), 2_000)
    {
        return false;
    }
    match (&input.source_id, &input.source_version_id) {
        (Some(source_id), Some(source_version_id)) => valid_id(source_id) && valid_id(source_version_id),
        (None, None) => general_conversation_intent(&input.question),
        _ => false,
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceCitationContentInput {
    pub workspace_id: String,
    pub notebook_id: String,
    pub citation_id: String,
    pub page: u32,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceCreateReportInput {
    pub workspace_id: String,
    pub notebook_id: String,
    pub source_id: String,
    pub source_version_id: String,
    pub run_id: String,
    pub run_result_id: String,
    pub title: String,
    pub purpose: String,
    pub request_idempotency_key: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceListStudioOutputsInput {
    pub workspace_id: String,
    pub notebook_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceGetLicenseInput {
    pub workspace_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceApplyLicenseInput {
    pub workspace_id: String,
    pub organization_id: String,
    pub document: serde_json::Value,
    pub password: String,
    pub request_idempotency_key: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NotebookWorkspaceInput {
    pub workspace_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NotebookSelectedInput {
    pub workspace_id: String,
    pub notebook_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NotebookCreateInput {
    pub workspace_id: String,
    pub title: String,
    pub description: Option<String>,
    pub request_idempotency_key: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct NotebookView {
    pub notebook_id: String,
    pub title: String,
    pub source_count: u32,
    pub output_count: u32,
    pub updated_at: String,
    pub status: String,
}

impl NotebookView {
    fn valid(&self) -> bool {
        valid_id(&self.notebook_id)
            && safe_text(&self.title, 120)
            && (20..=27).contains(&self.updated_at.len())
            && self.updated_at.ends_with('Z')
            && matches!(self.status.as_str(), "empty" | "active" | "attention")
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct NotebookSourceBinding {
    pub source_id: String,
    pub source_version_id: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct NotebookConversation {
    pub conversation_thread_id: String,
    pub answer: WorkspaceQuestionResult,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct NotebookContext {
    pub notebook_id: String,
    pub sources: Vec<NotebookSourceBinding>,
    pub knowledge_context_ids: Vec<String>,
    pub conversation_thread_ids: Vec<String>,
    pub studio_output_ids: Vec<String>,
    pub output_version_ids: Vec<String>,
    pub generation_settings_ids: Vec<String>,
    pub conversation: Option<NotebookConversation>,
}

fn valid_notebook_context(data: &NotebookContext, notebook_id: &str) -> bool {
    let ids_valid = |values: &[String]| values.len() <= 1_000 && values.iter().all(|value| valid_id(value));
    data.notebook_id == notebook_id
        && data.sources.len() <= 1_000
        && data.sources.iter().all(|source| valid_id(&source.source_id) && valid_id(&source.source_version_id))
        && ids_valid(&data.knowledge_context_ids)
        && ids_valid(&data.conversation_thread_ids)
        && ids_valid(&data.studio_output_ids)
        && ids_valid(&data.output_version_ids)
        && ids_valid(&data.generation_settings_ids)
        && match &data.conversation {
            None => data.conversation_thread_ids.is_empty(),
            Some(conversation) => data.conversation_thread_ids.first() == Some(&conversation.conversation_thread_id)
                && valid_id(&conversation.conversation_thread_id)
                && valid_id(&conversation.answer.run_id)
                && valid_id(&conversation.answer.run_result_id)
                && safe_text(&conversation.answer.answer, 8_000)
                && conversation.answer.citations.len() <= 20
                && conversation.answer.citations.iter().all(WorkspaceCitation::valid),
        }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope<T> {
    data: T,
    meta: WorkspaceMeta,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WorkspaceMeta {
    trace_id: String,
    workspace_id: String,
    #[serde(default)]
    replayed: Option<bool>,
}

impl WorkspaceMeta {
    fn valid(&self, workspace_id: &str, replay_allowed: bool, status: u16) -> bool {
        valid_id(&self.trace_id)
            && self.workspace_id == workspace_id
            && if replay_allowed {
                matches!(
                    (status, self.replayed),
                    (200, Some(true)) | (201, Some(false))
                )
            } else {
                self.replayed.is_none()
            }
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceSource {
    pub source_id: String,
    pub source_version_id: String,
    pub filename: String,
    pub source_state: String,
    pub processing_state: String,
    pub job_state: String,
}

impl WorkspaceSource {
    fn valid(&self) -> bool {
        valid_id(&self.source_id)
            && valid_id(&self.source_version_id)
            && valid_filename(&self.filename)
            && matches!(
                self.source_state.as_str(),
                "registered" | "security_check" | "processing" | "indexing" | "ready"
            )
            && safe_text(&self.processing_state, 64)
            && safe_text(&self.job_state, 64)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceListData {
    sources: Vec<WorkspaceSource>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUploadResult {
    pub source_id: String,
    pub source_version_id: String,
    pub object_id: String,
    pub digest_sha256: String,
    pub byte_size: u64,
    pub status: String,
    pub replayed: bool,
    pub processing_run_id: String,
    pub processing_state: String,
    pub job_state: Option<String>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceProcessingStatus {
    pub processing_run_id: String,
    pub source_id: String,
    pub source_version_id: String,
    pub processing_state: String,
    pub source_state: String,
    pub job_state: Option<String>,
    pub safe_error_code: Option<String>,
}

fn valid_upload_result(data: &WorkspaceUploadResult, expected_byte_size: u64) -> bool {
    valid_id(&data.source_id)
        && valid_id(&data.source_version_id)
        && valid_lower_hex(&data.object_id, 32)
        && valid_id(&data.processing_run_id)
        && valid_lower_hex(&data.digest_sha256, 64)
        && data.byte_size == expected_byte_size
        && data.status == "accepted"
        && safe_text(&data.processing_state, 64)
        && valid_optional_state(&data.job_state)
}

fn valid_processing_status(data: &WorkspaceProcessingStatus, processing_run_id: &str) -> bool {
    data.processing_run_id == processing_run_id
        && valid_id(&data.source_id)
        && valid_id(&data.source_version_id)
        && safe_text(&data.processing_state, 64)
        && matches!(
            data.source_state.as_str(),
            "registered" | "security_check" | "processing" | "indexing" | "ready"
        )
        && valid_optional_state(&data.job_state)
        && data.safe_error_code.as_deref().is_none_or(|code| {
            (3..=64).contains(&code.len())
                && code
                    .bytes()
                    .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
        })
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceCitation {
    pub citation_id: String,
    pub source_id: String,
    pub source_version_id: String,
    pub evidence_span_id: String,
    pub page: u32,
    pub origin: String,
    pub context_item_id: String,
    pub locator: WorkspaceCitationLocator,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceCitationLocator {
    pub kind: String,
    pub value: String,
}

impl WorkspaceCitation {
    fn valid(&self) -> bool {
        valid_id(&self.citation_id)
            && valid_id(&self.source_id)
            && valid_id(&self.source_version_id)
            && valid_id(&self.evidence_span_id)
            && self.page >= 1
            && matches!(self.origin.as_str(), "raw_source" | "daon_knowledge")
            && valid_id(&self.context_item_id)
            && matches!(self.locator.kind.as_str(), "page" | "section")
            && safe_text(&self.locator.value, 255)
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceQuestionResult {
    pub run_id: String,
    pub run_result_id: String,
    pub answer: String,
    pub insufficient: bool,
    pub citations: Vec<WorkspaceCitation>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceStudioOutput {
    pub studio_output_id: String,
    pub output_version_id: String,
    pub output_type: String,
    pub title: String,
    pub purpose: String,
    pub status: String,
    pub content: String,
    pub run_id: String,
    pub run_result_id: String,
    pub citations: Vec<WorkspaceCitation>,
}

impl WorkspaceStudioOutput {
    fn valid(&self) -> bool {
        valid_id(&self.studio_output_id)
            && valid_id(&self.output_version_id)
            && self.output_type == "evidence_report"
            && self.status == "draft"
            && safe_text(&self.title, 200)
            && safe_text(&self.purpose, 500)
            && safe_text(&self.content, SAFE_TEXT_MAX)
            && valid_id(&self.run_id)
            && valid_id(&self.run_result_id)
            && (1..=20).contains(&self.citations.len())
            && self.citations.iter().all(WorkspaceCitation::valid)
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LicenseResourceProjection {
    pub resource: String,
    pub limit: u64,
    pub used: u64,
    pub remaining: u64,
    pub status: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LicenseWarningProjection {
    pub code: String,
    pub action: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LicenseProjection {
    pub product: String,
    pub edition: Option<String>,
    pub license_id_hint: Option<String>,
    pub issued_at: Option<String>,
    pub expires_at: Option<String>,
    pub status: String,
    pub features: Vec<String>,
    pub resources: Vec<LicenseResourceProjection>,
    pub warning: Option<LicenseWarningProjection>,
    pub creation_allowed: bool,
    pub existing_read_allowed: bool,
    pub existing_export_allowed: bool,
    pub can_apply: bool,
}

impl LicenseProjection {
    fn valid(&self) -> bool {
        self.product == "daon-user"
            && self.edition.as_deref().is_none_or(|value| safe_text(value, 128))
            && self.license_id_hint.as_deref().is_none_or(|value| {
                value.strip_prefix('…').is_some_and(|suffix| {
                    suffix.chars().count() == 5 && suffix.chars().all(|item| !item.is_whitespace())
                })
            })
            && self.issued_at.as_deref().is_none_or(valid_license_timestamp)
            && self.expires_at.as_deref().is_none_or(valid_license_timestamp)
            && matches!(self.status.as_str(), "not_configured" | "active" | "expiring_soon" | "expired" | "limit_reached")
            && self.features.len() <= 64
            && self.features.iter().all(|value| valid_license_feature(value))
            && self.features.iter().enumerate().all(|(index, value)| {
                !self.features[..index].contains(value)
            })
            && self.resources.len() <= 64
            && self.resources.iter().all(|item| {
                matches!(item.resource.as_str(), "users" | "notebooks" | "storage_bytes" | "generation_runs" | "source_versions" | "studio_outputs")
                    && item.limit >= 1
                    && item.remaining == item.limit.saturating_sub(item.used)
                    && item.status == if item.used >= item.limit { "limit_reached" } else { "available" }
            })
            && self.warning.as_ref().is_none_or(|warning| {
                matches!(warning.code.as_str(), "LICENSE_NOT_CONFIGURED" | "LICENSE_EXPIRED" | "LICENSE_RESOURCE_LIMIT_REACHED" | "LICENSE_EXPIRES_WITHIN_30_DAYS")
                    && safe_text(&warning.action, 256)
            })
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LicenseMeta {
    trace_id: String,
    #[serde(default)]
    workspace_id: Option<String>,
    #[serde(default)]
    replayed: Option<bool>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LicenseEnvelope<T> {
    data: T,
    meta: LicenseMeta,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StepUpProjection {
    step_up_authorization: String,
    issued_at: String,
    expires_at: String,
}

fn parse_license_envelope<T: DeserializeOwned>(response: NativeWorkspaceResponse) -> Result<(T, LicenseMeta), WorkspaceError> {
    if response.content_type.as_deref().unwrap_or("").split(';').next() != Some("application/json") {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    let envelope: LicenseEnvelope<T> = serde_json::from_slice(&response.body)
        .map_err(|_| map_error("WORKSPACE_RESPONSE_REJECTED"))?;
    if !valid_id(&envelope.meta.trace_id) {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok((envelope.data, envelope.meta))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StudioListData {
    outputs: Vec<WorkspaceStudioOutput>,
}

#[derive(Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceCitationContent {
    pub content_type: &'static str,
    pub page: u32,
    pub bytes: Vec<u8>,
}

fn parse_json<T: DeserializeOwned>(
    response: NativeWorkspaceResponse,
    workspace_id: &str,
    replay_allowed: bool,
) -> Result<T, WorkspaceError> {
    if response
        .content_type
        .as_deref()
        .unwrap_or("")
        .to_ascii_lowercase()
        .split(';')
        .next()
        != Some("application/json")
    {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    let envelope: Envelope<T> = serde_json::from_slice(&response.body)
        .map_err(|_| map_error("WORKSPACE_RESPONSE_REJECTED"))?;
    if !envelope
        .meta
        .valid(workspace_id, replay_allowed, response.status)
    {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(envelope.data)
}

async fn execute(
    session: &NativeSessionRuntime,
    operation: NativeWorkspaceOperation,
) -> Result<NativeWorkspaceResponse, WorkspaceError> {
    session
        .execute_workspace_once(operation)
        .await
        .map_err(map_error)
}

#[tauri::command]
pub async fn notebook_list(
    input: NotebookWorkspaceInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<Vec<NotebookView>, WorkspaceError> {
    if !valid_id(&input.workspace_id) { return Err(map_error("INVALID_REQUEST")); }
    let data: Vec<NotebookView> = parse_json(
        execute(&session, NativeWorkspaceOperation::ListNotebooks { workspace_id: input.workspace_id.clone() }).await?,
        &input.workspace_id,
        false,
    )?;
    if data.len() > 500 || !data.iter().all(NotebookView::valid) {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(data)
}

#[tauri::command]
pub async fn notebook_create(
    input: NotebookCreateInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<NotebookView, WorkspaceError> {
    let title = input.title.trim();
    if !valid_id(&input.workspace_id) || !safe_text(title, 120)
        || input.description.as_deref().is_some_and(|value| value.chars().count() > 1_000 || value.contains('\0'))
        || !valid_idempotency_fingerprint(&input.request_idempotency_key)
    { return Err(map_error("INVALID_REQUEST")); }
    let body = serde_json::to_vec(&serde_json::json!({"title": title, "description": input.description}))
        .map_err(|_| map_error("INVALID_REQUEST"))?;
    let key = workspace_idempotency_key(
        &format!("notebook:{}:{}", input.workspace_id, input.request_idempotency_key),
        &body,
    ).map_err(map_error)?;
    let data: NotebookView = parse_json(
        execute(&session, NativeWorkspaceOperation::CreateNotebook {
            workspace_id: input.workspace_id.clone(), body: Zeroizing::new(body), idempotency_key: key,
        }).await?,
        &input.workspace_id,
        true,
    )?;
    if !data.valid() { return Err(map_error("WORKSPACE_RESPONSE_REJECTED")); }
    Ok(data)
}

#[tauri::command]
pub async fn notebook_get(
    input: NotebookSelectedInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<NotebookView, WorkspaceError> {
    if !valid_id(&input.workspace_id) || !valid_id(&input.notebook_id) { return Err(map_error("INVALID_REQUEST")); }
    let data: NotebookView = parse_json(
        execute(&session, NativeWorkspaceOperation::GetNotebook {
            workspace_id: input.workspace_id.clone(), notebook_id: input.notebook_id,
        }).await?,
        &input.workspace_id,
        false,
    )?;
    if !data.valid() { return Err(map_error("WORKSPACE_RESPONSE_REJECTED")); }
    Ok(data)
}

#[tauri::command]
pub async fn notebook_context(
    input: NotebookSelectedInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<NotebookContext, WorkspaceError> {
    if !valid_id(&input.workspace_id) || !valid_id(&input.notebook_id) { return Err(map_error("INVALID_REQUEST")); }
    let data: NotebookContext = parse_json(
        execute(&session, NativeWorkspaceOperation::GetNotebookContext {
            workspace_id: input.workspace_id.clone(), notebook_id: input.notebook_id.clone(),
        }).await?,
        &input.workspace_id,
        false,
    )?;
    if !valid_notebook_context(&data, &input.notebook_id) { return Err(map_error("WORKSPACE_RESPONSE_REJECTED")); }
    Ok(data)
}

#[tauri::command]
pub async fn workspace_list_sources(
    input: WorkspaceListSourcesInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<Vec<WorkspaceSource>, WorkspaceError> {
    if !valid_id(&input.workspace_id) || !valid_id(&input.notebook_id) {
        return Err(map_error("INVALID_REQUEST"));
    }
    let data: SourceListData = parse_json(
        execute(
            &session,
            NativeWorkspaceOperation::ListSources {
                workspace_id: input.workspace_id.clone(),
                notebook_id: input.notebook_id,
            },
        )
        .await?,
        &input.workspace_id,
        false,
    )?;
    if data.sources.len() > 1_000 || !data.sources.iter().all(WorkspaceSource::valid) {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(data.sources)
}

#[tauri::command]
pub async fn workspace_upload_pdf(
    mut input: WorkspaceUploadPdfInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<WorkspaceUploadResult, WorkspaceError> {
    if !valid_id(&input.workspace_id) || !valid_id(&input.notebook_id)
        || !valid_pdf_upload_for_contract(&input.filename, &input.mime_type, &input.bytes)
    {
        input.bytes.zeroize();
        return Err(map_error("INVALID_REQUEST"));
    }
    let key = workspace_idempotency_key(
        &format!("upload:{}:{}", input.workspace_id, input.filename),
        &input.bytes,
    )
    .map_err(map_error)?;
    let expected_byte_size = input.bytes.len() as u64;
    let operation = NativeWorkspaceOperation::UploadPdf {
        workspace_id: input.workspace_id.clone(),
        notebook_id: input.notebook_id,
        filename: input.filename,
        bytes: Zeroizing::new(std::mem::take(&mut input.bytes)),
        idempotency_key: key,
    };
    let data: WorkspaceUploadResult = parse_json(
        execute(&session, operation).await?,
        &input.workspace_id,
        false,
    )?;
    if !valid_upload_result(&data, expected_byte_size) {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(data)
}

#[tauri::command]
pub async fn workspace_processing_status(
    input: WorkspaceProcessingStatusInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<WorkspaceProcessingStatus, WorkspaceError> {
    if !valid_id(&input.workspace_id) || !valid_id(&input.notebook_id) || !valid_id(&input.processing_run_id) {
        return Err(map_error("INVALID_REQUEST"));
    }
    let data: WorkspaceProcessingStatus = parse_json(
        execute(
            &session,
            NativeWorkspaceOperation::ProcessingStatus {
                workspace_id: input.workspace_id.clone(),
                notebook_id: input.notebook_id,
                processing_run_id: input.processing_run_id.clone(),
            },
        )
        .await?,
        &input.workspace_id,
        false,
    )?;
    if !valid_processing_status(&data, &input.processing_run_id) {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(data)
}

#[tauri::command]
pub async fn workspace_ask_question(
    input: WorkspaceAskQuestionInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<WorkspaceQuestionResult, WorkspaceError> {
    if !valid_workspace_question_input_for_contract(&input) {
        return Err(map_error("INVALID_REQUEST"));
    }
    let workspace_id = input.workspace_id.clone();
    let notebook_id = input.notebook_id.clone();
    let mut body_value = serde_json::json!({"notebook_id": notebook_id, "question": input.question.trim()});
    if let (Some(source_id), Some(source_version_id)) = (input.source_id, input.source_version_id) {
        body_value["source_id"] = serde_json::Value::String(source_id);
        body_value["source_version_id"] = serde_json::Value::String(source_version_id);
    }
    let body = serde_json::to_vec(&body_value).map_err(|_| map_error("INVALID_REQUEST"))?;
    let key =
        workspace_idempotency_key(&format!("question:{workspace_id}"), &body).map_err(map_error)?;
    let data: WorkspaceQuestionResult = parse_json(
        execute(
            &session,
            NativeWorkspaceOperation::AskQuestion {
                workspace_id: workspace_id.clone(),
                notebook_id: input.notebook_id,
                body: Zeroizing::new(body),
                idempotency_key: key,
            },
        )
        .await?,
        &workspace_id,
        false,
    )?;
    if !valid_id(&data.run_id)
        || !valid_id(&data.run_result_id)
        || !safe_text(&data.answer, 8_000)
        || data.citations.len() > 10
        || !data.citations.iter().all(WorkspaceCitation::valid)
    {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(data)
}

#[tauri::command]
pub async fn workspace_citation_content(
    input: WorkspaceCitationContentInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<WorkspaceCitationContent, WorkspaceError> {
    if !valid_id(&input.workspace_id) || !valid_id(&input.notebook_id) || !valid_id(&input.citation_id) || input.page < 1 {
        return Err(map_error("INVALID_REQUEST"));
    }
    let response = execute(
        &session,
        NativeWorkspaceOperation::CitationContent {
            workspace_id: input.workspace_id,
            notebook_id: input.notebook_id,
            citation_id: input.citation_id,
        },
    )
    .await?;
    if response.status != 200
        || response.citation_page != Some(input.page)
        || response
            .content_type
            .as_deref()
            .unwrap_or("")
            .to_ascii_lowercase()
            .split(';')
            .next()
            != Some("application/pdf")
        || response.body.len() < 5
        || response.body.len() > MAX_WORKSPACE_PDF_BYTES
        || !response.body.starts_with(b"%PDF-")
    {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(WorkspaceCitationContent {
        content_type: "application/pdf",
        page: input.page,
        bytes: response.body.to_vec(),
    })
}

#[tauri::command]
pub async fn workspace_create_report(
    input: WorkspaceCreateReportInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<WorkspaceStudioOutput, WorkspaceError> {
    if !valid_id(&input.workspace_id) || !valid_id(&input.notebook_id)
        || !valid_id(&input.source_id)
        || !valid_id(&input.source_version_id)
        || !valid_id(&input.run_id)
        || !valid_id(&input.run_result_id)
        || !safe_text(&input.title, 200)
        || !safe_text(&input.purpose, 500)
        || !valid_idempotency_fingerprint(&input.request_idempotency_key)
    {
        return Err(map_error("INVALID_REQUEST"));
    }
    let workspace_id = input.workspace_id.clone();
    let notebook_id = input.notebook_id.clone();
    let body = serde_json::to_vec(&serde_json::json!({"notebook_id": notebook_id, "source_id": input.source_id, "source_version_id": input.source_version_id, "run_id": input.run_id, "run_result_id": input.run_result_id, "title": input.title, "purpose": input.purpose})).map_err(|_| map_error("INVALID_REQUEST"))?;
    let key = workspace_idempotency_key(
        &format!("studio:{workspace_id}:{}", input.request_idempotency_key),
        &body,
    )
    .map_err(map_error)?;
    let data: WorkspaceStudioOutput = parse_json(
        execute(
            &session,
            NativeWorkspaceOperation::CreateReport {
                workspace_id: workspace_id.clone(),
                notebook_id: input.notebook_id,
                body: Zeroizing::new(body),
                idempotency_key: key,
            },
        )
        .await?,
        &workspace_id,
        true,
    )?;
    if !data.valid() {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(data)
}

#[tauri::command]
pub async fn workspace_list_studio_outputs(
    input: WorkspaceListStudioOutputsInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<Vec<WorkspaceStudioOutput>, WorkspaceError> {
    if !valid_id(&input.workspace_id) || !valid_id(&input.notebook_id) {
        return Err(map_error("INVALID_REQUEST"));
    }
    let data: StudioListData = parse_json(
        execute(
            &session,
            NativeWorkspaceOperation::ListStudioOutputs {
                workspace_id: input.workspace_id.clone(),
                notebook_id: input.notebook_id,
            },
        )
        .await?,
        &input.workspace_id,
        false,
    )?;
    if data.outputs.len() > 1_000 || !data.outputs.iter().all(WorkspaceStudioOutput::valid) {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(data.outputs)
}

#[tauri::command]
pub async fn workspace_get_license(
    input: WorkspaceGetLicenseInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<LicenseProjection, WorkspaceError> {
    if !valid_id(&input.workspace_id) {
        return Err(map_error("INVALID_REQUEST"));
    }
    let (data, meta): (LicenseProjection, LicenseMeta) = parse_license_envelope(
        execute(&session, NativeWorkspaceOperation::GetLicense { workspace_id: input.workspace_id.clone() }).await?,
    )?;
    if meta.workspace_id.as_deref() != Some(input.workspace_id.as_str()) || meta.replayed.is_some() || !data.valid() {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(data)
}

#[tauri::command]
pub async fn workspace_apply_license(
    mut input: WorkspaceApplyLicenseInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<LicenseProjection, WorkspaceError> {
    if !valid_id(&input.workspace_id)
        || !valid_id(&input.organization_id)
        || !input.document.is_object()
        || !(12..=1024).contains(&input.password.len())
        || !valid_idempotency_fingerprint(&input.request_idempotency_key)
    {
        input.password.zeroize();
        input.document = serde_json::Value::Null;
        return Err(map_error("INVALID_REQUEST"));
    }
    let workspace_id = input.workspace_id.clone();
    let organization_id = input.organization_id.clone();
    let idempotency_key = Zeroizing::new(input.request_idempotency_key);
    let mut password = Zeroizing::new(std::mem::take(&mut input.password));
    let step_up_body = serde_json::to_vec(&serde_json::json!({
        "action_group": "organization_security_or_connector_policy_change",
        "target_id": organization_id,
        "password": password.as_str(),
    })).map_err(|_| map_error("INVALID_REQUEST"))?;
    password.zeroize();
    let (step_up, step_meta): (StepUpProjection, LicenseMeta) = parse_license_envelope(
        execute(&session, NativeWorkspaceOperation::LicenseStepUp {
            workspace_id: workspace_id.clone(),
            body: Zeroizing::new(step_up_body),
            idempotency_key: Zeroizing::new(idempotency_key.to_string()),
        }).await?,
    )?;
    if step_meta.workspace_id.is_some() || step_meta.replayed.is_some()
        || !safe_text(&step_up.step_up_authorization, 512)
        || !safe_text(&step_up.issued_at, 64) || !safe_text(&step_up.expires_at, 64)
    {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    let step_up_authorization = Zeroizing::new(step_up.step_up_authorization);
    let document = std::mem::take(&mut input.document);
    let apply_body = serde_json::to_vec(&serde_json::json!({
        "document": document,
        "step_up_authorization_id": step_up_authorization.as_str(),
    })).map_err(|_| map_error("INVALID_REQUEST"))?;
    let (data, apply_meta): (LicenseProjection, LicenseMeta) = parse_license_envelope(
        execute(&session, NativeWorkspaceOperation::ApplyLicense {
            workspace_id,
            organization_id,
            body: Zeroizing::new(apply_body),
            idempotency_key: Zeroizing::new(idempotency_key.to_string()),
        }).await?,
    )?;
    if apply_meta.workspace_id.is_some() || apply_meta.replayed.is_none() || !data.valid() {
        return Err(map_error("WORKSPACE_RESPONSE_REJECTED"));
    }
    Ok(data)
}

#[cfg(all(test, feature = "contract-test"))]
mod tests {
    use super::*;
    use crate::native_session::{
        NativeIdentityClient, NativeSessionCredentials, NativeSessionError,
        NativeSessionProjection, NativeSessionVaultPort, NativeWorkspaceTransport,
    };
    use std::{
        future::Future,
        pin::Pin,
        sync::{
            atomic::{AtomicUsize, Ordering},
            Arc, Mutex,
        },
        time::Duration,
    };

    struct ContractVault {
        workspace_id: Option<&'static str>,
    }

    impl NativeSessionVaultPort for ContractVault {
        fn read(&self) -> Result<Option<NativeSessionCredentials>, NativeSessionError> {
            self.workspace_id
                .map(|workspace_id| {
                    let projection = NativeSessionProjection::new(
                        "user-1".into(),
                        "tenant-1".into(),
                        workspace_id.into(),
                        "session-1".into(),
                        "device-1".into(),
                        "2027-08-12T00:00:00Z".into(),
                    )
                    .map_err(|_| NativeSessionError::authentication_required_for_contract())?;
                    NativeSessionCredentials::new("a".repeat(48), "r".repeat(48), projection)
                        .map_err(|_| NativeSessionError::authentication_required_for_contract())
                })
                .transpose()
        }
        fn write(&self, _: &NativeSessionCredentials) -> Result<(), NativeSessionError> {
            Ok(())
        }
        fn revoke(&self) -> Result<(), NativeSessionError> {
            Ok(())
        }
    }

    struct ContractTransport {
        calls: AtomicUsize,
        kinds: Mutex<Vec<&'static str>>,
    }

    impl ContractTransport {
        fn new() -> Self {
            Self {
                calls: AtomicUsize::new(0),
                kinds: Mutex::new(Vec::new()),
            }
        }
    }

    impl NativeWorkspaceTransport for ContractTransport {
        fn execute<'a>(
            &'a self,
            access: &'a [u8],
            operation: NativeWorkspaceOperation,
        ) -> Pin<Box<dyn Future<Output = Result<NativeWorkspaceResponse, &'static str>> + Send + 'a>>
        {
            Box::pin(async move {
                assert_eq!(access, b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
                let kind = match operation {
                    NativeWorkspaceOperation::ListNotebooks { .. } => "list_notebooks",
                    NativeWorkspaceOperation::CreateNotebook { .. } => "create_notebook",
                    NativeWorkspaceOperation::GetNotebook { .. } => "get_notebook",
                    NativeWorkspaceOperation::GetNotebookContext { .. } => "get_notebook_context",
                    NativeWorkspaceOperation::ListSources { .. } => "list_sources",
                    NativeWorkspaceOperation::UploadPdf { .. } => "upload_pdf",
                    NativeWorkspaceOperation::ProcessingStatus { .. } => "processing_status",
                    NativeWorkspaceOperation::AskQuestion { .. } => "ask_question",
                    NativeWorkspaceOperation::CitationContent { .. } => "citation_content",
                    NativeWorkspaceOperation::CreateReport { .. } => "create_report",
                    NativeWorkspaceOperation::ListStudioOutputs { .. } => "list_studio_outputs",
                    NativeWorkspaceOperation::GetLicense { .. } => "get_license",
                    NativeWorkspaceOperation::LicenseStepUp { .. } => "license_step_up",
                    NativeWorkspaceOperation::ApplyLicense { .. } => "apply_license",
                };
                self.calls.fetch_add(1, Ordering::SeqCst);
                self.kinds.lock().expect("contract kinds").push(kind);
                Ok(NativeWorkspaceResponse { status: 200, content_type: Some("application/json".into()), citation_page: None, body: Zeroizing::new(br#"{"data":{"sources":[]},"meta":{"trace_id":"trace-1","workspace_id":"workspace-1"}}"#.to_vec()) })
            })
        }
    }

    fn runtime(
        workspace_id: Option<&'static str>,
        transport: Arc<ContractTransport>,
    ) -> NativeSessionRuntime {
        NativeSessionRuntime::for_workspace_contract_test(
            Arc::new(ContractVault { workspace_id }),
            Arc::new(
                NativeIdentityClient::for_contract_test(
                    "http://127.0.0.1:9",
                    Duration::from_millis(20),
                )
                .expect("identity fixture"),
            ),
            transport,
        )
    }

    fn block_on<T>(future: impl Future<Output = T>) -> T {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("runtime")
            .block_on(future)
    }

    #[test]
    fn session_absent_and_workspace_mismatch_are_network_zero() {
        for workspace_id in [None, Some("workspace-other")] {
            let transport = Arc::new(ContractTransport::new());
            let result = block_on(
                runtime(workspace_id, Arc::clone(&transport)).execute_workspace_once(
                    NativeWorkspaceOperation::ListSources {
                        workspace_id: "workspace-1".into(),
                        notebook_id: "notebook-1".into(),
                    },
                ),
            );
            assert_eq!(result.err(), Some("AUTHENTICATION_REQUIRED"));
            assert_eq!(transport.calls.load(Ordering::SeqCst), 0);
        }
    }

    #[test]
    fn authenticated_executor_keeps_access_internal_and_uses_fixed_operation() {
        let transport = Arc::new(ContractTransport::new());
        let response = block_on(
            runtime(Some("workspace-1"), Arc::clone(&transport)).execute_workspace_once(
                NativeWorkspaceOperation::ListSources {
                    workspace_id: "workspace-1".into(),
                    notebook_id: "notebook-1".into(),
                },
            ),
        )
        .expect("workspace response");
        let data: SourceListData =
            parse_json(response, "workspace-1", false).expect("strict envelope");
        assert!(data.sources.is_empty());
        assert_eq!(
            transport.kinds.lock().expect("kinds").as_slice(),
            ["list_sources"]
        );
    }

    #[test]
    fn response_projection_rejects_unknown_and_sensitive_fields() {
        for body in [
            br#"{"data":{"sources":[],"unknown":true},"meta":{"trace_id":"trace-1","workspace_id":"workspace-1"}}"#.as_slice(),
            br#"{"data":{"sources":[]},"meta":{"trace_id":"trace-1","workspace_id":"workspace-1"},"access_credential":"secret"}"#.as_slice(),
        ] {
            let response = NativeWorkspaceResponse { status: 200, content_type: Some("application/json".into()), citation_page: None, body: Zeroizing::new(body.to_vec()) };
            assert!(parse_json::<SourceListData>(response, "workspace-1", false).is_err());
        }
    }

    #[test]
    fn filename_and_unicode_lengths_match_runtime_contract() {
        assert!(valid_filename("승인 운영 지침.pdf"));
        assert!(safe_text(&"가".repeat(2_000), 2_000));
        for invalid in ["tab\tname.pdf", "line\nname.pdf", "folder/name.pdf"] {
            assert!(!valid_filename(invalid), "invalid filename: {invalid:?}");
        }
    }

    #[test]
    fn nullable_job_state_and_upload_fields_are_strict() {
        let upload = WorkspaceUploadResult {
            source_id: "source-1".into(),
            source_version_id: "version-1".into(),
            object_id: "a".repeat(32),
            digest_sha256: "b".repeat(64),
            byte_size: 5,
            status: "accepted".into(),
            replayed: false,
            processing_run_id: "run-1".into(),
            processing_state: "queued".into(),
            job_state: None,
        };
        assert!(valid_upload_result(&upload, 5));
        assert!(!valid_upload_result(&upload, 6));
        let invalid_object = WorkspaceUploadResult {
            object_id: "A".repeat(32),
            ..upload
        };
        assert!(!valid_upload_result(&invalid_object, 5));

        let status = WorkspaceProcessingStatus {
            processing_run_id: "run-1".into(),
            source_id: "source-1".into(),
            source_version_id: "version-1".into(),
            processing_state: "accepted".into(),
            source_state: "registered".into(),
            job_state: None,
            safe_error_code: None,
        };
        assert!(valid_processing_status(&status, "run-1"));
    }

    #[test]
    fn create_report_status_and_replay_flag_are_bound() {
        let body = |replayed| {
            format!(
            "{{\"data\":{{\"sources\":[]}},\"meta\":{{\"trace_id\":\"trace-1\",\"workspace_id\":\"workspace-1\",\"replayed\":{replayed}}}}}"
        )
        };
        for (status, replayed, accepted) in [
            (200, true, true),
            (201, false, true),
            (200, false, false),
            (201, true, false),
        ] {
            let response = NativeWorkspaceResponse {
                status,
                content_type: Some("application/json".into()),
                citation_page: None,
                body: Zeroizing::new(body(replayed).into_bytes()),
            };
            assert_eq!(
                parse_json::<SourceListData>(response, "workspace-1", true).is_ok(),
                accepted
            );
        }
    }

    #[test]
    fn license_projection_matches_openapi_enums_hints_timestamps_and_resource_invariants() {
        let valid = LicenseProjection {
            product: "daon-user".into(),
            edition: Some("enterprise".into()),
            license_id_hint: Some("…1-001".into()),
            issued_at: Some("2026-08-14T08:00:00Z".into()),
            expires_at: Some("2027-08-15T08:00:00Z".into()),
            status: "active".into(),
            features: vec!["citation".into()],
            resources: vec![LicenseResourceProjection {
                resource: "generation_runs".into(), limit: 100, used: 2,
                remaining: 98, status: "available".into(),
            }],
            warning: None,
            creation_allowed: true,
            existing_read_allowed: true,
            existing_export_allowed: true,
            can_apply: true,
        };
        assert!(valid.valid());

        let mut invalid_hint = valid.clone();
        invalid_hint.license_id_hint = Some("not-masked".into());
        assert!(!invalid_hint.valid());
        let mut invalid_timestamp = valid.clone();
        invalid_timestamp.issued_at = Some("2026/08/14".into());
        assert!(!invalid_timestamp.valid());
        let mut invalid_feature = valid.clone();
        invalid_feature.features.push("citation".into());
        assert!(!invalid_feature.valid());
        let mut invalid_resource = valid.clone();
        invalid_resource.resources[0].resource = "internal_cost".into();
        assert!(!invalid_resource.valid());
        let mut invalid_status = valid.clone();
        invalid_status.resources[0].status = "warning".into();
        assert!(!invalid_status.valid());
        let mut invalid_remaining = valid.clone();
        invalid_remaining.resources[0].remaining = 99;
        assert!(!invalid_remaining.valid());
        let mut invalid_warning = valid;
        invalid_warning.warning = Some(LicenseWarningProjection {
            code: "INTERNAL_DECISION".into(), action: "unsafe".into(),
        });
        assert!(!invalid_warning.valid());
    }
}
