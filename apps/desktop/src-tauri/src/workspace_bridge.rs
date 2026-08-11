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
}

impl WorkspaceOperation {
    pub fn names_for_contract() -> [&'static str; 7] {
        [
            "list_sources",
            "upload_pdf",
            "processing_status",
            "ask_question",
            "citation_content",
            "create_report",
            "list_studio_outputs",
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
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceUploadPdfInput {
    pub workspace_id: String,
    pub filename: String,
    pub mime_type: String,
    pub bytes: Vec<u8>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceProcessingStatusInput {
    pub workspace_id: String,
    pub processing_run_id: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceAskQuestionInput {
    pub workspace_id: String,
    pub source_id: String,
    pub source_version_id: String,
    pub question: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceCitationContentInput {
    pub workspace_id: String,
    pub citation_id: String,
    pub page: u32,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceCreateReportInput {
    pub workspace_id: String,
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
}

impl WorkspaceCitation {
    fn valid(&self) -> bool {
        valid_id(&self.citation_id)
            && valid_id(&self.source_id)
            && valid_id(&self.source_version_id)
            && valid_id(&self.evidence_span_id)
            && self.page >= 1
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
pub async fn workspace_list_sources(
    input: WorkspaceListSourcesInput,
    session: tauri::State<'_, NativeSessionRuntime>,
) -> Result<Vec<WorkspaceSource>, WorkspaceError> {
    if !valid_id(&input.workspace_id) {
        return Err(map_error("INVALID_REQUEST"));
    }
    let data: SourceListData = parse_json(
        execute(
            &session,
            NativeWorkspaceOperation::ListSources {
                workspace_id: input.workspace_id.clone(),
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
    if !valid_id(&input.workspace_id)
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
    if !valid_id(&input.workspace_id) || !valid_id(&input.processing_run_id) {
        return Err(map_error("INVALID_REQUEST"));
    }
    let data: WorkspaceProcessingStatus = parse_json(
        execute(
            &session,
            NativeWorkspaceOperation::ProcessingStatus {
                workspace_id: input.workspace_id.clone(),
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
    if !valid_id(&input.workspace_id)
        || !valid_id(&input.source_id)
        || !valid_id(&input.source_version_id)
        || !safe_text(input.question.trim(), 2_000)
    {
        return Err(map_error("INVALID_REQUEST"));
    }
    let workspace_id = input.workspace_id.clone();
    let body = serde_json::to_vec(&serde_json::json!({"source_id": input.source_id, "source_version_id": input.source_version_id, "question": input.question.trim()})).map_err(|_| map_error("INVALID_REQUEST"))?;
    let key =
        workspace_idempotency_key(&format!("question:{workspace_id}"), &body).map_err(map_error)?;
    let data: WorkspaceQuestionResult = parse_json(
        execute(
            &session,
            NativeWorkspaceOperation::AskQuestion {
                workspace_id: workspace_id.clone(),
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
    if !valid_id(&input.workspace_id) || !valid_id(&input.citation_id) || input.page < 1 {
        return Err(map_error("INVALID_REQUEST"));
    }
    let response = execute(
        &session,
        NativeWorkspaceOperation::CitationContent {
            workspace_id: input.workspace_id,
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
    if !valid_id(&input.workspace_id)
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
    let body = serde_json::to_vec(&serde_json::json!({"source_id": input.source_id, "source_version_id": input.source_version_id, "run_id": input.run_id, "run_result_id": input.run_result_id, "title": input.title, "purpose": input.purpose})).map_err(|_| map_error("INVALID_REQUEST"))?;
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
    if !valid_id(&input.workspace_id) {
        return Err(map_error("INVALID_REQUEST"));
    }
    let data: StudioListData = parse_json(
        execute(
            &session,
            NativeWorkspaceOperation::ListStudioOutputs {
                workspace_id: input.workspace_id.clone(),
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
                    NativeWorkspaceOperation::ListSources { .. } => "list_sources",
                    NativeWorkspaceOperation::UploadPdf { .. } => "upload_pdf",
                    NativeWorkspaceOperation::ProcessingStatus { .. } => "processing_status",
                    NativeWorkspaceOperation::AskQuestion { .. } => "ask_question",
                    NativeWorkspaceOperation::CitationContent { .. } => "citation_content",
                    NativeWorkspaceOperation::CreateReport { .. } => "create_report",
                    NativeWorkspaceOperation::ListStudioOutputs { .. } => "list_studio_outputs",
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
}
