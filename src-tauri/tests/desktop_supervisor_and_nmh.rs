//! Milestone M9 Integration & Unit Tests for `gemini-local-ui-desktop` (`src-tauri`).

use gemini_local_ui_desktop::nmh_registry::{
    DEFAULT_EXTENSION_ID, NMH_HOST_NAME, build_nmh_manifest_bytes, install_or_update_nmh_manifest,
    unregister_nmh_manifest,
};
use gemini_local_ui_desktop::sidecar::{SidecarSupervisor, generate_one_time_api_token};
use gemini_local_ui_desktop::tray::{CloseAction, TrayController, TrayMenuAction};
use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

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
fn nmh_manifest_is_bomless_and_registers_isolated_hkcu_keys() {
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

    assert_eq!(
        manifest_path.file_name().and_then(|s| s.to_str()),
        Some("com.orakulzero.gemini_local_ui.json")
    );
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
        let chrome_key = format!(
            r"HKCU\{ISOLATED_REG_SUBKEY}\Google\Chrome\NativeMessagingHosts\{NMH_HOST_NAME}"
        );
        let edge_key = format!(
            r"HKCU\{ISOLATED_REG_SUBKEY}\Microsoft\Edge\NativeMessagingHosts\{NMH_HOST_NAME}"
        );
        assert!(hkcu_key_exists(&chrome_key), "Chrome HKCU NMH key missing: {chrome_key}");
        assert!(hkcu_key_exists(&edge_key), "Edge HKCU NMH key missing: {edge_key}");

        unregister_nmh_manifest(Some(ISOLATED_REG_SUBKEY)).expect("unregister_nmh_manifest");
        assert!(!hkcu_key_exists(&chrome_key), "Chrome HKCU NMH key should be removed");
        assert!(!hkcu_key_exists(&edge_key), "Edge HKCU NMH key should be removed");
    }
}

#[test]
fn tray_close_to_tray_state_machine_and_token_backoff() {
    let mut tray = TrayController::new(true);
    assert!(tray.close_to_tray);
    assert!(tray.window_visible);

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
    assert!(tok_a.chars().all(|c| c.is_ascii_hexdigit()));
    assert_ne!(tok_a, tok_b);

    let b0 = SidecarSupervisor::compute_backoff_ms(0);
    let b1 = SidecarSupervisor::compute_backoff_ms(1);
    let b2 = SidecarSupervisor::compute_backoff_ms(2);
    let b10 = SidecarSupervisor::compute_backoff_ms(10);
    assert_eq!(b0, 250);
    assert!(b1 > b0 && b2 > b1);
    assert_eq!(b10, 4000);
}
