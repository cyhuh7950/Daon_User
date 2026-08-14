use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fmt;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpStream};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use zeroize::{Zeroize, Zeroizing};

pub const PROTOCOL_VERSION: &str = "1.1";
pub const READY_MAX_BYTES: usize = 4096;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(10);
const IO_TIMEOUT: Duration = Duration::from_secs(2);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(3);

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        result.push(DIGITS[(byte >> 4) as usize] as char);
        result.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    result
}

pub struct AppCredentials {
    app_instance_id: String,
    root_secret: [u8; 32],
    storage_root_key: [u8; 32],
    storage_root: String,
}

impl AppCredentials {
    pub fn generate() -> Result<Self, String> {
        let mut instance = [0_u8; 16];
        let mut root_secret = [0_u8; 32];
        let mut storage_root_key = [0_u8; 32];
        getrandom::fill(&mut instance).map_err(|_| "LOCAL_RANDOM_UNAVAILABLE".to_owned())?;
        getrandom::fill(&mut root_secret).map_err(|_| "LOCAL_RANDOM_UNAVAILABLE".to_owned())?;
        getrandom::fill(&mut storage_root_key)
            .map_err(|_| "LOCAL_RANDOM_UNAVAILABLE".to_owned())?;
        Ok(Self {
            app_instance_id: hex(&instance),
            root_secret,
            storage_root_key,
            storage_root: std::env::temp_dir()
                .join("daon-local-storage-test")
                .to_string_lossy()
                .into_owned(),
        })
    }

    #[cfg(windows)]
    fn generate_for_app() -> Result<Self, &'static str> {
        use crate::windows_credential::WindowsCredentialStore;

        let storage_root = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .ok_or("LOCAL_STORAGE_PATH_UNAVAILABLE")?
            .join("Daon")
            .join("User")
            .join("local-storage");
        let existing_ciphertext = storage_root.join("metadata.db").is_file();
        let secret = WindowsCredentialStore::new("DaonUser/LocalStorage/v1".to_owned())
            .load_or_create(existing_ciphertext)?;
        let mut credentials = Self::generate().map_err(|_| "LOCAL_RANDOM_UNAVAILABLE")?;
        credentials.storage_root_key = secret.bytes();
        credentials.storage_root = storage_root.to_string_lossy().into_owned();
        Ok(credentials)
    }

    #[cfg(not(windows))]
    fn generate_for_app() -> Result<Self, &'static str> {
        Self::generate().map_err(|_| "LOCAL_RANDOM_UNAVAILABLE")
    }

    pub fn app_instance_id(&self) -> &str {
        &self.app_instance_id
    }

    fn root_secret_hex(&self) -> String {
        hex(&self.root_secret)
    }

    pub fn issue_request_token(
        &self,
        capability: &str,
        command: &str,
        issued_at: u64,
    ) -> Result<String, String> {
        let authorized = matches!(
            (capability, command),
            ("runtime.read", "runtime.status.read")
                | ("runtime.read", "runtime.capabilities.read")
                | ("storage.read", "storage.status.read")
                | ("storage.read", "storage.file.get")
                | ("storage.read", "storage.vector.search")
                | ("storage.write", "storage.file.put")
                | ("storage.write", "storage.vector.put")
                | ("storage.write", "storage.lock")
                | ("recovery.write", "recovery.scan")
                | ("recovery.read", "recovery.job.read")
                | ("recovery.write", "recovery.repair")
                | ("studio.read", "studio_models_list")
                | ("studio.read", "studio_raw_sources_list")
                | ("studio.write", "studio_raw_source_import")
                | ("studio.write", "studio_provider_settings_import")
                | ("studio.write", "studio_context_prepare")
                | ("studio.write", "studio_settings_confirm")
                | ("studio.write", "studio_draft_generate")
                | ("studio.read", "studio_draft_get")
                | ("studio.write", "studio_draft_append_version")
                | ("studio.write", "studio_sync_queue")
                | ("knowledge.write", "studio_knowledge_copy_import")
                | ("knowledge.write", "studio_knowledge_copy_refresh")
                | ("sync.read", "studio_sync_state_read")
                | ("sync.write", "studio_sync_state_append")
        );
        if !authorized {
            return Err("LOCAL_COMMAND_NOT_ALLOWED".to_owned());
        }
        let expires_at = issued_at
            .checked_add(60)
            .ok_or_else(|| "LOCAL_TOKEN_TIME_INVALID".to_owned())?;
        let mut nonce = [0_u8; 32];
        getrandom::fill(&mut nonce).map_err(|_| "LOCAL_RANDOM_UNAVAILABLE".to_owned())?;
        let unsigned = format!(
            "lt1|{issued_at}|{expires_at}|{}|{capability}|{command}|{}",
            self.app_instance_id,
            hex(&nonce)
        );
        nonce.fill(0);
        let mut signature = hmac_sha256(&self.root_secret, unsigned.as_bytes());
        let token = format!("{unsigned}|{}", hex(&signature));
        signature.fill(0);
        Ok(token)
    }

    pub fn bootstrap_json(&self) -> String {
        serde_json::json!({
            "protocol_version": PROTOCOL_VERSION,
            "app_instance_id": self.app_instance_id,
            "root_secret": self.root_secret_hex(),
            "storage_root_key": hex(&self.storage_root_key),
            "storage_root": self.storage_root,
            "parent_process_id": std::process::id(),
        })
        .to_string()
    }
}

impl fmt::Debug for AppCredentials {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AppCredentials")
            .field("app_instance_id", &"[redacted]")
            .field("root_secret", &"[redacted]")
            .field("storage_root_key", &"[redacted]")
            .field("storage_root", &"[redacted]")
            .finish()
    }
}

impl Drop for AppCredentials {
    fn drop(&mut self) {
        self.root_secret.fill(0);
        self.storage_root_key.fill(0);
    }
}

#[cfg(any(test, feature = "contract-test"))]
fn credentials_for_launch() -> Result<AppCredentials, &'static str> {
    AppCredentials::generate().map_err(|_| "LOCAL_RANDOM_UNAVAILABLE")
}

#[cfg(not(any(test, feature = "contract-test")))]
fn credentials_for_launch() -> Result<AppCredentials, &'static str> {
    AppCredentials::generate_for_app()
}

fn hmac_sha256(secret: &[u8; 32], message: &[u8]) -> [u8; 32] {
    let mut inner_pad = [0x36_u8; 64];
    let mut outer_pad = [0x5c_u8; 64];
    for (index, byte) in secret.iter().enumerate() {
        inner_pad[index] ^= byte;
        outer_pad[index] ^= byte;
    }
    let mut inner = Sha256::new();
    inner.update(inner_pad);
    inner_pad.fill(0);
    inner.update(message);
    let inner_digest = inner.finalize();
    let mut outer = Sha256::new();
    outer.update(outer_pad);
    outer_pad.fill(0);
    outer.update(inner_digest);
    outer.finalize().into()
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReadyWire {
    event: String,
    protocol_version: String,
    app_instance_id: String,
    port: u16,
}

pub struct ReadyEnvelope {
    port: u16,
}

impl ReadyEnvelope {
    pub fn port(&self) -> u16 {
        self.port
    }
}

pub fn parse_ready_envelope(
    payload: &[u8],
    credentials: &AppCredentials,
) -> Result<ReadyEnvelope, String> {
    if payload.is_empty() || payload.len() > READY_MAX_BYTES {
        return Err("LOCAL_READY_INVALID_SIZE".to_owned());
    }
    let ready: ReadyWire =
        serde_json::from_slice(payload).map_err(|_| "LOCAL_READY_INVALID_FORMAT".to_owned())?;
    if ready.event != "ready"
        || ready.protocol_version != PROTOCOL_VERSION
        || ready.app_instance_id != credentials.app_instance_id
        || ready.port == 0
    {
        return Err("LOCAL_READY_MISMATCH".to_owned());
    }
    Ok(ReadyEnvelope { port: ready.port })
}

#[derive(Clone, Debug, Serialize)]
pub struct LocalServiceState {
    state: &'static str,
    retryable: bool,
    error_code: Option<&'static str>,
}

impl LocalServiceState {
    pub fn starting() -> Self {
        Self {
            state: "starting",
            retryable: false,
            error_code: None,
        }
    }

    pub fn ready() -> Self {
        Self {
            state: "ready",
            retryable: false,
            error_code: None,
        }
    }

    pub fn retrying() -> Self {
        Self {
            state: "retrying",
            retryable: false,
            error_code: None,
        }
    }

    pub fn unavailable(error_code: &'static str) -> Self {
        Self {
            state: "unavailable",
            retryable: true,
            error_code: Some(error_code),
        }
    }

    pub fn state(&self) -> &'static str {
        self.state
    }

    pub fn error_code(&self) -> Option<&'static str> {
        self.error_code
    }
}

struct RunningService {
    child: Child,
    stdin: Option<ChildStdin>,
    credentials: AppCredentials,
    recovery_sensitive: Arc<RecoverySensitiveBase>,
    port: u16,
    #[cfg(windows)]
    job: WindowsJob,
}

#[cfg(windows)]
struct WindowsJob {
    handle: isize,
}

#[cfg(windows)]
impl WindowsJob {
    fn create_kill_on_close() -> Result<Self, &'static str> {
        use std::mem::size_of;
        use std::ptr::null;
        use windows_sys::Win32::System::JobObjects::{
            CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };

        // SAFETY: Null security/name pointers request an unnamed job with default security.
        let handle = unsafe { CreateJobObjectW(null(), null()) };
        if handle.is_null() {
            return Err("LOCAL_JOB_CREATE_FAILED");
        }
        let job = Self {
            handle: handle as isize,
        };
        let mut information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: `information` is initialized for the exact information class and lives
        // for the full call. `job` owns a valid handle until this scope returns.
        let configured = unsafe {
            SetInformationJobObject(
                job.raw(),
                JobObjectExtendedLimitInformation,
                (&information as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if configured == 0 {
            return Err("LOCAL_JOB_CONFIGURE_FAILED");
        }
        Ok(job)
    }

    fn raw(&self) -> windows_sys::Win32::Foundation::HANDLE {
        self.handle as windows_sys::Win32::Foundation::HANDLE
    }

    fn assign(&self, child: &Child) -> Result<(), &'static str> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;

        // SAFETY: Both handles are live. The child is created suspended, so it
        // cannot create an unassigned descendant before this call succeeds.
        let assigned =
            unsafe { AssignProcessToJobObject(self.raw(), child.as_raw_handle().cast()) };
        (assigned != 0)
            .then_some(())
            .ok_or("LOCAL_JOB_ASSIGN_FAILED")
    }

    fn terminate(&self) -> Result<(), &'static str> {
        use windows_sys::Win32::System::JobObjects::TerminateJobObject;
        // SAFETY: The owned Job handle remains valid for this call.
        let terminated = unsafe { TerminateJobObject(self.raw(), 1) };
        (terminated != 0)
            .then_some(())
            .ok_or("LOCAL_JOB_TERMINATE_FAILED")
    }
}

