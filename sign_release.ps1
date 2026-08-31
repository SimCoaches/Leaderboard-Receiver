# Signed release build for Lap Time Receiver.
#
# Signs with the SIM COACHES LLC EV certificate the same way TrackPro and the
# Lap Time Sender do: Jsign against the EV private key held in Google Cloud
# KMS/HSM. No key material exists on this machine; gcloud application-default
# credentials authorize the signing.
#
#   powershell -ExecutionPolicy Bypass -File sign_release.ps1            # sign existing build
#   powershell -ExecutionPolicy Bypass -File sign_release.ps1 -Rebuild   # PyInstaller first
#
# Order matters: the app exe is signed BEFORE the installer is compiled so the
# packaged exe is the signed one; then the installer itself is signed.

param(
    [switch]$Rebuild,
    [string]$TrackProRepo = "$env:USERPROFILE\Documents\TrackProV2"
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$appExe = Join-Path $root 'Client\dist\Lap Time Receiver.exe'
$installerExe = Join-Path $root 'Output\LapTimeReceiver_Setup.exe'

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

$signScript = Join-Path $TrackProRepo 'scripts\sign-artifact-google-kms.ps1'
if (-not (Test-Path $signScript)) { Fail "TrackPro signing script not found: $signScript" }

$certFile = $env:TRACKPRO_EV_CERT_FILE
if (-not $certFile) { $certFile = Join-Path $TrackProRepo 'certs\sim-coaches-ev-code-signing-2027.pem' }
if (-not (Test-Path $certFile)) { Fail "EV certificate file not found: $certFile" }

$jsignJar = $env:TRACKPRO_JSIGN_JAR
if (-not $jsignJar) {
    foreach ($candidate in @("$env:LOCALAPPDATA\TrackProSigning\jsign.jar",
                             (Join-Path $TrackProRepo 'jsign.jar'),
                             "$env:USERPROFILE\Downloads\jsign.jar")) {
        if (Test-Path $candidate) { $jsignJar = $candidate; break }
    }
}
if (-not $jsignJar) { Fail "jsign.jar not found (TrackProSigning folder, TrackPro repo, or Downloads)." }

function Sign-File([string]$path) {
    Write-Host "`n== Signing $(Split-Path $path -Leaf)" -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File $signScript `
        -ArtifactPath $path -CertFile $certFile -JsignJar $jsignJar
    if ($LASTEXITCODE -ne 0) { Fail "Signing failed for $path" }
}

if ($Rebuild) {
    Write-Host "== Rebuilding executable (PyInstaller)"
    if (Get-Process 'Lap Time Receiver' -ErrorAction SilentlyContinue) { Fail "Close the running Lap Time Receiver first - it locks the build output." }
    Push-Location (Join-Path $root 'Client')
    python -m PyInstaller --clean --noconfirm 'Lap Time Receiver.spec'
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Fail "PyInstaller failed." }
}
if (-not (Test-Path $appExe)) { Fail "App executable not found ($appExe) - run with -Rebuild." }

# 1. Sign the application exe so the installer packages a signed binary
Sign-File $appExe

# 2. Compile the installer around the signed exe
Write-Host "`n== Compiling installer" -ForegroundColor Cyan
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { Fail "Inno Setup 6 not found." }
if (-not (Test-Path (Join-Path $root 'Output'))) { New-Item -ItemType Directory (Join-Path $root 'Output') | Out-Null }
Push-Location $root
& $iscc 'installer.iss' | Select-Object -Last 2
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Fail "Installer compile failed." }

# 3. Sign the installer itself (this is what downloads/SmartScreen sees)
Sign-File $installerExe

Write-Host "`n== Final verification" -ForegroundColor Cyan
$allOk = $true
foreach ($file in @($appExe, $installerExe)) {
    $sig = Get-AuthenticodeSignature $file
    $name = Split-Path $file -Leaf
    if ($sig.Status -eq 'Valid') {
        Write-Host "  [OK]   $name  ($($sig.SignerCertificate.Subject.Split(',')[0]))" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $name  ($($sig.Status))" -ForegroundColor Red
        $allOk = $false
    }
}
if (-not $allOk) { Fail "Signature verification failed." }

Write-Host "`nSigned release ready: $installerExe" -ForegroundColor Green
