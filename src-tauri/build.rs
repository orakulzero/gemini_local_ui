fn main() {
    #[cfg(feature = "tauri-gui")]
    tauri_build::build();
}