#[cfg(windows)]
impl Drop for WindowsJob {
    fn drop(&mut self) {
        use windows_sys::Win32::Foundation::CloseHandle;
        // SAFETY: `handle` is uniquely owned and closed exactly once here.
        let _ = unsafe { CloseHandle(self.raw()) };
    }
}

#[cfg(windows)]
fn resume_primary_thread(process_id: u32) -> Result<(), &'static str> {
    use std::mem::size_of;
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows_sys::Win32::System::Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME};

    // SAFETY: Snapshot flags and process id follow the ToolHelp contract.
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err("LOCAL_THREAD_SNAPSHOT_FAILED");
    }
    let mut entry = THREADENTRY32 {
        dwSize: size_of::<THREADENTRY32>() as u32,
        ..THREADENTRY32::default()
    };
    // SAFETY: `entry` has the required size and remains valid during iteration.
    let mut found = unsafe { Thread32First(snapshot, &mut entry) } != 0;
    let result = loop {
        if !found {
            break Err("LOCAL_PRIMARY_THREAD_NOT_FOUND");
        }
        if entry.th32OwnerProcessID == process_id {
            // SAFETY: Thread id came from a live system snapshot. Inheritance is disabled.
            let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
            if thread.is_null() {
                break Err("LOCAL_PRIMARY_THREAD_OPEN_FAILED");
            }
            // SAFETY: The thread handle is live and has THREAD_SUSPEND_RESUME access.
            let previous_count = unsafe { ResumeThread(thread) };
            // SAFETY: The thread handle is uniquely owned by this scope.
            let _ = unsafe { CloseHandle(thread) };
            if previous_count == u32::MAX {
                break Err("LOCAL_PRIMARY_THREAD_RESUME_FAILED");
            }
            break Ok(());
        }
        // SAFETY: Same initialized snapshot and entry as Thread32First.
        found = unsafe { Thread32Next(snapshot, &mut entry) } != 0;
    };
    // SAFETY: The snapshot handle is uniquely owned by this scope.
    let _ = unsafe { CloseHandle(snapshot) };
    result
}

#[cfg(windows)]
fn spawn_suspended_in_job(command: &mut Command) -> Result<(Child, WindowsJob), &'static str> {
    use std::os::windows::process::CommandExt;
    use windows_sys::Win32::System::Threading::{CREATE_NO_WINDOW, CREATE_SUSPENDED};

    let job = WindowsJob::create_kill_on_close()?;
    command.creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED);
    let mut starting = StartingChild(Some(
        command.spawn().map_err(|_| "LOCAL_SERVICE_START_FAILED")?,
    ));
    job.assign(starting.child_mut())?;
    resume_primary_thread(starting.child_mut().id())?;
    Ok((starting.into_child(), job))
}

trait ManagedService: Send {
    fn poll_exit(&mut self) -> Result<Option<i32>, &'static str>;
    fn verify_health(&self) -> Result<(), &'static str>;
    fn prepare_recovery_request(
        &self,
        scope: &str,
        capability: &str,
        issued_at: u64,
    ) -> Result<RecoveryRequestContext, &'static str>;
    fn stop(self: Box<Self>, timeout: Duration) -> Result<(), &'static str>;
}

struct SpawnPermit {
    inner: Arc<ManagerInner>,
    generation: u64,
}

impl SpawnPermit {
    fn acquire(&self) -> Result<MutexGuard<'_, ()>, &'static str> {
        let gate = self
            .inner
            .spawn_gate
            .lock()
            .map_err(|_| "LOCAL_STATE_POISONED")?;
        let runtime = self
            .inner
            .runtime
            .lock()
            .map_err(|_| "LOCAL_STATE_POISONED")?;
        if runtime.shutting_down || runtime.generation != self.generation {
            return Err("LOCAL_SERVICE_STOPPED");
        }
        drop(runtime);
        Ok(gate)
    }
}

trait ServiceLauncher: Send + Sync {
    fn launch(
        &self,
        credentials: AppCredentials,
        startup_timeout: Duration,
        permit: &SpawnPermit,
    ) -> Result<Box<dyn ManagedService>, &'static str>;
}

struct RealServiceLauncher {
    executable: Option<PathBuf>,
    executable_args: Vec<String>,
    environment: Vec<(String, String)>,
}

impl ServiceLauncher for RealServiceLauncher {
    fn launch(
        &self,
        credentials: AppCredentials,
        startup_timeout: Duration,
        permit: &SpawnPermit,
    ) -> Result<Box<dyn ManagedService>, &'static str> {
        let executable = match self.executable.as_ref() {
            Some(path) => path.clone(),
            None => sidecar_path()?,
        };
        spawn_sidecar(
            executable,
            &self.executable_args,
            &self.environment,
            credentials,
            startup_timeout,
            permit,
        )
        .map(|running| Box::new(running) as Box<dyn ManagedService>)
    }
}

impl ManagedService for RunningService {
    fn poll_exit(&mut self) -> Result<Option<i32>, &'static str> {
        self.child
            .try_wait()
            .map(|status| status.map(|value| value.code().unwrap_or(1)))
            .map_err(|_| "LOCAL_SERVICE_WAIT_FAILED")
    }

    fn verify_health(&self) -> Result<(), &'static str> {
        verify_health(self)
    }

    fn prepare_recovery_request(
        &self,
        scope: &str,
        capability: &str,
        issued_at: u64,
    ) -> Result<RecoveryRequestContext, &'static str> {
        Ok(RecoveryRequestContext {
            port: self.port,
            token: self
                .credentials
                .issue_request_token(scope, capability, issued_at)
                .map_err(|_| "LOCAL_TOKEN_ISSUE_FAILED")?,
            sensitive: self.recovery_sensitive.clone(),
        })
    }

    fn stop(self: Box<Self>, timeout: Duration) -> Result<(), &'static str> {
        stop_child(*self, timeout)
    }
}

#[cfg(feature = "contract-test")]
struct ContractRecoveryService {
    credentials: AppCredentials,
    recovery_sensitive: Arc<RecoverySensitiveBase>,
    port: u16,
}

#[cfg(feature = "contract-test")]
impl ManagedService for ContractRecoveryService {
    fn poll_exit(&mut self) -> Result<Option<i32>, &'static str> {
        Ok(None)
    }

    fn verify_health(&self) -> Result<(), &'static str> {
        Ok(())
    }

    fn prepare_recovery_request(
        &self,
        scope: &str,
        capability: &str,
        issued_at: u64,
    ) -> Result<RecoveryRequestContext, &'static str> {
        Ok(RecoveryRequestContext {
            port: self.port,
            token: self
                .credentials
                .issue_request_token(scope, capability, issued_at)
                .map_err(|_| "LOCAL_TOKEN_ISSUE_FAILED")?,
            sensitive: self.recovery_sensitive.clone(),
        })
    }

    fn stop(self: Box<Self>, _timeout: Duration) -> Result<(), &'static str> {
        Ok(())
    }
}

struct StartingChild(Option<Child>);

impl StartingChild {
    fn child_mut(&mut self) -> &mut Child {
        self.0
            .as_mut()
            .expect("starting child is present until startup succeeds")
    }

    fn into_child(mut self) -> Child {
        self.0
            .take()
            .expect("starting child is present until startup succeeds")
    }
}

impl Drop for StartingChild {
    fn drop(&mut self) {
        if let Some(child) = self.0.as_mut() {
            let _ = child.kill();
            let deadline = Instant::now() + SHUTDOWN_TIMEOUT;
            while Instant::now() < deadline {
                match child.try_wait() {
                    Ok(Some(_)) | Err(_) => return,
                    Ok(None) => thread::sleep(Duration::from_millis(10)),
                }
            }
        }
    }
}

struct RuntimeState {
    public: LocalServiceState,
    running: Option<Box<dyn ManagedService>>,
    generation: u64,
    starting: bool,
    shutting_down: bool,
}

#[derive(Clone, Copy)]
struct ManagerTiming {
    startup_timeout: Duration,
    shutdown_timeout: Duration,
    monitor_interval: Duration,
    recovery_timeout: Duration,
}

impl Default for ManagerTiming {
    fn default() -> Self {
        Self {
            startup_timeout: STARTUP_TIMEOUT,
            shutdown_timeout: SHUTDOWN_TIMEOUT,
            monitor_interval: Duration::from_secs(1),
            recovery_timeout: IO_TIMEOUT,
        }
    }
}

struct ManagerInner {
    spawn_gate: Mutex<()>,
    runtime: Mutex<RuntimeState>,
    launcher: Arc<dyn ServiceLauncher>,
    timing: ManagerTiming,
}

#[derive(Clone)]
pub struct LocalServiceManager {
    inner: Arc<ManagerInner>,
}

impl Default for LocalServiceManager {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(feature = "contract-test")]
pub struct ContractRecoveryCanaries {
    root_secret: String,
    storage_root: String,
    quarantine_path: String,
}

#[cfg(feature = "contract-test")]
impl ContractRecoveryCanaries {
    pub fn root_secret(&self) -> &str {
        &self.root_secret
    }

    pub fn storage_root(&self) -> &str {
        &self.storage_root
    }

    pub fn quarantine_path(&self) -> &str {
        &self.quarantine_path
    }
}

#[cfg(feature = "contract-test")]
impl Drop for ContractRecoveryCanaries {
    fn drop(&mut self) {
        self.root_secret.zeroize();
        self.storage_root.zeroize();
        self.quarantine_path.zeroize();
    }
}

impl LocalServiceManager {
    pub fn new() -> Self {
        Self::with_real_launcher(None)
    }

    pub fn with_sidecar_path(executable: PathBuf) -> Self {
        Self::with_real_launcher(Some(executable))
    }

    #[cfg(feature = "contract-test")]
    pub fn with_contract_recovery_endpoint(port: u16, recovery_timeout: Duration) -> Self {
        Self::with_contract_recovery_endpoint_and_canaries(port, recovery_timeout).0
    }

    #[cfg(feature = "contract-test")]
    pub fn with_contract_recovery_endpoint_and_canaries(
        port: u16,
        recovery_timeout: Duration,
    ) -> (Self, ContractRecoveryCanaries) {
        let manager = Self::with_launcher_and_timing(
            Arc::new(RealServiceLauncher {
                executable: None,
                executable_args: Vec::new(),
                environment: Vec::new(),
            }),
            ManagerTiming {
                recovery_timeout,
                ..ManagerTiming::default()
            },
        );
        let credentials = AppCredentials::generate().expect("contract credentials");
        let recovery_sensitive = Arc::new(RecoverySensitiveBase::new(&credentials));
        let canaries = ContractRecoveryCanaries {
            root_secret: recovery_sensitive.root_secret.clone(),
            storage_root: recovery_sensitive.storage_root.clone(),
            quarantine_path: recovery_sensitive.quarantine_path.clone(),
        };
        {
            let mut runtime = manager.inner.runtime.lock().expect("contract runtime");
            runtime.public = LocalServiceState::ready();
            runtime.running = Some(Box::new(ContractRecoveryService {
                credentials,
                recovery_sensitive,
                port,
            }));
        }
        (manager, canaries)
    }

