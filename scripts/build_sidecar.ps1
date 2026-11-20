param(
    [switch]$VerifySpecOnly,
    [string]$TargetTriple = "x86_64-pc-windows-msvc"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SpecPath = Join-Path $RepoRoot "backend\gemini_sidecar.spec"
$BinariesDir = Join-Path $RepoRoot "src-tauri\binaries"

if (-not (Test-Path $SpecPath)) {
    throw "PyInstaller spec not found at $SpecPath"
}

python -c "import ast, pathlib; src = pathlib.Path(r'$SpecPath').read_text(encoding='utf-8'); ast.parse(src); assert 'COLLECT(' in src and 'exclude_binaries=True' in src"
if ($LASTEXITCODE -ne 0) {
    throw "Spec validation failed for $SpecPath"
}

if ($VerifySpecOnly) {
    Write-Host "Verified onedir PyInstaller spec: $SpecPath"
    exit 0
}

Push-Location $RepoRoot
try {
    python -m PyInstaller $SpecPath --noconfirm --distpath (Join-Path $RepoRoot "dist") --workpath (Join-Path $RepoRoot "build\pyinstaller")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }

    if (-not (Test-Path $BinariesDir)) {
        New-Item -ItemType Directory -Path $BinariesDir -Force | Out-Null
    }

    $BuiltDir = Join-Path $RepoRoot "dist\gemini-backend"
    Copy-Item -Path (Join-Path $BuiltDir "*") -Destination $BinariesDir -Recurse -Force

    $AcpCandidates = @(
        (Join-Path (Split-Path $RepoRoot -Parent) "agy-ultra-acp\target\release\agy-ultra-acp.exe"),
        (Join-Path (Split-Path $RepoRoot -Parent) "agy-ultra-acp\target\debug\agy-ultra-acp.exe")
    )
    foreach ($cand in $AcpCandidates) {
        if (Test-Path $cand) {
            Copy-Item -Path $cand -Destination (Join-Path $BinariesDir "agy-ultra-acp-$TargetTriple.exe") -Force
            break
        }
    }
    Write-Host "Sidecar bundle staged at $BinariesDir"
}
finally {
    Pop-Location
}
