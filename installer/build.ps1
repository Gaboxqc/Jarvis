# Build the Kai installer — REQ-29, REQ-30.
#
# Two stages: freeze the Python backend, then let Tauri bundle it with the UI
# into a single installer.
#
# The self-test between them is not optional. Skills are discovered at runtime,
# so freezing can drop every one of them while leaving an app that starts,
# serves and answers questions -- a build that is broken in the only way that
# matters and looks fine from the outside. It happened on the first attempt.

param([switch]$SkipBackend, [switch]$SkipSelfTest, [switch]$SkipTests)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$dist = Join-Path $root "installer\dist\kai-backend"

# Before anything is frozen, because a bundle is only worth building from code
# that passes. This script has always gated on the self-test, which asks whether
# packaging kept the skills -- a different and much narrower question than
# whether the app works. The suite answers the second one, takes under two
# minutes, and was never run here.
#
# -SkipTests exists for the case where you are iterating on the packaging itself
# and have just run them by hand. It says so, loudly, for the same reason
# -SkipSelfTest does.
if ($SkipTests) {
    Write-Host "==> TESTS SKIPPED - nothing has checked this code" -ForegroundColor Yellow
}
else {
    Write-Host "==> Running the test suite" -ForegroundColor Cyan
    Push-Location (Join-Path $root "backend")
    try {
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "Tests failed - refusing to build an installer from this" }
    } finally {
        Pop-Location
    }
    Write-Host "    suite passed" -ForegroundColor Green
}

if (-not $SkipBackend) {
    Write-Host "==> Freezing the backend" -ForegroundColor Cyan
    & $python -m PyInstaller (Join-Path $root "installer\kai-backend.spec") `
        --noconfirm --distpath (Join-Path $root "installer\dist") `
        --workpath (Join-Path $root "installer\build") --log-level WARN
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
}

Write-Host "==> Checking the frozen build kept its skills" -ForegroundColor Cyan

# Every module the manifest found must appear in PyInstaller's own record of
# what it collected. Weaker than running the thing -- it proves the code was
# packed, not that it imports -- but it checks the exact failure the self-test
# exists to catch, and unlike the self-test it needs no freshly built unsigned
# binary to execute.
$toc = Join-Path $root "installer/build/kai-backend/Analysis-00.toc"
$manifest = Join-Path $root "backend/app/skills/_manifest.py"
if ((Test-Path $toc) -and (Test-Path $manifest)) {
    $wanted = Select-String -Path $manifest -Pattern '"([^"]+)"' -AllMatches |
        ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value }
    $collected = Get-Content $toc -Raw
    $missing = @($wanted | Where-Object { -not $collected.Contains($_) })
    if ($missing.Count -gt 0) {
        throw "The freeze dropped $($missing.Count) skill module(s): $($missing -join ', ')"
    }
    Write-Host "    all $($wanted.Count) skill modules were collected" -ForegroundColor Green
}

if ($SkipSelfTest) {
    # An Application Control policy refuses to execute freshly built unsigned
    # binaries, which makes the self-test impossible rather than optional. The
    # static check above still ran; what is skipped is the proof that the packed
    # code imports and answers.
    Write-Host "    SELF-TEST SKIPPED - this build has never been run" -ForegroundColor Yellow
    Write-Host "    Nothing here has verified that the backend starts." -ForegroundColor Yellow
}
else {

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
}

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

# The updater key signs the installer so existing installs will accept it.
#
# Read from disk here rather than being committed: whoever holds this key can
# push signed code to every install, and every install trusts it implicitly
# because the matching public key is compiled in. It lives outside the
# repository and is backed up by hand -- losing it means no existing install
# can ever be updated again, because the public key they carry will not match a
# replacement.
#
# A build without it still produces a working installer; it just cannot be
# offered as an update to anyone. That is said out loud rather than failing,
# because a first-time build on a fresh checkout has no reason to have a key.
# Deliberately NOT exported into the build. The key has no password, and there
# is no way to say "no password" through the environment: on Windows, assigning
# "" to a variable deletes it, so `$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""`
# leaves Tauri seeing nothing set. It then prompts -- and it reads that prompt
# from the console device (CONIN$), not stdin, specifically so redirection
# cannot answer it. Piping an empty line does nothing. A build started by
# anything that is not a person sitting at a console hangs forever, which is
# what happened to 0.4.0: silently, after the installer had already been built.
#
# So the bundle is produced unsigned and signed afterwards by `tauri signer
# sign`, where the password is a command-line flag and an empty one can
# actually be expressed. Same key, same signature, no prompt.
$keyFile = Join-Path $env:USERPROFILE ".tauri\kai-updater.key"
if (Test-Path $keyFile) {
    Write-Host "    will sign with $keyFile after bundling" -ForegroundColor Green
} else {
    Write-Host "    no signing key at $keyFile" -ForegroundColor Yellow
    Write-Host "    the installer will work, but cannot be published as an update" -ForegroundColor Yellow
}