    fn with_real_launcher(executable: Option<PathBuf>) -> Self {
        Self::with_launcher_and_timing(
            Arc::new(RealServiceLauncher {
                executable,
                executable_args: Vec::new(),
                environment: Vec::new(),
            }),
            ManagerTiming::default(),
        )
    }

    fn with_launcher_and_timing(launcher: Arc<dyn ServiceLauncher>, timing: ManagerTiming) -> Self {
        Self {
            inner: Arc::new(ManagerInner {
                spawn_gate: Mutex::new(()),
                runtime: Mutex::new(RuntimeState {
                    public: LocalServiceState::starting(),
                    running: None,
                    generation: 0,
                    starting: false,
                    shutting_down: false,
                }),
                launcher,
                timing,
            }),
        }
    }

    pub fn status(&self) -> LocalServiceState {
        self.inner
            .runtime
            .lock()
            .map(|runtime| runtime.public.clone())
            .unwrap_or_else(|_| LocalServiceState::unavailable("LOCAL_STATE_POISONED"))
    }

    pub(crate) fn execute_recovery_request(
        &self,
        scope: &str,
        capability: &str,
        method: &str,
        path: &str,
        body: &[u8],
    ) -> Result<LocalHttpResponse, &'static str> {
        if !approved_recovery_request(scope, capability, method, path, body.len()) {
            return Err("LOCAL_COMMAND_NOT_ALLOWED");
        }
        let issued_at = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "LOCAL_TOKEN_TIME_INVALID")?
            .as_secs();
        let context = {
            let runtime = self
                .inner
                .runtime
                .lock()
                .map_err(|_| "LOCAL_STATE_POISONED")?;
            if runtime.shutting_down || runtime.public.state() != "ready" {
                return Err("LOCAL_SERVICE_UNAVAILABLE");
            }
            runtime
                .running
                .as_ref()
                .ok_or("LOCAL_SERVICE_UNAVAILABLE")?
                .prepare_recovery_request(scope, capability, issued_at)?
        };
        execute_recovery_http(
            context,
            method,
            path,
            body,
            self.inner.timing.recovery_timeout,
            RECOVERY_RESPONSE_MAX_BYTES,
            None,
        )
    }

    pub(crate) fn execute_studio_request(
        &self,
        scope: &str,
        capability: &str,
        method: &str,
        path: &str,
        body: &[u8],
    ) -> Result<LocalHttpResponse, &'static str> {
        if !approved_studio_request(scope, capability, method, path, body.len()) {
            return Err("LOCAL_COMMAND_NOT_ALLOWED");
        }
        let issued_at = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "LOCAL_TOKEN_TIME_INVALID")?
            .as_secs();
        let context = {
            let runtime = self
                .inner
                .runtime
                .lock()
                .map_err(|_| "LOCAL_STATE_POISONED")?;
            if runtime.shutting_down || runtime.public.state() != "ready" {
                return Err("LOCAL_SERVICE_UNAVAILABLE");
            }
            runtime
                .running
                .as_ref()
                .ok_or("LOCAL_SERVICE_UNAVAILABLE")?
                .prepare_recovery_request(scope, capability, issued_at)?
        };
        execute_recovery_http(
            context,
            method,
            path,
            body,
            self.inner.timing.recovery_timeout,
            2 * 1024 * 1024,
            None,
        )
    }

    pub(crate) fn execute_workspace_studio_request(
        &self,
        workspace_id: &str,
        scope: &str,
        capability: &str,
        method: &str,
        path: &str,
        body: &[u8],
    ) -> Result<LocalHttpResponse, &'static str> {
        if !valid_workspace_id(workspace_id)
            || !approved_studio_request(scope, capability, method, path, body.len())
        {
            return Err("LOCAL_COMMAND_NOT_ALLOWED");
        }
        let issued_at = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "LOCAL_TOKEN_TIME_INVALID")?
            .as_secs();
        let context = {
            let runtime = self
                .inner
                .runtime
                .lock()
                .map_err(|_| "LOCAL_STATE_POISONED")?;
            if runtime.shutting_down || runtime.public.state() != "ready" {
                return Err("LOCAL_SERVICE_UNAVAILABLE");
            }
            runtime
                .running
                .as_ref()
                .ok_or("LOCAL_SERVICE_UNAVAILABLE")?
                .prepare_recovery_request(scope, capability, issued_at)?
        };
        execute_recovery_http(
            context,
            method,
            path,
            body,
            self.inner.timing.recovery_timeout,
            2 * 1024 * 1024,
            Some(workspace_id),
        )
    }

    pub fn start_async(&self) -> LocalServiceState {
        self.launch_async(false)
    }

    pub fn retry_async(&self) -> LocalServiceState {
        self.launch_async(true)
    }

    fn launch_async(&self, retry: bool) -> LocalServiceState {
        let (generation, previous) = {
            let mut runtime = match self.inner.runtime.lock() {
                Ok(runtime) => runtime,
                Err(_) => return LocalServiceState::unavailable("LOCAL_STATE_POISONED"),
            };
            if runtime.shutting_down {
                return LocalServiceState::unavailable("LOCAL_SERVICE_STOPPED");
            }
            if runtime.starting {
                return runtime.public.clone();
            }
            if !retry && runtime.running.is_some() {
                return runtime.public.clone();
            }
            runtime.generation = runtime.generation.wrapping_add(1);
            runtime.starting = true;
            runtime.public = if retry {
                LocalServiceState::retrying()
            } else {
                LocalServiceState::starting()
            };
            (runtime.generation, runtime.running.take())
        };
        let manager = self.clone();
        thread::spawn(move || {
            if let Some(previous) = previous {
                if let Err(error) = previous.stop(manager.inner.timing.shutdown_timeout) {
                    manager.finish_start_failure(generation, error);
                    return;
                }
            }
            manager.run_start(generation);
        });
        self.status()
    }

    fn finish_start_failure(&self, generation: u64, error_code: &'static str) {
        if let Ok(mut runtime) = self.inner.runtime.lock() {
            if !runtime.shutting_down && runtime.generation == generation {
                runtime.starting = false;
                runtime.public = LocalServiceState::unavailable(error_code);
            }
        }
    }

    fn run_start(&self, generation: u64) {
        let permit = SpawnPermit {
            inner: self.inner.clone(),
            generation,
        };
        let result = credentials_for_launch()
            .and_then(|credentials| {
                self.inner
                    .launcher
                    .launch(credentials, self.inner.timing.startup_timeout, &permit)
            })
            .and_then(|service| match service.verify_health() {
                Ok(()) => Ok(service),
                Err(error) => match service.stop(self.inner.timing.shutdown_timeout) {
                    Ok(()) => Err(error),
                    Err(stop_error) => Err(stop_error),
                },
            });

        let mut stale_service = None;
        {
            let mut runtime = match self.inner.runtime.lock() {
                Ok(runtime) => runtime,
                Err(_) => {
                    if let Ok(service) = result {
                        let _ = service.stop(self.inner.timing.shutdown_timeout);
                    }
                    return;
                }
            };
            if runtime.shutting_down || runtime.generation != generation {
                if let Ok(service) = result {
                    stale_service = Some(service);
                }
            } else {
                runtime.starting = false;
                match result {
                    Ok(service) => {
                        runtime.running = Some(service);
                        runtime.public = LocalServiceState::ready();
                    }
                    Err(error_code) => {
                        runtime.public = LocalServiceState::unavailable(error_code);
                    }
                }
            }
        }
        if let Some(service) = stale_service {
            let _ = service.stop(self.inner.timing.shutdown_timeout);
            return;
        }
        self.monitor(generation);
    }

    fn monitor(&self, generation: u64) {
        loop {
            thread::sleep(self.inner.timing.monitor_interval);
            let stopped = {
                let mut runtime = match self.inner.runtime.lock() {
                    Ok(runtime) => runtime,
                    Err(_) => return,
                };
                if runtime.shutting_down
                    || runtime.generation != generation
                    || runtime.running.is_none()
                {
                    return;
                }
                let result = {
                    let service = runtime.running.as_mut().expect("running service");
                    match service.poll_exit() {
                        Ok(Some(_)) => Err("LOCAL_SERVICE_EXITED"),
                        Ok(None) => service.verify_health(),
                        Err(error) => Err(error),
                    }
                };
                match result {
                    Ok(()) => None,
                    Err(error_code) => {
                        let service = runtime.running.take();
                        runtime.public = LocalServiceState::unavailable(error_code);
                        service
                    }
                }
            };
            if let Some(service) = stopped {
                if let Err(error_code) = service.stop(self.inner.timing.shutdown_timeout) {
                    self.finish_start_failure(generation, error_code);
                }
                return;
            }
        }
    }

    pub fn start(&self) -> LocalServiceState {
        self.start_async()
    }

    pub fn retry(&self) -> LocalServiceState {
        self.retry_async()
    }

    pub fn shutdown(&self) {
        let spawn_gate = match self.inner.spawn_gate.lock() {
            Ok(gate) => gate,
            Err(_) => return,
        };
        let running = {
            let mut runtime = match self.inner.runtime.lock() {
                Ok(runtime) => runtime,
                Err(_) => return,
            };
            if runtime.shutting_down {
                return;
            }
            runtime.shutting_down = true;
            runtime.starting = false;
            runtime.generation = runtime.generation.wrapping_add(1);
            runtime.public = LocalServiceState::unavailable("LOCAL_SERVICE_STOPPED");
            runtime.running.take()
        };
        drop(spawn_gate);
        if let Some(running) = running {
            if let Err(error_code) = running.stop(self.inner.timing.shutdown_timeout) {
                if let Ok(mut runtime) = self.inner.runtime.lock() {
                    runtime.public = LocalServiceState::unavailable(error_code);
                }
            }
        }
    }
}

fn sidecar_path() -> Result<PathBuf, &'static str> {
    let executable = std::env::current_exe().map_err(|_| "LOCAL_EXECUTABLE_PATH_UNAVAILABLE")?;
    let directory = executable
        .parent()
        .ok_or("LOCAL_EXECUTABLE_PATH_UNAVAILABLE")?;
    let file_name = if cfg!(windows) {
        "daon-user-local-service.exe"
    } else {
        "daon-user-local-service"
    };
    Ok(directory.join(file_name))
}

