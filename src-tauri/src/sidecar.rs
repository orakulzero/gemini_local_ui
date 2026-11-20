//! Win32 Job Object sidecar supervisor (`CREATE_NO_WINDOW`, one-time API token & `/health` probe).

use agy_ultra_acp::win32_job::Win32JobGuard;
use anyhow::{Context, Result, anyhow};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};

pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const LISTENING_PREFIX: &str = "GEMINI_SIDECAR_LISTENING=";

/// Generate a 64-character lowercase hex one-time API token via OS CSPRNG UUIDv4s.
pub fn generate_one_time_api_token() -> String {
    format!("{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple())
}

#[derive(Debug, Clone)]
pub struct SidecarConfig {
    pub command: PathBuf,
    pub args: Vec<String>,
    pub work_dir: PathBuf,
    pub api_token: String,
    pub max_restarts: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunningSidecarInfo {
    pub sidecar_pid: u32,
    pub base_url: String,
    pub api_token: String,
    pub job_bound: bool,
}

/// Manages the Python FastAPI sidecar bound to a Win32 Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).
pub struct SidecarSupervisor {
    job_guard: Arc<Win32JobGuard>,
    child: Option<Child>,
    info: Option<RunningSidecarInfo>,
}

impl Default for SidecarSupervisor {
    fn default() -> Self {
        Self::new()
    }
}

impl SidecarSupervisor {
    pub fn new() -> Self {
        Self {
            job_guard: Arc::new(Win32JobGuard::new()),
            child: None,
            info: None,
        }
    }

    pub fn with_job_guard(job_guard: Arc<Win32JobGuard>) -> Self {
        Self { job_guard, child: None, info: None }
    }

    pub fn job_guard(&self) -> Arc<Win32JobGuard> {
        self.job_guard.clone()
    }

    pub fn compute_backoff_ms(attempt: u32) -> u64 {
        250u64 * (1u64 << attempt.min(4))
    }

    pub fn current_info(&self) -> Option<&RunningSidecarInfo> {
        self.info.as_ref()
    }

    pub async fn spawn_and_wait_ready(
        &mut self,
        config: &SidecarConfig,
        startup_timeout: Duration,
    ) -> Result<RunningSidecarInfo> {
        self.shutdown().await;

        let mut cmd = Command::new(&config.command);
        cmd.args(&config.args)
            .current_dir(&config.work_dir)
            .env("GEMINI_LOCAL_API_TOKEN", &config.api_token)
            .env("PYTHONUNBUFFERED", "1")
            .kill_on_drop(true)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = cmd
            .spawn()
            .with_context(|| format!("failed spawning sidecar {}", config.command.display()))?;
        let sidecar_pid = child
            .id()
            .ok_or_else(|| anyhow!("sidecar exited immediately without PID"))?;
        let job_bound = self.job_guard.assign_pid(sidecar_pid);

        let stdout = child.stdout.take().ok_or_else(|| anyhow!("sidecar stdout missing"))?;
        let stderr = child.stderr.take().ok_or_else(|| anyhow!("sidecar stderr missing"))?;
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(_)) = lines.next_line().await {}
        });

        let mut stdout_lines = BufReader::new(stdout).lines();
        let deadline = tokio::time::Instant::now() + startup_timeout;
        let base_url = loop {
            let rem = deadline.saturating_duration_since(tokio::time::Instant::now());
            if rem.is_zero() {
                let _ = child.kill().await;
                return Err(anyhow!("timed out waiting for {LISTENING_PREFIX} banner"));
            }
            match tokio::time::timeout(rem, stdout_lines.next_line()).await {
                Ok(Ok(Some(line))) => {
                    if let Some(rest) = line.trim().strip_prefix(LISTENING_PREFIX) {
                        break rest.trim().trim_end_matches('/').to_string();
                    }
                }
                Ok(Ok(None)) => {
                    let status = child.wait().await.ok();
                    return Err(anyhow!("sidecar exited before ready banner (status={status:?})"));
                }
                Ok(Err(err)) => return Err(anyhow!("error reading sidecar stdout: {err}")),
                Err(_) => {
                    let _ = child.kill().await;
                    return Err(anyhow!("timed out reading sidecar startup banner"));
                }
            }
        };

        tokio::spawn(async move {
            while let Ok(Some(_)) = stdout_lines.next_line().await {}
        });

        if let Err(err) = wait_health_ok(&base_url, &config.api_token, deadline).await {
            let _ = child.kill().await;
            let _ = child.wait().await;
            return Err(err);
        }
        let info = RunningSidecarInfo {
            sidecar_pid,
            base_url,
            api_token: config.api_token.clone(),
            job_bound,
        };
        self.child = Some(child);
        self.info = Some(info.clone());
        Ok(info)
    }

    pub async fn shutdown(&mut self) {
        self.info = None;
        if let Some(mut child) = self.child.take() {
            let _ = child.kill().await;
            let _ = child.wait().await;
        }
    }
}

async fn wait_health_ok(base_url: &str, token: &str, deadline: tokio::time::Instant) -> Result<()> {
    let client = Client::builder().timeout(Duration::from_secs(2)).build()?;
    let health_url = format!("{base_url}/health");
    while tokio::time::Instant::now() < deadline {
        if let Ok(resp) = client
            .get(&health_url)
            .header("X-Gemini-Local-Token", token)
            .send()
            .await
            && resp.status().is_success()
        {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    Err(anyhow!("sidecar /health did not become 200 OK at {health_url}"))
}

/// Poll non-destructively until `path` has zero open OS handles (or does not exist).
pub fn wait_for_file_unlock(path: &std::path::Path, timeout: Duration) -> bool {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        if !path.exists() {
            return true;
        }
        let unlocked = {
            let mut opts = std::fs::OpenOptions::new();
            opts.read(true);
            #[cfg(windows)]
            {
                use std::os::windows::fs::OpenOptionsExt as _;
                opts.share_mode(0);
            }
            match opts.open(path) {
                Ok(_) => true,
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => true,
                Err(_) => false,
            }
        };
        if unlocked {
            return true;
        }
        if std::time::Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}
