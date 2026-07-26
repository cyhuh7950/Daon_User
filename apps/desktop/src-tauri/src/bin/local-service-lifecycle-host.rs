use daon_user_desktop_lib::local_service::LocalServiceManager;
use serde::Serialize;
use std::path::PathBuf;
use std::thread;
use std::time::{Duration, Instant};

const STATE_TIMEOUT: Duration = Duration::from_secs(20);

#[derive(Serialize)]
struct RunEvidence {
    start: &'static str,
    first_ready: &'static str,
    retry: &'static str,
    second_ready: &'static str,
    shutdown: &'static str,
}

#[derive(Serialize)]
struct HostEvidence {
    schema_version: &'static str,
    owner: &'static str,
    runs: Vec<RunEvidence>,
    secrets_emitted: bool,
}

fn wait_for_state(
    manager: &LocalServiceManager,
    expected: &'static str,
) -> Result<&'static str, String> {
    let deadline = Instant::now() + STATE_TIMEOUT;
    loop {
        let status = manager.status();
        if status.state() == expected {
            return Ok(expected);
        }
        if status.state() == "unavailable" {
            return Err(status.error_code().unwrap_or("LOCAL_SERVICE_UNAVAILABLE").to_owned());
        }
        if Instant::now() >= deadline {
            return Err("LOCAL_MANAGER_STATE_TIMEOUT".to_owned());
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn run_lifecycle(executable: PathBuf) -> Result<RunEvidence, String> {
    let manager = LocalServiceManager::with_sidecar_path(executable);
    let start = manager.start_async().state();
    let first_ready = wait_for_state(&manager, "ready")?;
    let retry = manager.retry_async().state();
    let second_ready = wait_for_state(&manager, "ready")?;
    manager.shutdown();
    let shutdown = manager.status().state();
    if shutdown != "unavailable" {
        return Err("LOCAL_MANAGER_SHUTDOWN_STATE_INVALID".to_owned());
    }
    Ok(RunEvidence {
        start,
        first_ready,
        retry,
        second_ready,
        shutdown,
    })
}

fn main() {
    let executable = std::env::var_os("DAON_LOCAL_SERVICE_SIDECAR")
        .map(PathBuf::from)
        .filter(|path| path.is_file())
        .unwrap_or_else(|| {
            eprintln!("LOCAL_MANAGER_HOST_ERROR LOCAL_SERVICE_BINARY_MISSING");
            std::process::exit(2);
        });
    let result = (0..2)
        .map(|_| run_lifecycle(executable.clone()))
        .collect::<Result<Vec<_>, _>>();
    match result {
        Ok(runs) => {
            let evidence = HostEvidence {
                schema_version: "1.0",
                owner: "rust_manager_headless_host",
                runs,
                secrets_emitted: false,
            };
            println!(
                "LOCAL_MANAGER_HOST_EVIDENCE {}",
                serde_json::to_string(&evidence).expect("serialize host evidence")
            );
        }
        Err(error) => {
            eprintln!("LOCAL_MANAGER_HOST_ERROR {error}");
            std::process::exit(1);
        }
    }
}
