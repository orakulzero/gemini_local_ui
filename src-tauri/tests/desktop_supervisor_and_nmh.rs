//! Milestone M9 & v2.1 Hardening Tests for `gemini-local-ui-desktop` (`src-tauri`).

use gemini_local_ui_desktop::nmh_registry::{
    BROWSER_NMH_VENDORS, DEFAULT_EXTENSION_ID, NMH_HOST_NAME, build_nmh_manifest_bytes,
    install_or_update_nmh_manifest, unregister_nmh_manifest,
};
use gemini_local_ui_desktop::sidecar::{
    SidecarSupervisor, generate_one_time_api_token, wait_for_file_unlock,
};
use gemini_local_ui_desktop::tray::{CloseAction, TrayController, TrayMenuAction};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

const ISOLATED_REG_SUBKEY: &str = r"Software\GeminiLocalUiM9Test";

struct RegistryCleanupGuard {
    temp_root: PathBuf,
}

impl Drop for RegistryCleanupGuard {
    fn drop(&mut self) {
        let _ = unregister_nmh_manifest(Some(ISOLATED_REG_SUBKEY));
        let _ = Command::new("reg")
            .args(["delete", &format!(r"HKCU\{ISOLATED_REG_SUBKEY}"), "/f"])
            .output();
        let _ = fs::remove_dir_all(&self.temp_root);
    }
}

fn hkcu_key_exists(path: &str) -> bool {
    Command::new("reg")
        .args(["query", path])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

#[test]
fn nmh_manifest_is_bomless_and_registers_all_four_chromium_hkcu_keys() {
    assert_eq!(BROWSER_NMH_VENDORS.len(), 4);
    for expected in ["Chrome", "Edge", "Brave", "Vivaldi"] {
        assert!(
            BROWSER_NMH_VENDORS.iter().any(|v| v.contains(expected)),
            "missing vendor {expected} in BROWSER_NMH_VENDORS"
        );
    }

    let temp_root = std::env::temp_dir().join(format!("m9_nmh_{}", uuid::Uuid::new_v4().simple()));
    let install_dir = temp_root.join("install");
    let manifest_dir = temp_root.join("manifests");
    fs::create_dir_all(&install_dir).expect("create install_dir");
    fs::create_dir_all(&manifest_dir).expect("create manifest_dir");
    let _guard = RegistryCleanupGuard {
        temp_root: temp_root.clone(),
    };

    let launcher = install_dir.join("run_nmh.cmd");
    fs::write(&launcher, "@echo off\r\n").expect("write dummy launcher");

    let raw_built = build_nmh_manifest_bytes(&launcher, &[DEFAULT_EXTENSION_ID]);
    assert!(!raw_built.starts_with(&[0xEF, 0xBB, 0xBF]), "manifest bytes must not have UTF-8 BOM");

    let manifest_path = install_or_update_nmh_manifest(
        &install_dir,
        &manifest_dir,
        &[DEFAULT_EXTENSION_ID],
        Some(ISOLATED_REG_SUBKEY),
    )
    .expect("install_or_update_nmh_manifest");

    let disk_bytes = fs::read(&manifest_path).expect("read written manifest");
    assert!(!disk_bytes.starts_with(&[0xEF, 0xBB, 0xBF]), "written manifest file must be BOM-less");

    let parsed: Value = serde_json::from_slice(&disk_bytes).expect("valid JSON");
    assert_eq!(parsed["name"], NMH_HOST_NAME);
    assert_eq!(parsed["type"], "stdio");
    assert_eq!(
        parsed["allowed_origins"][0],
        format!("chrome-extension://{DEFAULT_EXTENSION_ID}/")
    );

    if cfg!(windows) {
        for vendor in BROWSER_NMH_VENDORS {
            let key = format!(r"HKCU\{ISOLATED_REG_SUBKEY}\{vendor}\{NMH_HOST_NAME}");
            assert!(hkcu_key_exists(&key), "HKCU NMH key missing: {key}");
        }

        unregister_nmh_manifest(Some(ISOLATED_REG_SUBKEY)).expect("unregister_nmh_manifest");
        for vendor in BROWSER_NMH_VENDORS {
            let key = format!(r"HKCU\{ISOLATED_REG_SUBKEY}\{vendor}\{NMH_HOST_NAME}");
            assert!(!hkcu_key_exists(&key), "HKCU NMH key should be removed: {key}");
        }
    }

    // Verify wait_for_file_unlock returns true for missing & unlocked files
    assert!(wait_for_file_unlock(&temp_root.join("missing.dll"), Duration::from_millis(200)));
    assert!(wait_for_file_unlock(&launcher, Duration::from_millis(200)));
}

#[test]
fn nsis_hooks_handle_release_and_tray_state_machine() {
    let hooks_path = Path::new(env!("CARGO_MANIFEST_DIR")).join("windows").join("hooks.nsh");
    let hooks_text = fs::read_to_string(&hooks_path).expect("read windows/hooks.nsh");
    assert!(hooks_text.contains("agy-ultra-acp.exe"), "hooks.nsh must terminate agy-ultra-acp.exe");
    assert!(hooks_text.contains("Sleep 300"), "hooks.nsh must include Sleep 300 handle-release loop");

    let mut tray = TrayController::new(true);
    assert!(tray.close_to_tray && tray.window_visible);
    assert_eq!(tray.on_window_close_requested(), CloseAction::HideToTray);
    assert!(!tray.window_visible);

    let _ = tray.handle_menu_action(TrayMenuAction::ShowWindow);
    assert!(tray.window_visible);

    let _ = tray.handle_menu_action(TrayMenuAction::Quit);
    assert_eq!(tray.on_window_close_requested(), CloseAction::ExitApp);

    let mut direct_exit_tray = TrayController::new(false);
    assert_eq!(direct_exit_tray.on_window_close_requested(), CloseAction::ExitApp);

    let tok_a = generate_one_time_api_token();
    let tok_b = generate_one_time_api_token();
    assert_eq!(tok_a.len(), 64);
    assert!(tok_a.chars().all(|c| c.is_ascii_hexdigit()) && tok_a != tok_b);

    let b0 = SidecarSupervisor::compute_backoff_ms(0);
    let b2 = SidecarSupervisor::compute_backoff_ms(2);
    let b10 = SidecarSupervisor::compute_backoff_ms(10);
    assert!(b0 == 250 && b2 > b0 && b10 == 4000);
}
