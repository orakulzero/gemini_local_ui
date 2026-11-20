//! Entrypoint for `gemini-local-ui-desktop`: NMH installer CLI & Win32 Job supervisor runtime.

use agy_ultra_acp::config::OrchestratorConfig;
use agy_ultra_acp::orchestrator::Orchestrator;
use agy_ultra_acp::permission_policy::SharedHitlBroker;
use agy_ultra_acp::state_store::StateStore;
use agy_ultra_acp::ws_bridge::{WsBridgeConfig, start_loopback_ws_bridge};
use anyhow::{Context, Result};
use clap::Parser;
use gemini_local_ui_desktop::nmh_registry::{
    DEFAULT_EXTENSION_ID, install_or_update_nmh_manifest, unregister_nmh_manifest,
};
use gemini_local_ui_desktop::sidecar::{
    SidecarConfig, SidecarSupervisor, generate_one_time_api_token,
};
use serde_json::json;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tracing_subscriber::EnvFilter;

#[derive(Parser, Debug)]
#[command(name = "gemini-local-ui-desktop")]
#[command(about = "Gemini Local UI Desktop Supervisor & Native Messaging Host Installer")]
struct Cli {
    /// Install/update Chrome & Edge HKCU Native Messaging Host manifest pointing to <INSTALL_DIR>.
    #[arg(long)]
    register_nmh: Option<PathBuf>,

    /// Remove Chrome & Edge HKCU Native Messaging Host registry keys.
    #[arg(long)]
    unregister_nmh: bool,

    /// Run headless desktop sidecar + ACP WS v1 supervisor (used by E2E Job Object tests & CLI).
    #[arg(long)]
    headless_supervisor: bool,

    /// Python interpreter or packaged sidecar binary used when spawning backend sidecar.
    #[arg(long, default_value = "python")]
    python_cmd: String,

    /// Optional explicit path to `backend/sidecar_entry.py`.
    #[arg(long)]
    sidecar_script: Option<PathBuf>,
}

fn default_local_data_dir() -> PathBuf {
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("USERPROFILE")
                .map(PathBuf::from)
                .map(|p| p.join("AppData").join("Local"))
        })
        .unwrap_or_else(std::env::temp_dir)
        .join("gemini_local_ui")
}

fn resolve_repo_work_dir() -> PathBuf {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if cwd.join("backend").join("sidecar_entry.py").is_file() || cwd.join("backend").is_dir() {
        return cwd;
    }
    if let Some(exe) = std::env::current_exe().ok()
        && let Some(src_tauri) = exe.ancestors().nth(3)
        && let Some(repo) = src_tauri.parent()
        && repo.join("backend").is_dir()
    {
        return repo.to_path_buf();
    }
    cwd
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .with_writer(std::io::stderr)
        .with_ansi(false)
        .init();

    let cli = Cli::parse();

    if cli.unregister_nmh {
        unregister_nmh_manifest(None)?;
        return Ok(());
    }

    if let Some(install_dir) = cli.register_nmh {
        let manifest_dir = default_local_data_dir();
        let written = install_or_update_nmh_manifest(
            &install_dir,
            &manifest_dir,
            &[DEFAULT_EXTENSION_ID],
            None,
        )?;
        println!("{}", written.display());
        return Ok(());
    }

    if cli.headless_supervisor || cfg!(not(feature = "tauri-gui")) {
        return run_supervisor(&cli).await;
    }

    run_supervisor(&cli).await
}

async fn run_supervisor(cli: &Cli) -> Result<()> {
    let work_dir = resolve_repo_work_dir();
    let script_path = cli
        .sidecar_script
        .clone()
        .unwrap_or_else(|| work_dir.join("backend").join("sidecar_entry.py"));
    let api_token = generate_one_time_api_token();

    let mut supervisor = SidecarSupervisor::new();
    let sidecar_cfg = SidecarConfig {
        command: PathBuf::from(&cli.python_cmd),
        args: vec![
            "-u".to_string(),
            script_path.to_string_lossy().into_owned(),
            "--host".to_string(),
            "127.0.0.1".to_string(),
            "--port".to_string(),
            "0".to_string(),
        ],
        work_dir: work_dir.clone(),
        api_token: api_token.clone(),
        max_restarts: 3,
    };

    let sidecar_info = supervisor
        .spawn_and_wait_ready(&sidecar_cfg, Duration::from_secs(20))
        .await
        .context("failed starting Python FastAPI sidecar under Win32JobGuard")?;

    let mut orch_cfg = OrchestratorConfig::from_env();
    orch_cfg.bridge_url = sidecar_info.base_url.clone();
    orch_cfg.auto_start_bridge = false;
    let store = StateStore::open(&orch_cfg.state_dir)?;
    let orchestrator = Arc::new(Orchestrator::new(orch_cfg, store));
    let broker = SharedHitlBroker::new();
    let (ws_addr, ws_shutdown) = start_loopback_ws_bridge(
        WsBridgeConfig {
            bind_addr: "127.0.0.1:0".to_string(),
            expected_token: Some(api_token),
        },
        orchestrator,
        broker,
    )
    .await?;

    let ready = json!({
        "desktop_pid": std::process::id(),
        "sidecar_pid": sidecar_info.sidecar_pid,
        "sidecar_url": sidecar_info.base_url,
        "acp_ws_url": format!("ws://{ws_addr}"),
        "job_bound": sidecar_info.job_bound,
    });
    println!("DESKTOP_SUPERVISOR_READY={ready}");
    std::io::stdout().flush()?;

    let _ = tokio::signal::ctrl_c().await;
    let _ = ws_shutdown.send(());
    supervisor.shutdown().await;
    Ok(())
}