fn spawn_sidecar(
    executable: PathBuf,
    executable_args: &[String],
    environment: &[(String, String)],
    credentials: AppCredentials,
    startup_timeout: Duration,
    permit: &SpawnPermit,
) -> Result<RunningService, &'static str> {
    if !executable.is_file() {
        return Err("LOCAL_SERVICE_BINARY_MISSING");
    }
    let mut command = Command::new(executable);
    command
        .args(executable_args)
        .envs(environment.iter().map(|(key, value)| (key, value)))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let spawn_gate = permit.acquire()?;
    #[cfg(windows)]
    let (child, job) = spawn_suspended_in_job(&mut command)?;
    #[cfg(not(windows))]
    let child = command.spawn().map_err(|_| "LOCAL_SERVICE_START_FAILED")?;
    drop(spawn_gate);
    let mut starting = StartingChild(Some(child));
    let mut child_stdin = starting
        .child_mut()
        .stdin
        .take()
        .ok_or("LOCAL_SERVICE_IPC_FAILED")?;
    let child_stdout = starting
        .child_mut()
        .stdout
        .take()
        .ok_or("LOCAL_SERVICE_IPC_FAILED")?;
    let bootstrap = credentials.bootstrap_json();
    child_stdin
        .write_all(bootstrap.as_bytes())
        .and_then(|_| child_stdin.write_all(b"\n"))
        .and_then(|_| child_stdin.flush())
        .map_err(|_| "LOCAL_SERVICE_BOOTSTRAP_FAILED")?;

    let (sender, receiver) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let mut reader = BufReader::new(child_stdout).take((READY_MAX_BYTES + 1) as u64);
        let mut line = Vec::new();
        let result = reader.read_until(b'\n', &mut line).map(|_| line);
        let _ = sender.send(result);
    });
    let line = receiver
        .recv_timeout(startup_timeout)
        .map_err(|_| "LOCAL_SERVICE_START_TIMEOUT")?
        .map_err(|_| "LOCAL_SERVICE_READY_IO_FAILED")?;
    let payload = line.strip_suffix(b"\n").unwrap_or(&line);
    let ready =
        parse_ready_envelope(payload, &credentials).map_err(|_| "LOCAL_SERVICE_READY_REJECTED")?;
    let recovery_sensitive = Arc::new(RecoverySensitiveBase::new(&credentials));
    let running = RunningService {
        child: starting.into_child(),
        stdin: Some(child_stdin),
        credentials,
        recovery_sensitive,
        port: ready.port(),
        #[cfg(windows)]
        job,
    };
    if let Err(error) = verify_health(&running) {
        let _ = stop_child(running, SHUTDOWN_TIMEOUT);
        return Err(error);
    }
    Ok(running)
}

fn verify_health(running: &RunningService) -> Result<(), &'static str> {
    let address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, running.port);
    let mut stream = TcpStream::connect_timeout(&address.into(), IO_TIMEOUT)
        .map_err(|_| "LOCAL_HEALTH_FAILED")?;
    stream
        .set_read_timeout(Some(IO_TIMEOUT))
        .map_err(|_| "LOCAL_HEALTH_FAILED")?;
    stream
        .set_write_timeout(Some(IO_TIMEOUT))
        .map_err(|_| "LOCAL_HEALTH_FAILED")?;
    let issued_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "LOCAL_TOKEN_TIME_INVALID")?
        .as_secs();
    let token = running
        .credentials
        .issue_request_token("runtime.read", "runtime.status.read", issued_at)
        .map_err(|_| "LOCAL_TOKEN_ISSUE_FAILED")?;
    let request = format!(
        "GET /v1/status HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n",
        running.port, token
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|_| "LOCAL_HEALTH_FAILED")?;
    let mut response = String::new();
    stream
        .take(8192)
        .read_to_string(&mut response)
        .map_err(|_| "LOCAL_HEALTH_FAILED")?;
    if !response.starts_with("HTTP/1.1 200") || !response.contains("\"status\":\"ready\"") {
        return Err("LOCAL_HEALTH_REJECTED");
    }
    Ok(())
}

pub(crate) struct LocalHttpResponse {
    pub(crate) status: u16,
    pub(crate) content_type: Option<String>,
    pub(crate) content_length: Option<usize>,
    pub(crate) body: Vec<u8>,
}

const RECOVERY_RESPONSE_MAX_BYTES: usize = 1_048_576;
const RECOVERY_HEADER_MAX_BYTES: usize = 8192;

struct RecoverySensitiveBase {
    root_secret: String,
    storage_root: String,
    quarantine_path: String,
}

impl RecoverySensitiveBase {
    fn new(credentials: &AppCredentials) -> Self {
        Self {
            root_secret: credentials.root_secret_hex(),
            storage_root: credentials.storage_root.clone(),
            quarantine_path: PathBuf::from(&credentials.storage_root)
                .join("quarantine")
                .to_string_lossy()
                .into_owned(),
        }
    }
}

impl Drop for RecoverySensitiveBase {
    fn drop(&mut self) {
        self.root_secret.zeroize();
        self.storage_root.zeroize();
        self.quarantine_path.zeroize();
    }
}

struct RecoveryRequestContext {
    port: u16,
    token: String,
    sensitive: Arc<RecoverySensitiveBase>,
}

impl Drop for RecoveryRequestContext {
    fn drop(&mut self) {
        self.token.zeroize();
    }
}

impl RecoveryRequestContext {
    fn appears_in(&self, response: &[u8]) -> bool {
        let port = Zeroizing::new(self.port.to_string());
        [
            port.as_bytes(),
            self.token.as_bytes(),
            self.sensitive.root_secret.as_bytes(),
            self.sensitive.storage_root.as_bytes(),
            self.sensitive.quarantine_path.as_bytes(),
        ]
        .into_iter()
        .any(|canary| {
            !canary.is_empty()
                && response
                    .windows(canary.len())
                    .any(|window| window == canary)
        })
    }

    fn appears_in_decoded_json(&self, body: &[u8]) -> bool {
        let Ok(mut value) = serde_json::from_slice::<serde_json::Value>(body) else {
            return false;
        };
        let port = Zeroizing::new(self.port.to_string());
        let storage_root = Zeroizing::new(normalize_windows_path(&self.sensitive.storage_root));
        let quarantine_path =
            Zeroizing::new(normalize_windows_path(&self.sensitive.quarantine_path));
        self.json_value_contains_context(&mut value, &port, &storage_root, &quarantine_path)
    }

    fn json_value_contains_context(
        &self,
        value: &mut serde_json::Value,
        port: &str,
        storage_root: &str,
        quarantine_path: &str,
    ) -> bool {
        match value {
            serde_json::Value::String(text) => {
                let found = self.string_contains_context(text, port, storage_root, quarantine_path);
                text.zeroize();
                found
            }
            serde_json::Value::Array(values) => {
                let mut found = false;
                for value in values {
                    found |= self.json_value_contains_context(
                        value,
                        port,
                        storage_root,
                        quarantine_path,
                    );
                }
                found
            }
            serde_json::Value::Object(values) => {
                let mut found = false;
                for (mut key, mut value) in std::mem::take(values) {
                    found |=
                        self.string_contains_context(&key, port, storage_root, quarantine_path);
                    key.zeroize();
                    found |= self.json_value_contains_context(
                        &mut value,
                        port,
                        storage_root,
                        quarantine_path,
                    );
                }
                found
            }
            _ => false,
        }
    }

    fn string_contains_context(
        &self,
        value: &str,
        port: &str,
        storage_root: &str,
        quarantine_path: &str,
    ) -> bool {
        if value.contains(port)
            || value.contains(&self.token)
            || contains_ascii_case_insensitive(value, &self.sensitive.root_secret)
        {
            return true;
        }
        let normalized = Zeroizing::new(normalize_windows_path(value));
        (!storage_root.is_empty() && normalized.contains(storage_root))
            || (!quarantine_path.is_empty() && normalized.contains(quarantine_path))
    }
}

fn contains_ascii_case_insensitive(value: &str, needle: &str) -> bool {
    !needle.is_empty()
        && value
            .as_bytes()
            .windows(needle.len())
            .any(|window| window.eq_ignore_ascii_case(needle.as_bytes()))
}

fn normalize_windows_path(value: &str) -> String {
    value
        .chars()
        .flat_map(char::to_lowercase)
        .map(|character| if character == '\\' { '/' } else { character })
        .collect()
}

fn approved_recovery_request(
    scope: &str,
    capability: &str,
    method: &str,
    path: &str,
    body_len: usize,
) -> bool {
    match (scope, capability, method, path) {
        ("recovery.write", "recovery.scan", "POST", "/local/v1/recovery/scans") => body_len <= 4096,
        ("recovery.read", "recovery.job.read", "GET", path) => {
            body_len == 0 && recovery_job_path(path, false)
        }
        ("recovery.write", "recovery.repair", "POST", path) => {
            body_len <= 1024 && recovery_job_path(path, true)
        }
        _ => false,
    }
}

fn recovery_job_path(path: &str, repair: bool) -> bool {
    const PREFIX: &str = "/local/v1/recovery/jobs/";
    let Some(mut job_id) = path.strip_prefix(PREFIX) else {
        return false;
    };
    if repair {
        let Some(value) = job_id.strip_suffix("/repair") else {
            return false;
        };
        job_id = value;
    } else if job_id.contains('/') {
        return false;
    }
    !job_id.is_empty()
        && job_id.len() <= 256
        && job_id.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric() || (index > 0 && matches!(byte, b'.' | b'_' | b':' | b'-'))
        })
}

fn approved_studio_request(
    scope: &str,
    capability: &str,
    method: &str,
    path: &str,
    body_len: usize,
) -> bool {
    match (scope, capability, method, path) {
        ("studio.read", "studio_models_list", "GET", "/local/v1/studio/models") => {
            body_len == 0
        }
        ("studio.read", "studio_raw_sources_list", "GET", "/local/v1/studio/raw-sources") => {
            body_len == 0
        }
        ("studio.write", "studio_raw_source_import", "POST", "/local/v1/studio/raw-sources") => {
            body_len <= 36 * 1024 * 1024
        }
        ("studio.write", "studio_provider_settings_import", "POST", "/local/v1/studio/provider-settings") => {
            body_len <= 256 * 1024
        }
        ("studio.write", "studio_context_prepare", "POST", "/local/v1/studio/knowledge-contexts") => {
            body_len <= 32_768
        }
        ("studio.write", "studio_settings_confirm", "POST", "/local/v1/studio/settings/confirm") => {
            body_len <= 16_384
        }
        ("studio.write", "studio_draft_generate", "POST", "/local/v1/studio/drafts/generate") => {
            body_len <= 4096
        }
        ("studio.read", "studio_draft_get", "GET", path) => {
            body_len == 0 && studio_draft_path(path, None)
        }
        ("studio.write", "studio_draft_append_version", "POST", path) => {
            body_len <= 1_100_000 && studio_draft_path(path, Some("versions"))
        }
        ("studio.write", "studio_sync_queue", "POST", path) => {
            body_len <= 32_768 && studio_draft_path(path, Some("sync-queue"))
        }
        ("knowledge.write", "studio_knowledge_copy_import", "POST", "/local/v1/studio/knowledge-copies") => {
            body_len <= 16 * 1024 * 1024
        }
        ("knowledge.write", "studio_knowledge_copy_refresh", "POST", path) => {
            body_len <= 32 * 1024
                && studio_scoped_id_path(path, "/local/v1/studio/knowledge-copies/", "/refresh")
        }
        ("sync.read", "studio_sync_state_read", "GET", path) => {
            body_len == 0
                && studio_scoped_id_path(path, "/local/v1/studio/sync-operations/", "")
        }
        ("sync.write", "studio_sync_state_append", "POST", path) => {
            body_len <= 64 * 1024
                && studio_scoped_id_path(path, "/local/v1/studio/sync-operations/", "/states")
        }
        _ => false,
    }
}