# Authenticode signing — separate from the updater key above, and often confused
# with it. The updater key is minisign: it proves to an *already installed* Kai
# that an update came from you. Authenticode proves to *Windows* that the
# installer did, and it is the only one SmartScreen and Smart App Control read.
# Signing one does nothing for the other.
#
# Driven by a thumbprint in the environment rather than anything in the repo, so
# no certificate reference is committed and an unsigned build stays possible.
#
#     $env:KAI_SIGN_THUMBPRINT = "the thumbprint, no spaces"
#
# The timestamp matters as much as the signature: without one, everything you
# ever shipped stops verifying the day the certificate expires. With one, old
# installers keep working because the countersignature records that the
# signature existed while the certificate was valid.
# Tauri refuses to bundle at all when a pubkey is configured and no private key
# is in the environment -- "A public key has been found, but no private key" --
# so the key cannot simply be withheld to get an unsigned bundle. This is the
# switch that actually turns bundle-time signing off. The pubkey itself stays
# exactly where it is, compiled into the binary, which is what existing
# installs verify against; all this says is "do not produce the .sig here",
# because the step below produces it instead.
$bundleOverride = @{ createUpdaterArtifacts = $false }

$thumbprint = $env:KAI_SIGN_THUMBPRINT
if (-not $thumbprint) {
    $found = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
        Sort-Object NotAfter -Descending | Select-Object -First 1
    if ($found) { $thumbprint = $found.Thumbprint }
}

if ($thumbprint) {
    $bundleOverride.windows = @{
        certificateThumbprint = $thumbprint
        digestAlgorithm       = "sha256"
        timestampUrl          = "http://timestamp.digicert.com"
    }
    Write-Host "    signing with certificate $($thumbprint.Substring(0,8))..." -ForegroundColor Green
} else {
    Write-Host "    NOT Authenticode signed - no code signing certificate found" -ForegroundColor Yellow
    Write-Host "    Windows will warn on install, and Smart App Control will refuse" -ForegroundColor Yellow
    Write-Host "    to run it at all. See docs/RELEASING.md." -ForegroundColor Yellow
}

# Merged into one --config: Tauri takes the last one when given several.
$signArgs = @("--config", (@{ bundle = $bundleOverride } | ConvertTo-Json -Depth 6 -Compress))

Push-Location (Join-Path $root "ui")
try {
    npm run tauri build -- @signArgs
    if ($LASTEXITCODE -ne 0) { throw "Tauri bundle failed" }
} finally {
    Pop-Location
}

# Sign for the updater -- see the note where $keyFile is read for why this is a
# separate step and not part of the build above.
if (Test-Path $keyFile) {
    Write-Host "==> Signing the installer for the updater" -ForegroundColor Cyan
    $nsis = Join-Path $root "ui/src-tauri/target/release/bundle/nsis"
    # Scoped to the version just built. The bundle directory is not cleaned
    # between releases, so a bare *-setup.exe also matches installers from
    # every previous one -- the first run of this signed 0.3.5 again on its way
    # past.
    $version = (Get-Content (Join-Path $root "ui/src-tauri/tauri.conf.json") -Raw |
        ConvertFrom-Json).version
    $installers = @(Get-ChildItem -Path $nsis -Filter "*_${version}_*-setup.exe" -ErrorAction SilentlyContinue)
    if ($installers.Count -eq 0) {
        throw "Bundling reported success but produced no $version installer in $nsis"
    }
    Push-Location (Join-Path $root "ui")
    try {
        foreach ($installer in $installers) {
            # --password "" is the point of the whole exercise: an empty
            # password that can actually be expressed, so nothing prompts.
            npx tauri signer sign --private-key-path $keyFile --password "" $installer.FullName
            if ($LASTEXITCODE -ne 0) { throw "Signing $($installer.Name) failed" }
            $sig = "$($installer.FullName).sig"
            if (-not (Test-Path $sig)) {
                throw "The signer exited 0 but wrote no $([IO.Path]::GetFileName($sig)) - the update would be rejected by every install"
            }
            Write-Host "    signed $($installer.Name)" -ForegroundColor Green
        }
    } finally {
        Pop-Location
    }
}

Write-Host "`nInstallers written to ui\src-tauri\target\release\bundle" -ForegroundColor Green
