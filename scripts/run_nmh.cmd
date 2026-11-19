@echo off
setlocal
set "REPO_ROOT=%~dp0.."
set "PYTHONPATH=%REPO_ROOT%;%PYTHONPATH%"
python -u -m backend.app.bridge.native_messaging_host %*