fn studio_scoped_id_path(path: &str, prefix: &str, suffix: &str) -> bool {
    let Some(value) = path.strip_prefix(prefix) else {
        return false;
    };
    let Some(identifier) = value.strip_suffix(suffix) else {
        return false;
    };
    !identifier.is_empty()
        && !identifier.contains('/')
        && identifier.len() <= 256
        && identifier.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric()
                || (index > 0 && matches!(byte, b'.' | b'_' | b':' | b'-'))
        })
}

fn valid_workspace_id(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(index, byte)| match index {
            8 | 13 | 18 | 23 => byte == b'-',
            _ => byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase(),
        })
}

fn studio_draft_path(path: &str, suffix: Option<&str>) -> bool {
    const PREFIX: &str = "/local/v1/studio/drafts/";
    let Some(mut draft_id) = path.strip_prefix(PREFIX) else {
        return false;
    };
    if let Some(suffix) = suffix {
        let ending = format!("/{suffix}");
        let Some(value) = draft_id.strip_suffix(&ending) else {
            return false;
        };
        draft_id = value;
    } else if draft_id.contains('/') || draft_id == "generate" {
        return false;
    }
    !draft_id.is_empty()
        && draft_id.len() <= 256
        && draft_id.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric()
                || (index > 0 && matches!(byte, b'.' | b'_' | b':' | b'-'))
        })
}

fn execute_recovery_http(
    context: RecoveryRequestContext,
    method: &str,
    path: &str,
    body: &[u8],
    timeout: Duration,
    response_max_bytes: usize,
    workspace_id: Option<&str>,
) -> Result<LocalHttpResponse, &'static str> {
    let deadline = Instant::now() + timeout;
    let address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, context.port);
    let mut stream = TcpStream::connect_timeout(&address.into(), remaining(deadline)?)
        .map_err(|_| "LOCAL_RECOVERY_CONNECT_FAILED")?;
    let socket_timeout = remaining(deadline)?;
    stream
        .set_read_timeout(Some(socket_timeout))
        .and_then(|_| stream.set_write_timeout(Some(socket_timeout)))
        .map_err(|_| "LOCAL_RECOVERY_TIMEOUT_CONFIG_FAILED")?;
    let workspace_headers = if let Some(workspace_id) = workspace_id {
        let mut key = decode_hex_32(&context.sensitive.root_secret)?;
        let mut message = format!("{}|{workspace_id}", context.token);
        let mut proof = hmac_sha256(&key, message.as_bytes());
        key.fill(0);
        message.zeroize();
        let header = format!(
            "X-Daon-Workspace-Id: {workspace_id}\r\nX-Daon-Workspace-Proof: {}\r\n",
            hex(&proof),
        );
        proof.fill(0);
        header
    } else {
        String::new()
    };
    let mut request = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {token}\r\n{workspace_headers}Accept: application/json\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        context.port,
        body.len(),
        token = context.token,
    );
    let write_result = stream.write_all(request.as_bytes()).and_then(|_| {
        if body.is_empty() {
            Ok(())
        } else {
            stream.set_write_timeout(Some(remaining(deadline).map_err(std::io::Error::other)?))?;
            stream.write_all(body)
        }
    });
    request.zeroize();
    write_result.map_err(|_| "LOCAL_RECOVERY_REQUEST_FAILED")?;

    let mut raw = Zeroizing::new(Vec::new());
    let maximum = RECOVERY_HEADER_MAX_BYTES + response_max_bytes + 1;
    let mut chunk = [0_u8; 8192];
    loop {
        stream
            .set_read_timeout(Some(remaining(deadline)?))
            .map_err(|_| "LOCAL_RECOVERY_TIMEOUT_CONFIG_FAILED")?;
        let count = stream.read(&mut chunk).map_err(|error| {
            if error.kind() == std::io::ErrorKind::TimedOut
                || error.kind() == std::io::ErrorKind::WouldBlock
            {
                "LOCAL_RECOVERY_REQUEST_TIMEOUT"
            } else {
                "LOCAL_RECOVERY_RESPONSE_REJECTED"
            }
        })?;
        if count == 0 {
            break;
        }
        if raw.len() + count > maximum {
            return Err("LOCAL_RECOVERY_RESPONSE_REJECTED");
        }
        raw.extend_from_slice(&chunk[..count]);
    }
    if context.appears_in(&raw) {
        raw.zeroize();
        return Err("LOCAL_RECOVERY_RESPONSE_REJECTED");
    }
    let parsed = parse_local_http_response_with_limit(&raw, response_max_bytes);
    raw.zeroize();
    let mut response = parsed?;
    if context.appears_in_decoded_json(&response.body) {
        response.body.zeroize();
        return Err("LOCAL_RECOVERY_RESPONSE_REJECTED");
    }
    Ok(response)
}

fn remaining(deadline: Instant) -> Result<Duration, &'static str> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|duration| !duration.is_zero())
        .ok_or("LOCAL_RECOVERY_REQUEST_TIMEOUT")
}

#[cfg(test)]
fn parse_local_http_response(raw: &[u8]) -> Result<LocalHttpResponse, &'static str> {
    parse_local_http_response_with_limit(raw, RECOVERY_RESPONSE_MAX_BYTES)
}

fn decode_hex_32(value: &str) -> Result<[u8; 32], &'static str> {
    if value.len() != 64 {
        return Err("LOCAL_WORKSPACE_BINDING_FAILED");
    }
    let mut result = [0_u8; 32];
    for (index, chunk) in value.as_bytes().chunks_exact(2).enumerate() {
        let text = std::str::from_utf8(chunk).map_err(|_| "LOCAL_WORKSPACE_BINDING_FAILED")?;
        result[index] = u8::from_str_radix(text, 16)
            .map_err(|_| "LOCAL_WORKSPACE_BINDING_FAILED")?;
    }
    Ok(result)
}

fn parse_local_http_response_with_limit(
    raw: &[u8], response_max_bytes: usize
) -> Result<LocalHttpResponse, &'static str> {
    let separator = raw
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or("LOCAL_RECOVERY_RESPONSE_REJECTED")?;
    if separator > RECOVERY_HEADER_MAX_BYTES {
        return Err("LOCAL_RECOVERY_RESPONSE_REJECTED");
    }
    let headers =
        std::str::from_utf8(&raw[..separator]).map_err(|_| "LOCAL_RECOVERY_RESPONSE_REJECTED")?;
    let mut lines = headers.split("\r\n");
    let mut status_line = lines
        .next()
        .ok_or("LOCAL_RECOVERY_RESPONSE_REJECTED")?
        .split_ascii_whitespace();
    if status_line.next() != Some("HTTP/1.1") {
        return Err("LOCAL_RECOVERY_RESPONSE_REJECTED");
    }
    let status = status_line
        .next()
        .filter(|value| value.len() == 3 && value.bytes().all(|byte| byte.is_ascii_digit()))
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or("LOCAL_RECOVERY_RESPONSE_REJECTED")?;
    let mut content_type = None;
    let mut content_length = None;
    for line in lines {
        let (name, value) = line
            .split_once(':')
            .ok_or("LOCAL_RECOVERY_RESPONSE_REJECTED")?;
        let value = value.trim();
        if name.eq_ignore_ascii_case("content-type") {
            if content_type.replace(value.to_owned()).is_some() {
                return Err("LOCAL_RECOVERY_RESPONSE_REJECTED");
            }
        } else if name.eq_ignore_ascii_case("content-length") {
            let length = value
                .parse::<usize>()
                .map_err(|_| "LOCAL_RECOVERY_RESPONSE_REJECTED")?;
            if content_length.replace(length).is_some() {
                return Err("LOCAL_RECOVERY_RESPONSE_REJECTED");
            }
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            return Err("LOCAL_RECOVERY_RESPONSE_REJECTED");
        }
    }
    let body = raw[(separator + 4)..].to_vec();
    if body.len() > response_max_bytes || content_length != Some(body.len()) {
        return Err("LOCAL_RECOVERY_RESPONSE_REJECTED");
    }
    Ok(LocalHttpResponse {
        status,
        content_type,
        content_length,
        body,
    })
}

trait ProcessTermination {
    fn has_exited(&mut self) -> Result<bool, &'static str>;
    fn terminate_job(&mut self) -> Result<(), &'static str>;
    fn kill_child(&mut self) -> Result<(), &'static str>;
}

trait StopClock {
    fn now(&self) -> Instant;
    fn sleep(&self, duration: Duration);
}

struct SystemStopClock;

impl StopClock for SystemStopClock {
    fn now(&self) -> Instant {
        Instant::now()
    }

    fn sleep(&self, duration: Duration) {
        thread::sleep(duration);
    }
}

struct RunningTermination<'a> {
    child: &'a mut Child,
    #[cfg(windows)]
    job: &'a WindowsJob,
}

impl ProcessTermination for RunningTermination<'_> {
    fn has_exited(&mut self) -> Result<bool, &'static str> {
        self.child
            .try_wait()
            .map(|status| status.is_some())
            .map_err(|_| "LOCAL_SERVICE_WAIT_FAILED")
    }

    #[cfg(windows)]
    fn terminate_job(&mut self) -> Result<(), &'static str> {
        self.job.terminate()
    }

    #[cfg(not(windows))]
    fn terminate_job(&mut self) -> Result<(), &'static str> {
        Err("LOCAL_JOB_TERMINATE_UNAVAILABLE")
    }

    fn kill_child(&mut self) -> Result<(), &'static str> {
        self.child.kill().map_err(|_| "LOCAL_CHILD_KILL_FAILED")
    }
}

fn wait_for_process_exit(
    process: &mut dyn ProcessTermination,
    clock: &dyn StopClock,
    timeout: Duration,
) -> Result<bool, &'static str> {
    let deadline = clock.now() + timeout;
    loop {
        if process.has_exited()? {
            return Ok(true);
        }
        let now = clock.now();
        if now >= deadline {
            return Ok(false);
        }
        clock.sleep((deadline - now).min(Duration::from_millis(50)));
    }
}

fn stop_process(
    process: &mut dyn ProcessTermination,
    clock: &dyn StopClock,
    timeout: Duration,
) -> Result<(), &'static str> {
    if wait_for_process_exit(process, clock, timeout)? {
        return Ok(());
    }

    #[cfg(windows)]
    if process.terminate_job().is_ok() && wait_for_process_exit(process, clock, timeout)? {
        return Ok(());
    }

    process.kill_child()?;
    match wait_for_process_exit(process, clock, timeout)? {
        true => Ok(()),
        false => Err("LOCAL_SERVICE_STOP_TIMEOUT"),
    }
}

fn stop_child(mut running: RunningService, timeout: Duration) -> Result<(), &'static str> {
    running.stdin.take();
    let mut process = RunningTermination {
        child: &mut running.child,
        #[cfg(windows)]
        job: &running.job,
    };
    stop_process(&mut process, &SystemStopClock, timeout)
}

