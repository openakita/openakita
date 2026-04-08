# OpenAkita Core Package Build Script (Windows PowerShell)
# Output: Installer with core dependencies only (~180MB)
# Usage: .\build_core.ps1 [-Fast]

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
    Write-Host "  OpenAkita Core Package Build [FAST MODE]" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
} else {
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host "  OpenAkita Core Package Build" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
}

# Step 1: Package Python backend (core mode)
Write-Host "`n[1/3] Packaging Python backend (core mode)..." -ForegroundColor Yellow
$backendArgs = @("$ScriptDir\build_backend.py", "--mode", "core")
if ($Fast) { $backendArgs += "--fast" }
python @backendArgs
if ($LASTEXITCODE -ne 0) { throw "Python backend packaging failed" }

# Step 2: Copy package result to Tauri resources
Write-Host "`n[2/3] Copying backend to Tauri resources..." -ForegroundColor Yellow
$DistServerDir = Join-Path $ProjectRoot "dist\openakita-server"
$TargetDir = Join-Path $ResourceDir "openakita-server"

if (Test-Path $TargetDir) { Remove-Item -Recurse -Force $TargetDir }
New-Item -ItemType Directory -Force -Path $ResourceDir | Out-Null
Copy-Item -Recurse $DistServerDir $TargetDir
Write-Host "  Copied to: $TargetDir"

# Step 3: Build Tauri app
Write-Host "`n[3/3] Building Tauri app..." -ForegroundColor Yellow
Push-Location $SetupCenterDir
try {
    npm run tauri build
    if ($LASTEXITCODE -ne 0) { throw "Tauri build failed" }
} finally {
    Pop-Location
}

Write-Host "`n============================================" -ForegroundColor Green
Write-Host "  Core package build completed!" -ForegroundColor Green
Write-Host "  Installer at: $SetupCenterDir\src-tauri\target\release\bundle\" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
