; NSIS lifecycle hooks for Gemini Local UI Desktop (M9 + v2.1 Hardening)
; Preserves %LOCALAPPDATA%\gemini_local_ui\vault.enc and gemini_local.db across upgrades.

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Stopping running Gemini Local UI desktop, backend sidecar, and Rust ACP processes..."
  nsExec::ExecToLog 'taskkill /IM "gemini-local-ui-desktop.exe" /T /F'
  nsExec::ExecToLog 'taskkill /IM "gemini-backend-x86_64-pc-windows-msvc.exe" /T /F'
  nsExec::ExecToLog 'taskkill /IM "agy-ultra-acp.exe" /T /F'

  StrCpy $R0 0
  _preinstall_unlock_loop:
    IfFileExists "$INSTDIR\binaries\gemini-backend-x86_64-pc-windows-msvc.exe" 0 _preinstall_check_dll
    ClearErrors
    FileOpen $R1 "$INSTDIR\binaries\gemini-backend-x86_64-pc-windows-msvc.exe" a
    IfErrors _preinstall_retry
    FileClose $R1
  _preinstall_check_dll:
    IfFileExists "$INSTDIR\_internal\sqlite_vec.dll" 0 _preinstall_unlock_done
    ClearErrors
    FileOpen $R1 "$INSTDIR\_internal\sqlite_vec.dll" a
    IfErrors _preinstall_retry
    FileClose $R1
    Goto _preinstall_unlock_done
  _preinstall_retry:
    IntOp $R0 $R0 + 1
    IntCmp $R0 10 _preinstall_unlock_done _preinstall_sleep _preinstall_unlock_done
  _preinstall_sleep:
    Sleep 300
    Goto _preinstall_unlock_loop
  _preinstall_unlock_done:
!macroend

!macro NSIS_HOOK_POSTINSTALL
  DetailPrint "Registering Chrome, Edge, Brave & Vivaldi Native Messaging Host manifests (HKCU)..."
  ExecWait '"$INSTDIR\gemini-local-ui-desktop.exe" --register-nmh "$INSTDIR"'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DetailPrint "Stopping Gemini Local UI processes and removing HKCU Native Messaging Host keys..."
  nsExec::ExecToLog 'taskkill /IM "gemini-local-ui-desktop.exe" /T /F'
  nsExec::ExecToLog 'taskkill /IM "gemini-backend-x86_64-pc-windows-msvc.exe" /T /F'
  nsExec::ExecToLog 'taskkill /IM "agy-ultra-acp.exe" /T /F'

  StrCpy $R0 0
  _preuninstall_unlock_loop:
    IfFileExists "$INSTDIR\binaries\gemini-backend-x86_64-pc-windows-msvc.exe" 0 _preuninstall_check_dll
    ClearErrors
    FileOpen $R1 "$INSTDIR\binaries\gemini-backend-x86_64-pc-windows-msvc.exe" a
    IfErrors _preuninstall_retry
    FileClose $R1
  _preuninstall_check_dll:
    IfFileExists "$INSTDIR\_internal\sqlite_vec.dll" 0 _preuninstall_unlock_done
    ClearErrors
    FileOpen $R1 "$INSTDIR\_internal\sqlite_vec.dll" a
    IfErrors _preuninstall_retry
    FileClose $R1
    Goto _preuninstall_unlock_done
  _preuninstall_retry:
    IntOp $R0 $R0 + 1
    IntCmp $R0 10 _preuninstall_unlock_done _preuninstall_sleep _preuninstall_unlock_done
  _preuninstall_sleep:
    Sleep 300
    Goto _preuninstall_unlock_loop
  _preuninstall_unlock_done:

  ExecWait '"$INSTDIR\gemini-local-ui-desktop.exe" --unregister-nmh'
!macroend