impl Drop for LocalServiceManager {
    fn drop(&mut self) {
        if Arc::strong_count(&self.inner) == 1 {
            self.shutdown();
        }
    }
}

#[cfg(test)]
mod manager_tests {
    use super::*;
    use std::collections::VecDeque;
    #[cfg(windows)]
    use std::net::TcpListener;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::sync::Arc;

    #[test]
    fn request_tokens_are_short_lived_command_bound_and_unique() {
        let credentials = AppCredentials::generate().expect("credentials");
        let first = credentials
            .issue_request_token("runtime.read", "runtime.status.read", 2_000_000_000)
            .expect("first token");
        let second = credentials
            .issue_request_token("runtime.read", "runtime.capabilities.read", 2_000_000_000)
            .expect("second token");
        let first_fields: Vec<_> = first.split('|').collect();
        let second_fields: Vec<_> = second.split('|').collect();
        assert_eq!(first_fields.len(), 8);
        assert_eq!(first_fields[0], "lt1");
        assert_eq!(first_fields[1], "2000000000");
        assert_eq!(first_fields[2], "2000000060");
        assert_eq!(first_fields[3], credentials.app_instance_id());
        assert_eq!(first_fields[4], "runtime.read");
        assert_eq!(first_fields[5], "runtime.status.read");
        assert_eq!(first_fields[6].len(), 64);
        assert_eq!(first_fields[7].len(), 64);
        assert_eq!(second_fields[5], "runtime.capabilities.read");
        assert_ne!(first_fields[6], second_fields[6]);
        assert_ne!(first, second);
    }

    #[test]
    fn bootstrap_binds_actual_parent_process_without_debug_secret_leak() {
        let credentials = AppCredentials::generate().expect("credentials");
        let bootstrap: serde_json::Value =
            serde_json::from_str(&credentials.bootstrap_json()).expect("bootstrap json");
        assert_eq!(bootstrap["parent_process_id"], std::process::id());
        assert_eq!(bootstrap["root_secret"].as_str().expect("secret").len(), 64);
        let debug = format!("{credentials:?}");
        assert!(!debug.contains(bootstrap["root_secret"].as_str().expect("secret")));
        assert!(!debug.contains(credentials.app_instance_id()));
    }

    #[test]
    fn recovery_http_parser_rejects_a_forged_status_line() {
        let body = br#"{"data":{}}"#;
        let raw = format!(
            "GARBAGE 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            std::str::from_utf8(body).expect("body")
        )
        .into_bytes();
        assert_eq!(
            parse_local_http_response(&raw).err(),
            Some("LOCAL_RECOVERY_RESPONSE_REJECTED")
        );
    }

    struct FakeControl {
        exited: AtomicBool,
        healthy: AtomicBool,
        stopped: AtomicBool,
        block_stop: AtomicBool,
        stop_entered: AtomicBool,
        release_stop: AtomicBool,
    }

    impl FakeControl {
        fn healthy() -> Arc<Self> {
            Arc::new(Self {
                exited: AtomicBool::new(false),
                healthy: AtomicBool::new(true),
                stopped: AtomicBool::new(false),
                block_stop: AtomicBool::new(false),
                stop_entered: AtomicBool::new(false),
                release_stop: AtomicBool::new(false),
            })
        }
    }

    struct FakeService {
        control: Arc<FakeControl>,
    }

    impl ManagedService for FakeService {
        fn poll_exit(&mut self) -> Result<Option<i32>, &'static str> {
            Ok(self.control.exited.load(Ordering::SeqCst).then_some(1))
        }

