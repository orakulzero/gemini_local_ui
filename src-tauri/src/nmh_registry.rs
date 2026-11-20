//! BOM-less Native Messaging Host (NMH) manifest builder & HKCU Chrome/Edge registry installer.

use anyhow::{Context, Result, anyhow};
use serde_json::json;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

pub const NMH_HOST_NAME: &str = "com.orakulzero.gemini_local_ui";
pub const DEFAULT_EXTENSION_ID: &str = "jdihgieaoimpndginklkcjaccejbnmhe";

pub const BROWSER_NMH_VENDORS: [&str; 4] = [
    r"Google\Chrome\NativeMessagingHosts",
    r"Microsoft\Edge\NativeMessagingHosts",
    r"BraveSoftware\Brave-Browser\NativeMessagingHosts",
    r"Vivaldi\NativeMessagingHosts",
];

/// Serialize the Chrome/Edge Native Messaging Host JSON manifest strictly as UTF-8 without BOM.
pub fn build_nmh_manifest_bytes(launcher_path: &Path, extension_ids: &[&str]) -> Vec<u8> {
    let ids: Vec<String> = if extension_ids.is_empty() {
        vec![format!("chrome-extension://{DEFAULT_EXTENSION_ID}/")]
    } else {
        extension_ids
            .iter()
            .map(|raw| {
                let clean = raw.trim().trim_end_matches('/');
                if let Some(stripped) = clean.strip_prefix("chrome-extension://") {
                    format!("chrome-extension://{stripped}/")
                } else {
                    format!("chrome-extension://{clean}/")
                }
            })
            .collect()
    };

    let doc = json!({
        "name": NMH_HOST_NAME,
        "description": "Native messaging host for Gemini Local UI cookie sync",
        "path": launcher_path.to_string_lossy(),
        "type": "stdio",
        "allowed_origins": ids,
    });
    let mut bytes = serde_json::to_vec_pretty(&doc).unwrap_or_default();
    bytes.push(b'\n');
    debug_assert!(!bytes.starts_with(&[0xEF, 0xBB, 0xBF]));
    bytes
}

fn resolve_launcher_in_install_dir(install_dir: &Path) -> PathBuf {
    for rel in [
        Path::new("run_nmh.cmd"),
        Path::new("scripts/run_nmh.cmd"),
        Path::new("gemini-local-ui-desktop.exe"),
    ] {
        let candidate = install_dir.join(rel);
        if candidate.is_file() {
            return candidate;
        }
    }
    install_dir.join("run_nmh.cmd")
}

/// Write BOM-less `com.orakulzero.gemini_local_ui.json` and register Chrome + Edge HKCU keys.
pub fn install_or_update_nmh_manifest(
    install_dir: &Path,
    manifest_dir: &Path,
    extension_ids: &[&str],
    registry_root_subkey: Option<&str>,
) -> Result<PathBuf> {
    fs::create_dir_all(manifest_dir)
        .with_context(|| format!("failed creating NMH manifest dir {}", manifest_dir.display()))?;

    let launcher = resolve_launcher_in_install_dir(install_dir);
    let manifest_path = manifest_dir.join(format!("{NMH_HOST_NAME}.json"));
    let payload = build_nmh_manifest_bytes(&launcher, extension_ids);
    fs::write(&manifest_path, &payload)
        .with_context(|| format!("failed writing NMH manifest {}", manifest_path.display()))?;

    #[cfg(windows)]
    {
        let subkey = registry_root_subkey
            .unwrap_or("Software")
            .trim()
            .trim_matches('\\');
        let manifest_str = manifest_path.to_string_lossy().into_owned();
        for vendor in BROWSER_NMH_VENDORS {
            let key_path = format!(r"HKCU\{subkey}\{vendor}\{NMH_HOST_NAME}");
            let mut cmd = Command::new("reg");
            cmd.args(["add", &key_path, "/ve", "/t", "REG_SZ", "/d", &manifest_str, "/f"]);
            apply_no_window(&mut cmd);
            let out = cmd
                .output()
                .with_context(|| format!("failed invoking reg.exe add for {key_path}"))?;
            if !out.status.success() {
                return Err(anyhow!(
                    "reg add failed for {key_path}: {}",
                    String::from_utf8_lossy(&out.stderr).trim()
                ));
            }
        }
    }
    #[cfg(not(windows))]
    {
        let _ = registry_root_subkey;
    }

    Ok(manifest_path)
}

/// Remove Chrome, Edge, Brave & Vivaldi HKCU Native Messaging Host keys cleanly.
pub fn unregister_nmh_manifest(registry_root_subkey: Option<&str>) -> Result<()> {
    #[cfg(windows)]
    {
        let subkey = registry_root_subkey
            .unwrap_or("Software")
            .trim()
            .trim_matches('\\');
        for vendor in BROWSER_NMH_VENDORS {
            let key_path = format!(r"HKCU\{subkey}\{vendor}\{NMH_HOST_NAME}");
            let mut cmd = Command::new("reg");
            cmd.args(["delete", &key_path, "/f"]);
            apply_no_window(&mut cmd);
            let _ = cmd.output();
        }
        if !subkey.is_empty() && !subkey.eq_ignore_ascii_case("Software") {
            let root_path = format!(r"HKCU\{subkey}");
            let mut clean_cmd = Command::new("reg");
            clean_cmd.args(["delete", &root_path, "/f"]);
            apply_no_window(&mut clean_cmd);
            let _ = clean_cmd.output();
        }
    }
    #[cfg(not(windows))]
    {
        let _ = registry_root_subkey;
    }
    Ok(())
}

#[cfg(windows)]
fn apply_no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt as _;
    cmd.creation_flags(0x0800_0000);
}
