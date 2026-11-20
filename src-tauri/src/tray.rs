//! System tray close-to-tray state machine and menu action dispatcher.

use serde::{Deserialize, Serialize};

/// Action taken when the main desktop window receives a close request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum CloseAction {
    HideToTray,
    ExitApp,
}

/// System tray context-menu actions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TrayMenuAction {
    ShowWindow,
    OpenBrowser,
    RestartSidecar,
    Quit,
}

/// Deterministic controller tracking window visibility and close-to-tray behavior.
#[derive(Debug, Clone)]
pub struct TrayController {
    pub close_to_tray: bool,
    pub window_visible: bool,
    quit_requested: bool,
}

impl Default for TrayController {
    fn default() -> Self {
        Self::new(true)
    }
}

impl TrayController {
    pub fn new(close_to_tray: bool) -> Self {
        Self {
            close_to_tray,
            window_visible: true,
            quit_requested: false,
        }
    }

    pub fn on_window_close_requested(&mut self) -> CloseAction {
        if self.quit_requested || !self.close_to_tray {
            self.window_visible = false;
            CloseAction::ExitApp
        } else {
            self.window_visible = false;
            CloseAction::HideToTray
        }
    }

    pub fn handle_menu_action(&mut self, action: TrayMenuAction) -> Option<CloseAction> {
        match action {
            TrayMenuAction::ShowWindow => {
                self.window_visible = true;
                None
            }
            TrayMenuAction::OpenBrowser | TrayMenuAction::RestartSidecar => None,
            TrayMenuAction::Quit => {
                self.quit_requested = true;
                self.window_visible = false;
                Some(CloseAction::ExitApp)
            }
        }
    }
}