        fn verify_health(&self) -> Result<(), &'static str> {
            self.control
                .healthy
                .load(Ordering::SeqCst)
                .then_some(())
                .ok_or("LOCAL_HEALTH_FAILED")
        }

        fn prepare_recovery_request(
            &self,
            _scope: &str,
            _capability: &str,
            _issued_at: u64,
        ) -> Result<RecoveryRequestContext, &'static str> {
            Err("LOCAL_TEST_RECOVERY_UNAVAILABLE")
        }

        fn stop(self: Box<Self>, _timeout: Duration) -> Result<(), &'static str> {
            if self.control.block_stop.load(Ordering::SeqCst) {
                self.control.stop_entered.store(true, Ordering::SeqCst);
                while !self.control.release_stop.load(Ordering::SeqCst) {
                    thread::sleep(Duration::from_millis(1));
                }
            }
            self.control.stopped.store(true, Ordering::SeqCst);
            Ok(())
        }
    }

    enum LaunchPlan {
        Service(Arc<FakeControl>),
        Error(&'static str),
    }

    struct FakeLauncher {
        plans: Mutex<VecDeque<LaunchPlan>>,
        credentials: Mutex<Vec<(String, String)>>,
        delay: Duration,
    }

    struct GatedLauncher {
        reserved: AtomicBool,
        release: AtomicBool,
        spawned: AtomicBool,
    }

    impl GatedLauncher {
        fn new() -> Arc<Self> {
            Arc::new(Self {
                reserved: AtomicBool::new(false),
                release: AtomicBool::new(false),
                spawned: AtomicBool::new(false),
            })
        }
    }

    impl ServiceLauncher for GatedLauncher {
        fn launch(
            &self,
            _credentials: AppCredentials,
            _startup_timeout: Duration,
            permit: &SpawnPermit,
        ) -> Result<Box<dyn ManagedService>, &'static str> {
            self.reserved.store(true, Ordering::SeqCst);
            while !self.release.load(Ordering::SeqCst) {
                thread::sleep(Duration::from_millis(1));
            }
            let _spawn_gate = permit.acquire()?;
            self.spawned.store(true, Ordering::SeqCst);
            Err("LOCAL_TEST_LAUNCH_FINISHED")
        }
    }

    impl FakeLauncher {
        fn new(plans: Vec<LaunchPlan>, delay: Duration) -> Arc<Self> {
            Arc::new(Self {
                plans: Mutex::new(plans.into()),
                credentials: Mutex::new(Vec::new()),
                delay,
            })
        }

        fn launch_count(&self) -> usize {
            self.credentials.lock().expect("credentials").len()
        }
    }

    impl ServiceLauncher for FakeLauncher {
        fn launch(
            &self,
            credentials: AppCredentials,
            _startup_timeout: Duration,
            permit: &SpawnPermit,
        ) -> Result<Box<dyn ManagedService>, &'static str> {
            let _spawn_gate = permit.acquire()?;
            self.credentials.lock().expect("credentials").push((
                credentials.app_instance_id().to_owned(),
                credentials.root_secret_hex(),
            ));
            thread::sleep(self.delay);
            match self.plans.lock().expect("plans").pop_front() {
                Some(LaunchPlan::Service(control)) => Ok(Box::new(FakeService { control })),
                Some(LaunchPlan::Error(code)) => Err(code),
                None => Err("LOCAL_TEST_PLAN_EXHAUSTED"),
            }
        }
    }

    fn timing() -> ManagerTiming {
        ManagerTiming {
            startup_timeout: Duration::from_millis(100),
            shutdown_timeout: Duration::from_millis(20),
            monitor_interval: Duration::from_millis(5),
            recovery_timeout: Duration::from_millis(100),
        }
    }

    fn wait_for_state(manager: &LocalServiceManager, expected: &str) -> LocalServiceState {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let state = manager.status();
            if state.state() == expected {
                return state;
            }
            assert!(Instant::now() < deadline, "state did not become {expected}");
            thread::sleep(Duration::from_millis(5));
        }
    }

    fn wait_for_flag(flag: &AtomicBool) {
        let deadline = Instant::now() + Duration::from_secs(1);
        while !flag.load(Ordering::SeqCst) {
            assert!(Instant::now() < deadline, "flag did not become true");
            thread::sleep(Duration::from_millis(1));
        }
    }

    #[test]
    fn async_start_returns_starting_and_duplicate_start_is_suppressed() {
        let control = FakeControl::healthy();
        let launcher = FakeLauncher::new(
            vec![LaunchPlan::Service(control)],
            Duration::from_millis(50),
        );
        let manager = LocalServiceManager::with_launcher_and_timing(launcher.clone(), timing());

        let started = Instant::now();
        assert_eq!(manager.start_async().state(), "starting");
        assert!(started.elapsed() < Duration::from_millis(20));
        assert_eq!(manager.start_async().state(), "starting");
        assert_eq!(wait_for_state(&manager, "ready").state(), "ready");
        assert_eq!(launcher.launch_count(), 1);
    }

    #[test]
    fn monitor_marks_ready_service_unavailable_after_exit_or_health_failure() {
        let exited = FakeControl::healthy();
        let unhealthy = FakeControl::healthy();
        let launcher = FakeLauncher::new(
            vec![
                LaunchPlan::Service(exited.clone()),
                LaunchPlan::Service(unhealthy.clone()),
            ],
            Duration::ZERO,
        );
        let manager = LocalServiceManager::with_launcher_and_timing(launcher, timing());

        manager.start_async();
        wait_for_state(&manager, "ready");
        exited.exited.store(true, Ordering::SeqCst);
        assert_eq!(
            wait_for_state(&manager, "unavailable").error_code(),
            Some("LOCAL_SERVICE_EXITED")
        );

        manager.retry_async();
        wait_for_state(&manager, "ready");
        unhealthy.healthy.store(false, Ordering::SeqCst);
        assert_eq!(
            wait_for_state(&manager, "unavailable").error_code(),
            Some("LOCAL_HEALTH_FAILED")
        );
        assert!(unhealthy.stopped.load(Ordering::SeqCst));
    }

    #[test]
    fn retry_cleans_previous_service_and_uses_new_credentials() {
        let first = FakeControl::healthy();
        let second = FakeControl::healthy();
        let launcher = FakeLauncher::new(
            vec![
                LaunchPlan::Service(first.clone()),
                LaunchPlan::Service(second),
            ],
            Duration::ZERO,
        );
        let manager = LocalServiceManager::with_launcher_and_timing(launcher.clone(), timing());

        manager.start_async();
        wait_for_state(&manager, "ready");
        assert_eq!(manager.retry_async().state(), "retrying");
        wait_for_state(&manager, "ready");
        assert!(first.stopped.load(Ordering::SeqCst));
        let credentials = launcher.credentials.lock().expect("credentials");
        assert_eq!(credentials.len(), 2);
        assert_ne!(credentials[0], credentials[1]);
    }

    #[test]
    fn shutdown_while_retry_stop_is_blocked_prevents_relaunch() {
        let first = FakeControl::healthy();
        first.block_stop.store(true, Ordering::SeqCst);
        let second = FakeControl::healthy();
        let launcher = FakeLauncher::new(
            vec![
                LaunchPlan::Service(first.clone()),
                LaunchPlan::Service(second),
            ],
            Duration::ZERO,
        );
        let manager = LocalServiceManager::with_launcher_and_timing(launcher.clone(), timing());

        manager.start_async();
        wait_for_state(&manager, "ready");
        manager.retry_async();
        wait_for_flag(&first.stop_entered);
        manager.shutdown();
        first.release_stop.store(true, Ordering::SeqCst);

        let deadline = Instant::now() + Duration::from_millis(200);
        while Instant::now() < deadline && launcher.launch_count() == 1 {
            thread::sleep(Duration::from_millis(1));
        }
        assert_eq!(
            launcher.launch_count(),
            1,
            "shutdown must cancel the reserved retry before a new launch"
        );
    }

    #[test]
    fn shutdown_after_start_reservation_prevents_process_spawn() {
        let launcher = GatedLauncher::new();
        let manager = LocalServiceManager::with_launcher_and_timing(launcher.clone(), timing());

        manager.start_async();
        wait_for_flag(&launcher.reserved);
        manager.shutdown();
        launcher.release.store(true, Ordering::SeqCst);

        let deadline = Instant::now() + Duration::from_millis(200);
        while Instant::now() < deadline && !launcher.spawned.load(Ordering::SeqCst) {
            thread::sleep(Duration::from_millis(1));
        }
        assert!(
            !launcher.spawned.load(Ordering::SeqCst),
            "shutdown must cancel a reserved launch before process spawn"
        );
    }

    #[test]
    fn shutdown_releases_spawn_gate_before_waiting_for_service_stop() {
        let control = FakeControl::healthy();
        control.block_stop.store(true, Ordering::SeqCst);
        let launcher =
            FakeLauncher::new(vec![LaunchPlan::Service(control.clone())], Duration::ZERO);
        let manager = LocalServiceManager::with_launcher_and_timing(launcher, timing());
        manager.start_async();
        wait_for_state(&manager, "ready");

        let shutdown_manager = manager.clone();
        let shutdown = thread::spawn(move || shutdown_manager.shutdown());
        wait_for_flag(&control.stop_entered);
        assert!(
            manager.inner.spawn_gate.try_lock().is_ok(),
            "shutdown must not retain the spawn gate during process I/O"
        );
        control.release_stop.store(true, Ordering::SeqCst);
        shutdown.join().expect("shutdown thread");
    }

    #[test]
    fn startup_failure_is_unavailable_and_shutdown_prevents_restart() {
        let launcher = FakeLauncher::new(
            vec![LaunchPlan::Error("LOCAL_SERVICE_START_TIMEOUT")],
            Duration::ZERO,
        );
        let manager = LocalServiceManager::with_launcher_and_timing(launcher.clone(), timing());
        manager.start_async();
        assert_eq!(
            wait_for_state(&manager, "unavailable").error_code(),
            Some("LOCAL_SERVICE_START_TIMEOUT")
        );
        manager.shutdown();
        assert_eq!(
            manager.start_async().error_code(),
            Some("LOCAL_SERVICE_STOPPED")
        );
        assert_eq!(launcher.launch_count(), 1);
    }

    #[cfg(windows)]
    #[test]
    fn job_tree_fixture_child() {
        let role = match std::env::var("DAON_JOB_FIXTURE_ROLE") {
            Ok(role) => role,
            Err(_) => return,
        };
        let port: u16 = std::env::var("DAON_JOB_FIXTURE_PORT")
            .expect("fixture port")
            .parse()
            .expect("numeric fixture port");
        if role == "listener" {
            let listener =
                TcpListener::bind((Ipv4Addr::LOCALHOST, port)).expect("bind fixture listener");
            loop {
                let _ = listener.accept();
            }
        }
        assert_eq!(role, "parent");
        let executable = std::env::current_exe().expect("fixture executable");
        let mut descendant = Command::new(executable)
            .args([
                "--exact",
                "local_service::manager_tests::job_tree_fixture_child",
                "--nocapture",
            ])
            .env("DAON_JOB_FIXTURE_ROLE", "listener")
            .env("DAON_JOB_FIXTURE_PORT", port.to_string())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn fixture descendant");
        loop {
            match descendant.try_wait() {
                Ok(Some(_)) | Err(_) => break,
                Ok(None) => thread::sleep(Duration::from_millis(50)),
            }
        }
    }

    #[cfg(windows)]
    fn wait_for_fixture_listener(port: u16, expected_open: bool) {
        let address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let open =
                TcpStream::connect_timeout(&address.into(), Duration::from_millis(100)).is_ok();
            if open == expected_open {
                return;
            }
            assert!(
                Instant::now() < deadline,
                "fixture listener open={open}, expected {expected_open}"
            );
            thread::sleep(Duration::from_millis(25));
        }
    }

    #[cfg(windows)]
    #[test]
    fn job_object_termination_kills_stubborn_descendant_listener() {
        let probe = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("reserve fixture port");
        let port = probe.local_addr().expect("fixture address").port();
        drop(probe);

        let executable = std::env::current_exe().expect("fixture executable");
        let mut command = Command::new(executable);
        command
            .args([
                "--exact",
                "local_service::manager_tests::job_tree_fixture_child",
                "--nocapture",
            ])
            .env("DAON_JOB_FIXTURE_ROLE", "parent")
            .env("DAON_JOB_FIXTURE_PORT", port.to_string())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let (mut child, job) =
            spawn_suspended_in_job(&mut command).expect("spawn fixture inside job");
        wait_for_fixture_listener(port, true);

        job.terminate().expect("terminate fixture job");
        let mut process = RunningTermination {
            child: &mut child,
            job: &job,
        };
        assert_eq!(
            wait_for_process_exit(&mut process, &SystemStopClock, Duration::from_secs(5)),
            Ok(true)
        );
        wait_for_fixture_listener(port, false);
    }

    #[cfg(windows)]
    struct ScriptedTermination {
        waits: Mutex<VecDeque<Result<bool, &'static str>>>,
        job_result: Result<(), &'static str>,
        kill_result: Result<(), &'static str>,
        job_calls: AtomicUsize,
        kill_calls: AtomicUsize,
    }

    #[cfg(windows)]
    impl ProcessTermination for ScriptedTermination {
        fn has_exited(&mut self) -> Result<bool, &'static str> {
            self.waits
                .lock()
                .expect("wait plan")
                .pop_front()
                .unwrap_or(Ok(false))
        }

        fn terminate_job(&mut self) -> Result<(), &'static str> {
            self.job_calls.fetch_add(1, Ordering::SeqCst);
            self.job_result
        }

        fn kill_child(&mut self) -> Result<(), &'static str> {
            self.kill_calls.fetch_add(1, Ordering::SeqCst);
            self.kill_result
        }
    }

    #[cfg(windows)]
    struct ScriptedClock {
        origin: Instant,
        elapsed_millis: Mutex<u64>,
    }

    #[cfg(windows)]
    impl ScriptedClock {
        fn new() -> Self {
            Self {
                origin: Instant::now(),
                elapsed_millis: Mutex::new(0),
            }
        }
    }

    #[cfg(windows)]
    impl StopClock for ScriptedClock {
        fn now(&self) -> Instant {
            self.origin + Duration::from_millis(*self.elapsed_millis.lock().expect("clock"))
        }

        fn sleep(&self, duration: Duration) {
            let millis = duration.as_millis().max(1) as u64;
            *self.elapsed_millis.lock().expect("clock") += millis;
        }
    }

    #[cfg(windows)]
    fn scripted_termination(
        waits: Vec<Result<bool, &'static str>>,
        job_result: Result<(), &'static str>,
        kill_result: Result<(), &'static str>,
    ) -> ScriptedTermination {
        ScriptedTermination {
            waits: Mutex::new(waits.into()),
            job_result,
            kill_result,
            job_calls: AtomicUsize::new(0),
            kill_calls: AtomicUsize::new(0),
        }
    }

    #[cfg(windows)]
    #[test]
    fn failed_job_termination_falls_back_to_child_kill_and_bounded_wait() {
        let mut process = scripted_termination(
            vec![Ok(false), Ok(false), Ok(true)],
            Err("LOCAL_JOB_TERMINATE_FAILED"),
            Ok(()),
        );

        assert_eq!(
            stop_process(
                &mut process,
                &ScriptedClock::new(),
                Duration::from_millis(1)
            ),
            Ok(())
        );
        assert_eq!(process.job_calls.load(Ordering::SeqCst), 1);
        assert_eq!(process.kill_calls.load(Ordering::SeqCst), 1);
    }

    #[cfg(windows)]
    #[test]
    fn successful_job_termination_with_live_child_uses_kill_fallback() {
        let mut process = scripted_termination(
            vec![Ok(false), Ok(false), Ok(false), Ok(false), Ok(true)],
            Ok(()),
            Ok(()),
        );

        assert_eq!(
            stop_process(
                &mut process,
                &ScriptedClock::new(),
                Duration::from_millis(1)
            ),
            Ok(())
        );
        assert_eq!(process.job_calls.load(Ordering::SeqCst), 1);
        assert_eq!(process.kill_calls.load(Ordering::SeqCst), 1);
    }

    #[cfg(windows)]
    #[test]
    fn child_kill_or_final_wait_failure_returns_stable_error_without_blocking() {
        let mut kill_failed = scripted_termination(
            vec![Ok(false), Ok(false)],
            Err("LOCAL_JOB_TERMINATE_FAILED"),
            Err("LOCAL_CHILD_KILL_FAILED"),
        );
        assert_eq!(
            stop_process(
                &mut kill_failed,
                &ScriptedClock::new(),
                Duration::from_millis(1)
            ),
            Err("LOCAL_CHILD_KILL_FAILED")
        );

        let mut wait_failed = scripted_termination(
            vec![Ok(false), Ok(false), Err("LOCAL_SERVICE_WAIT_FAILED")],
            Err("LOCAL_JOB_TERMINATE_FAILED"),
            Ok(()),
        );
        assert_eq!(
            stop_process(
                &mut wait_failed,
                &ScriptedClock::new(),
                Duration::from_millis(1)
            ),
            Err("LOCAL_SERVICE_WAIT_FAILED")
        );
    }

    #[cfg(windows)]
    fn append_fixture_marker(kind: &str, value: &str) {
        use std::fs::OpenOptions;
        let path = std::env::var("DAON_MANAGER_FIXTURE_MARKER").expect("fixture marker");
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .expect("open fixture marker");
        writeln!(file, "{kind}={value}").expect("write fixture marker");
    }

    #[cfg(windows)]
    fn fixture_launch_index() -> usize {
        let path = std::env::var("DAON_MANAGER_FIXTURE_MARKER").expect("fixture marker");
        std::fs::read_to_string(path)
            .unwrap_or_default()
            .lines()
            .filter(|line| line.starts_with("credential="))
            .count()
    }

    #[cfg(windows)]
    fn serve_fixture_health(port: u16, ready: bool) {
        let listener =
            TcpListener::bind((Ipv4Addr::LOCALHOST, port)).expect("bind manager fixture");
        for incoming in listener.incoming() {
            let mut stream = match incoming {
                Ok(stream) => stream,
                Err(_) => return,
            };
            let mut request = [0_u8; 8192];
            let _ = stream.read(&mut request);
            let body = if ready {
                r#"{"status":"ready"}"#
            } else {
                r#"{"status":"unavailable"}"#
            };
            let status = if ready {
                "HTTP/1.1 200 OK"
            } else {
                "HTTP/1.1 503 Service Unavailable"
            };
            let response = format!(
                "{status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        }
    }

    #[cfg(windows)]
    #[test]
    fn manager_error_fixture_child() {
        let mode = match std::env::var("DAON_MANAGER_FIXTURE_MODE") {
            Ok(mode) => mode,
            Err(_) => return,
        };
        let role =
            std::env::var("DAON_MANAGER_FIXTURE_ROLE").unwrap_or_else(|_| "parent".to_owned());
        let port: u16 = std::env::var("DAON_MANAGER_FIXTURE_PORT")
            .expect("fixture port")
            .parse()
            .expect("numeric fixture port");
        if role == "listener" {
            append_fixture_marker("listener", &std::process::id().to_string());
            serve_fixture_health(port, true);
            return;
        }

        append_fixture_marker("parent", &std::process::id().to_string());
        let mut stdin = BufReader::new(std::io::stdin());
        let mut bootstrap = String::new();
        stdin.read_line(&mut bootstrap).expect("fixture bootstrap");
        let bootstrap: serde_json::Value =
            serde_json::from_str(&bootstrap).expect("fixture bootstrap json");
        let app_instance_id = bootstrap["app_instance_id"]
            .as_str()
            .expect("fixture app instance");
        let launch_index = fixture_launch_index();
        append_fixture_marker("credential", app_instance_id);

        if mode == "no_ready" {
            thread::sleep(Duration::from_secs(30));
            return;
        }
        if mode == "invalid_then_ready" && launch_index == 0 {
            println!(
                r#"{{"event":"Ready","protocol_version":"0","app_instance_id":"invalid","port":{port}}}"#
            );
            return;
        }

        let health_ready = !(mode == "health_fail_then_ready" && launch_index == 0);
        if mode == "stubborn_tree" {
            let executable = std::env::current_exe().expect("fixture executable");
            let descendant = Command::new(executable)
                .args([
                    "--exact",
                    "local_service::manager_tests::manager_error_fixture_child",
                    "--nocapture",
                ])
                .env("DAON_MANAGER_FIXTURE_MODE", "listener")
                .env("DAON_MANAGER_FIXTURE_ROLE", "listener")
                .env("DAON_MANAGER_FIXTURE_PORT", port.to_string())
                .env(
                    "DAON_MANAGER_FIXTURE_MARKER",
                    std::env::var("DAON_MANAGER_FIXTURE_MARKER").expect("fixture marker"),
                )
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .expect("spawn stubborn descendant");
            append_fixture_marker("descendant", &descendant.id().to_string());
            wait_for_fixture_listener(port, true);
        } else {
            thread::spawn(move || serve_fixture_health(port, health_ready));
            wait_for_fixture_listener(port, true);
        }

        println!(
            "{}",
            serde_json::json!({
                "event": "Ready",
                "protocol_version": PROTOCOL_VERSION,
                "app_instance_id": app_instance_id,
                "port": port,
            })
        );
        std::io::stdout().flush().expect("flush fixture ready");
        if mode == "stubborn_tree" {
            loop {
                thread::sleep(Duration::from_secs(1));
            }
        }
        let mut remaining = Vec::new();
        let _ = stdin.read_to_end(&mut remaining);
    }

    #[cfg(windows)]
    fn reserve_fixture_port() -> u16 {
        let probe = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("reserve manager port");
        probe.local_addr().expect("manager fixture address").port()
    }

    #[cfg(windows)]
    fn manager_fixture(mode: &str, port: u16, marker: &std::path::Path) -> LocalServiceManager {
        let executable = std::env::var_os("PATH")
            .into_iter()
            .flat_map(|value| std::env::split_paths(&value).collect::<Vec<_>>())
            .map(|directory| directory.join("node.exe"))
            .find(|candidate| candidate.is_file())
            .expect("node fixture executable");
        let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/local-service-error-fixture.mjs");
        LocalServiceManager::with_launcher_and_timing(
            Arc::new(RealServiceLauncher {
                executable: Some(executable),
                executable_args: vec![fixture.to_string_lossy().into_owned()],
                environment: vec![
                    ("DAON_MANAGER_FIXTURE_MODE".to_owned(), mode.to_owned()),
                    ("DAON_MANAGER_FIXTURE_PORT".to_owned(), port.to_string()),
                    (
                        "DAON_MANAGER_FIXTURE_MARKER".to_owned(),
                        marker.to_string_lossy().into_owned(),
                    ),
                ],
            }),
            ManagerTiming {
                // Node fixture startup can exceed 250 ms on a loaded Windows host.
                // Keep the test bounded while matching the production owner's patient wait policy.
                startup_timeout: Duration::from_secs(2),
                shutdown_timeout: Duration::from_millis(250),
                monitor_interval: Duration::from_millis(10),
                recovery_timeout: Duration::from_millis(250),
            },
        )
    }

    #[cfg(windows)]
    struct FixtureMarker(PathBuf);

    #[cfg(windows)]
    impl std::ops::Deref for FixtureMarker {
        type Target = std::path::Path;

        fn deref(&self) -> &Self::Target {
            &self.0
        }
    }

    #[cfg(windows)]
    impl Drop for FixtureMarker {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    #[cfg(windows)]
    fn new_fixture_marker(port: u16) -> FixtureMarker {
        let marker = std::env::temp_dir().join(format!(
            "daon-manager-fixture-{}-{port}.txt",
            std::process::id()
        ));
        let _ = std::fs::remove_file(&marker);
        FixtureMarker(marker)
    }

    #[cfg(windows)]
    fn process_is_running(process_id: u32) -> bool {
        use windows_sys::Win32::Foundation::{CloseHandle, WAIT_TIMEOUT};
        use windows_sys::Win32::System::Threading::{
            OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE,
        };
        // SAFETY: The process id was written by the fixture; no handle inheritance is requested.
        let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, process_id) };
        if handle.is_null() {
            return false;
        }
        // SAFETY: `handle` is valid for synchronization and remains live for this call.
        let result = unsafe { WaitForSingleObject(handle, 0) };
        // SAFETY: `handle` is uniquely owned by this scope.
        let _ = unsafe { CloseHandle(handle) };
        result == WAIT_TIMEOUT
    }

    #[cfg(windows)]
    fn wait_for_fixture_processes_stopped(marker: &std::path::Path) {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let process_ids: Vec<u32> = std::fs::read_to_string(marker)
                .unwrap_or_default()
                .lines()
                .filter_map(|line| {
                    let (kind, value) = line.split_once('=')?;
                    matches!(kind, "parent" | "descendant" | "listener")
                        .then(|| value.parse().ok())
                        .flatten()
                })
                .collect();
            if process_ids
                .iter()
                .all(|process_id| !process_is_running(*process_id))
            {
                match std::fs::remove_file(marker) {
                    Ok(()) => return,
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => return,
                    Err(error) => panic!("failed to remove fixture marker: {error}"),
                }
            }
            assert!(
                Instant::now() < deadline,
                "fixture processes still running: {process_ids:?}"
            );
            thread::sleep(Duration::from_millis(25));
        }
    }

    #[cfg(windows)]
    fn fixture_credentials(marker: &std::path::Path) -> Vec<String> {
        std::fs::read_to_string(marker)
            .unwrap_or_default()
            .lines()
            .filter_map(|line| line.strip_prefix("credential=").map(str::to_owned))
            .collect()
    }

    #[cfg(windows)]
    #[test]
    fn production_manager_error_fixtures_are_bounded_and_leave_no_processes() {
        let no_ready_port = reserve_fixture_port();
        let no_ready_marker = new_fixture_marker(no_ready_port);
        let no_ready = manager_fixture("no_ready", no_ready_port, &no_ready_marker);
        no_ready.start_async();
        assert_eq!(
            wait_for_state(&no_ready, "unavailable").error_code(),
            Some("LOCAL_SERVICE_START_TIMEOUT")
        );
        wait_for_fixture_processes_stopped(&no_ready_marker);
        wait_for_fixture_listener(no_ready_port, false);
        assert!(
            !no_ready_marker.exists(),
            "no-ready fixture marker must be removed after process cleanup"
        );

        let invalid_port = reserve_fixture_port();
        let invalid_marker = new_fixture_marker(invalid_port);
        let invalid = manager_fixture("invalid_then_ready", invalid_port, &invalid_marker);
        invalid.start_async();
        assert_eq!(
            wait_for_state(&invalid, "unavailable").error_code(),
            Some("LOCAL_SERVICE_READY_REJECTED")
        );
        invalid.retry_async();
        wait_for_state(&invalid, "ready");
        let credentials = fixture_credentials(&invalid_marker);
        assert_eq!(credentials.len(), 2);
        assert_ne!(credentials[0], credentials[1]);
        invalid.shutdown();
        wait_for_fixture_processes_stopped(&invalid_marker);
        wait_for_fixture_listener(invalid_port, false);
        assert!(
            !invalid_marker.exists(),
            "invalid-ready fixture marker must be removed after process cleanup"
        );

        let health_port = reserve_fixture_port();
        let health_marker = new_fixture_marker(health_port);
        let health = manager_fixture("health_fail_then_ready", health_port, &health_marker);
        health.start_async();
        assert_eq!(
            wait_for_state(&health, "unavailable").error_code(),
            Some("LOCAL_HEALTH_REJECTED")
        );
        health.retry_async();
        wait_for_state(&health, "ready");
        let credentials = fixture_credentials(&health_marker);
        assert_eq!(credentials.len(), 2);
        assert_ne!(credentials[0], credentials[1]);
        health.shutdown();
        wait_for_fixture_processes_stopped(&health_marker);
        wait_for_fixture_listener(health_port, false);
        assert!(
            !health_marker.exists(),
            "health-failure fixture marker must be removed after process cleanup"
        );

        let stubborn_port = reserve_fixture_port();
        let stubborn_marker = new_fixture_marker(stubborn_port);
        let stubborn = manager_fixture("stubborn_tree", stubborn_port, &stubborn_marker);
        stubborn.start_async();
        wait_for_state(&stubborn, "ready");
        stubborn.shutdown();
        wait_for_fixture_processes_stopped(&stubborn_marker);
        wait_for_fixture_listener(stubborn_port, false);
        assert!(
            !stubborn_marker.exists(),
            "stubborn-tree fixture marker must be removed after process cleanup"
        );

        for _ in 0..3 {
            let race_port = reserve_fixture_port();
            let race_marker = new_fixture_marker(race_port);
            let race = manager_fixture("ready", race_port, &race_marker);
            race.start_async();
            wait_for_state(&race, "ready");
            race.retry_async();
            race.shutdown();
            wait_for_fixture_processes_stopped(&race_marker);
            wait_for_fixture_listener(race_port, false);
            assert!(
                !race_marker.exists(),
                "race fixture marker must be removed after process cleanup"
            );
        }
    }
}
