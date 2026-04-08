# OpenAkita Full Package Build Script (Windows PowerShell)
# Output: Installer with all dependencies and models (~1GB)
# Usage: .\build_full.ps1 [-Fast]

param(
    [switch]$Fast
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$SetupCenterDir = Join-Path $ProjectRoot "apps\setup-center"
$ResourceDir = Join-Path $SetupCenterDir "src-tauri\resources"

if ($Fast) {
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "  OpenAkita Full Package Build [FAST MODE]" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
} else {
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "  OpenAkita Full Package Build" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
}

# Step 1: Package Python backend (full mode)
Write-Host "`n[1/4] Packaging Python backend (full mode)..." -ForegroundColor Yellow
$backendArgs = @("$ScriptDir\build_backend.py", "--mode", "full")
if ($Fast) { $backendArgs += "--fast" }
python @backendArgs
if ($LASTEXITCODE -ne 0) { throw "Python backend packaging failed" }

# Step 2: Pre-bundle optional modules
Write-Host "`n[2/4] Pre-bundling optional modules..." -ForegroundColor Yellow
python "$ScriptDir\bundle_modules.py"
if ($LASTEXITCODE -ne 0) { throw "Module pre-bundling failed" }

# Step 3: Copy to Tauri resources
Write-Host "`n[3/4] Copying backend and modules to Tauri resources..." -ForegroundColor Yellow
$DistServerDir = Join-Path $ProjectRoot "dist\openakita-server"
$ModulesDir = Join-Path $ScriptDir "modules"
$TargetServerDir = Join-Path $ResourceDir "openakita-server"
$TargetModulesDir = Join-Path $ResourceDir "modules"

if (Test-Path $TargetServerDir) { Remove-Item -Recurse -Force $TargetServerDir }
if (Test-Path $TargetModulesDir) { Remove-Item -Recurse -Force $TargetModulesDir }
New-Item -ItemType Directory -Force -Path $ResourceDir | Out-Null
Copy-Item -Recurse $DistServerDir $TargetServerDir
if (Test-Path $ModulesDir) {
    Copy-Item -Recurse $ModulesDir $TargetModulesDir
}
Write-Host "  Backend: $TargetServerDir"
Write-Host "  Modules: $TargetModulesDir"

# Step 4: Build Tauri app (add modules resource via TAURI_CONFIG)
Write-Host "`n[4/4] Building Tauri app..." -ForegroundColor Yellow
Push-Location $SetupCenterDir
try {
    # Full package needs additional modules resource directory
    $env:TAURI_CONFIG = '{"bundle":{"resources":["resources/openakita-server/","resources/modules/"]}}'
    npx tauri build
    if ($LASTEXITCODE -ne 0) { throw "Tauri build failed" }
} finally {
    $env:TAURI_CONFIG = $null
    Pop-Location
}

Write-Host "`n============================================" -ForegroundColor Green
Write-Host "  Full package build completed!" -ForegroundColor Green
Write-Host "  Installer at: $SetupCenterDir\src-tauri\target\release\bundle\" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
