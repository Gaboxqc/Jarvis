# Build the Kai installer — REQ-29, REQ-30.
#
# Two stages: freeze the Python backend, then let Tauri bundle it with the UI
# into a single installer.
#
# The self-test between them is not optional. Skills are discovered at runtime,
# so freezing can drop every one of them while leaving an app that starts,
# serves and answers questions -- a build that is broken in the only way that
# matters and looks fine from the outside. It happened on the first attempt.

param([switch]$SkipBackend)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$dist = Join-Path $root "installer\dist\kai-backend"

if (-not $SkipBackend) {
    Write-Host "==> Freezing the backend" -ForegroundColor Cyan
    & $python -m PyInstaller (Join-Path $root "installer\kai-backend.spec") `
        --noconfirm --distpath (Join-Path $root "installer\dist") `
        --workpath (Join-Path $root "installer\build") --log-level WARN
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
}

Write-Host "==> Checking the frozen build kept its skills" -ForegroundColor Cyan
# The sidecar is built windowed (no console flash on every launch), which makes
# it a Windows-subsystem binary. PowerShell does not wait for those, so calling
# it directly returns immediately with a meaningless $LASTEXITCODE -- the
# self-test appears to fail even when it passed. Start-Process -Wait is the
# only reliable way to get a real exit code out of it.
$out = Join-Path $env:TEMP "kai-selftest.out"
$err = Join-Path $env:TEMP "kai-selftest.err"
$check = Start-Process -FilePath (Join-Path $dist "kai-backend.exe") `
    -ArgumentList "--selftest" -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput $out -RedirectStandardError $err
if (Test-Path $out) { Get-Content $out | Select-Object -First 4 }
if ($check.ExitCode -ne 0) {
    if (Test-Path $err) { Get-Content $err | Write-Host -ForegroundColor Red }
    throw "Self-test failed - refusing to package a broken build"
}
Write-Host "    self-test passed" -ForegroundColor Green

Write-Host "==> Staging the backend for Tauri" -ForegroundColor Cyan
# Shipped as a resource directory rather than an externalBin. PyInstaller's
# one-dir output is an executable plus an _internal folder that has to sit
# beside it, and externalBin carries only the single named file. The
# alternative, --onefile, unpacks half a gigabyte to temp on every launch.
$staged = Join-Path $root "ui\src-tauri\resources\kai-backend"
if (Test-Path $staged) { Remove-Item -Recurse -Force $staged }
New-Item -ItemType Directory -Force -Path $staged | Out-Null
Copy-Item -Recurse -Force (Join-Path $dist "*") $staged

if (-not (Test-Path (Join-Path $staged "kai-backend.exe"))) {
    throw "Staging produced no kai-backend.exe - the bundle would ship without a backend"
}
$mb = [int]((Get-ChildItem -Recurse $staged | Measure-Object -Property Length -Sum).Sum / 1MB)
Write-Host "    staged $mb MB" -ForegroundColor Green

Write-Host "==> Bundling the installer" -ForegroundColor Cyan
Push-Location (Join-Path $root "ui")
try {
    npm run tauri build
    if ($LASTEXITCODE -ne 0) { throw "Tauri bundle failed" }
} finally { Pop-Location }

Write-Host "`nInstallers written to ui\src-tauri\target\release\bundle" -ForegroundColor Green
