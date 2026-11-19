param(
    [Parameter(Mandatory = $true)]
    [string]$ExtensionId
)

$ErrorActionPreference = "Stop"

if ($ExtensionId -notmatch '^[a-p]{32}$') {
    Write-Error "Invalid ExtensionId: must be exactly 32 lowercase characters [a-p] with no wildcards."
    exit 1
}

$localApp = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::LocalApplicationData)
$hostDir = Join-Path $localApp "gemini_local_ui"
if (-not (Test-Path $hostDir)) {
    New-Item -Path $hostDir -ItemType Directory -Force | Out-Null
}

$cmdPath = (Resolve-Path (Join-Path $PSScriptRoot "run_nmh.cmd")).Path
$manifestPath = Join-Path $hostDir "com.orakulzero.gemini_local_ui.json"

$manifestContent = @{
    name = "com.orakulzero.gemini_local_ui"
    description = "Native messaging host for Gemini Local UI cookie sync"
    path = $cmdPath
    type = "stdio"
    allowed_origins = @(
        "chrome-extension://$ExtensionId/"
    )
} | ConvertTo-Json -Depth 4

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($manifestPath, $manifestContent, $utf8NoBom)
Write-Host "Wrote NMH manifest: $manifestPath"

$registryTargets = @(
    "HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.orakulzero.gemini_local_ui",
    "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\com.orakulzero.gemini_local_ui"
)

foreach ($regPath in $registryTargets) {
    if (-not (Test-Path $regPath)) {
        New-Item -Path $regPath -Force | Out-Null
    }
    Set-ItemProperty -Path $regPath -Name "(Default)" -Value $manifestPath -Force
    Write-Host "Registered NMH host in $regPath"
}

Write-Host "NMH registration completed successfully for extension: $ExtensionId"
