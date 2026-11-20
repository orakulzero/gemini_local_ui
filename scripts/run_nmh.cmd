@echo off
setlocal
set "PYTHONSTARTUP="
set "REPO_ROOT=%~dp0.."
set "PYTHONPATH=%REPO_ROOT%"
if exist "C:\Python314\python.exe" (
    "C:\Python314\python.exe" -u -m backend.app.bridge.native_messaging_host %*
) else (
    python -u -m backend.app.bridge.native_messaging_host %*
)
