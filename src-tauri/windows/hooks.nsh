; NSIS lifecycle hooks for Gemini Local UI Desktop (M9)
; Preserves %LOCALAPPDATA%\gemini_local_ui\vault.enc and SQLite history across upgrades.

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Stopping running Gemini Local UI desktop and sidecar processes..."
  nsExec::ExecToLog 'taskkill /IM "gemini-local-ui-desktop.exe" /T /F'
  nsExec::ExecToLog 'taskkill /IM "gemini-backend-x86_64-pc-windows-msvc.exe" /F'
!macroend

!macro NSIS_HOOK_POSTINSTALL
  DetailPrint "Registering Chrome & Edge Native Messaging Host manifests (HKCU)..."
  ExecWait '"$INSTDIR\gemini-local-ui-desktop.exe" --register-nmh "$INSTDIR"'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DetailPrint "Stopping Gemini Local UI processes and removing HKCU Native Messaging Host keys..."
  nsExec::ExecToLog 'taskkill /IM "gemini-local-ui-desktop.exe" /T /F'
  nsExec::ExecToLog 'taskkill /IM "gemini-backend-x86_64-pc-windows-msvc.exe" /F'
  ExecWait '"$INSTDIR\gemini-local-ui-desktop.exe" --unregister-nmh'
!macroend
