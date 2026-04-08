# ==========================================================
#  OpenAkita Parallel Build Script (PowerShell)
#  将 PyInstaller、前端构建、Rust 编译并行执行，大幅缩短打包时间
#
#  Usage:
#    powershell -File build/build_parallel.ps1          # core mode
#    powershell -File build/build_parallel.ps1 -Mode full
#
#  串行: PyInstaller(112s) → Copy(8s) → Frontend(2s) → Rust(108s) → NSIS(60s) ≈ 290s
#  并行: max(PyInstaller, Frontend+Rust)(~120s) → Copy(8s) → NSIS(60s)          ≈ 190s
# ==========================================================

param(
    [ValidateSet("core", "full")]
    [string]$Mode = "core",
    [switch]$Fast
)

$ErrorActionPreference = "Stop"
$sw = [System.Diagnostics.Stopwatch]::StartNew()

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$SetupCenter = Join-Path $ProjectRoot "apps\setup-center"
$SrcTauri    = Join-Path $SetupCenter "src-tauri"
$ResourceDir = Join-Path $SrcTauri "resources"

$modeLabel = if ($Fast) { "$Mode, FAST" } else { $Mode }
Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  OpenAkita Parallel Build (mode: $modeLabel)" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# ── Phase 1: Three parallel jobs ──────────────────────────
Write-Host ""
Write-Host "[Phase 1/3] Starting 3 parallel build tasks..." -ForegroundColor Yellow

# Resolve full native paths for tools so Start-Job can find them.
# On Windows, npm/cargo are .cmd batch files; Get-Command may return extensionless bash scripts
# that don't work in PowerShell jobs. Prefer .cmd/.exe variants explicitly.
function Resolve-NativeCmd($name) {
    # Try .cmd first (Windows batch wrapper), then .exe, then fallback to Get-Command
    foreach ($ext in @(".cmd", ".exe", "")) {
        $candidate = Get-Command "$name$ext" -ErrorAction SilentlyContinue
        if ($candidate) { return $candidate.Source }
    }
    return $null
}

$npmCmd    = Resolve-NativeCmd "npm"
$cargoCmd  = Resolve-NativeCmd "cargo"
$pythonCmd = Resolve-NativeCmd "python"

Write-Host "  Resolved: python=$pythonCmd"
Write-Host "  Resolved: npm=$npmCmd"
Write-Host "  Resolved: cargo=$cargoCmd"

if (-not $npmCmd)    { Write-Host "  [WARN] npm not found in PATH" -ForegroundColor Yellow }
if (-not $cargoCmd)  { Write-Host "  [WARN] cargo not found in PATH" -ForegroundColor Yellow }
if (-not $pythonCmd) { Write-Host "  [WARN] python not found in PATH" -ForegroundColor Yellow }

# Job A: PyInstaller
$jobPy = Start-Job -Name "PyInstaller" -ScriptBlock {
    param($root, $scriptDir, $mode, $py, $useFast)
    Set-Location $root
    $args = @("$scriptDir\build_backend.py", "--mode", $mode)
    if ($useFast) { $args += "--fast" }
    & $py @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }
} -ArgumentList $ProjectRoot, $ScriptDir, $Mode, $pythonCmd, $Fast.IsPresent
Write-Host "  → [A] PyInstaller backend packaging  (Job: $($jobPy.Id))"

# Job B: Frontend build
$jobFe = Start-Job -Name "Frontend" -ScriptBlock {
    param($dir, $npm)
    Set-Location $dir
    $env:VITE_PREVIEW_BUILD = "true"
    & $npm run build 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed (exit $LASTEXITCODE)" }
} -ArgumentList $SetupCenter, $npmCmd
Write-Host "  → [B] Frontend build (Vite)          (Job: $($jobFe.Id))"

# Job C: Rust pre-compile
$jobRs = Start-Job -Name "RustCompile" -ScriptBlock {
    param($dir, $cargo)
    Set-Location $dir
    & $cargo build --release --features tauri/custom-protocol 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Rust compile failed (exit $LASTEXITCODE)" }
} -ArgumentList $SrcTauri, $cargoCmd
Write-Host "  → [C] Rust release compile           (Job: $($jobRs.Id))"

Write-Host ""
Write-Host "  Waiting for all tasks to complete..."

# Wait for all jobs
$allJobs = @($jobPy, $jobFe, $jobRs)
$failed = $false

# Poll with progress
while ($allJobs | Where-Object { $_.State -eq 'Running' }) {
    Start-Sleep -Seconds 3
    foreach ($job in $allJobs) {
        if ($job.State -eq 'Completed' -and -not $job.HasMoreData) { continue }
        if ($job.State -ne 'Running') {
            # Print completion status once
        }
    }
    $running = ($allJobs | Where-Object { $_.State -eq 'Running' }).Count
    $elapsed = [math]::Round($sw.Elapsed.TotalSeconds)
    Write-Host "`r  [${elapsed}s] $running task(s) still running..." -NoNewline
}
Write-Host ""

# Check results
foreach ($job in $allJobs) {
    $result = Receive-Job $job -ErrorAction SilentlyContinue
    if ($job.State -eq 'Completed') {
        Write-Host "  ✓ $($job.Name) done" -ForegroundColor Green
    } elseif ($job.State -eq 'Failed') {
        if ($job.Name -eq 'RustCompile') {
            Write-Host "  ⚠ $($job.Name) failed (Tauri will retry)" -ForegroundColor Yellow
        } else {
            Write-Host "  ✗ $($job.Name) FAILED" -ForegroundColor Red
            Write-Host "    Output:" -ForegroundColor Red
            $result | Select-Object -Last 10 | ForEach-Object { Write-Host "    $_" }
            $failed = $true
        }
    }
}
Remove-Job $allJobs -Force

$phase1Time = [math]::Round($sw.Elapsed.TotalSeconds)
Write-Host ""
Write-Host "  Phase 1 completed in ${phase1Time}s" -ForegroundColor Cyan

if ($failed) {
    Write-Host "ERROR: Critical task failed. Aborting." -ForegroundColor Red
    exit 1
}

# ── Phase 2: Copy resources ──────────────────────────────
Write-Host ""
Write-Host "[Phase 2/3] Copying backend to Tauri resources..." -ForegroundColor Yellow

$DistServerDir = Join-Path $ProjectRoot "dist\openakita-server"
$TargetDir = Join-Path $ResourceDir "openakita-server"

if (Test-Path $TargetDir) { Remove-Item -Recurse -Force $TargetDir }
New-Item -ItemType Directory -Force -Path $ResourceDir | Out-Null
Copy-Item -Recurse $DistServerDir $TargetDir

if ($Mode -eq "full") {
    $modulesDir = Join-Path $ProjectRoot "build\modules"
    if (Test-Path $modulesDir) {
        $targetModules = Join-Path $ResourceDir "modules"
        if (Test-Path $targetModules) { Remove-Item -Recurse -Force $targetModules }
        Copy-Item -Recurse $modulesDir $targetModules
    }
}
Write-Host "  ✓ Resources copied" -ForegroundColor Green

# ── Phase 3: Tauri NSIS bundling ──────────────────────────
Write-Host ""
Write-Host "[Phase 3/3] Creating NSIS installer..." -ForegroundColor Yellow

Push-Location $SetupCenter
try {
    $env:CI = $null
    # Skip frontend build (already done), Rust binary is cached
    npx tauri build --bundles nsis --config '{\"build\":{\"beforeBuildCommand\":\"\"}}'
    if ($LASTEXITCODE -ne 0) { throw "Tauri build failed" }
} finally {
    Pop-Location
}

$totalTime = [math]::Round($sw.Elapsed.TotalSeconds)

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "  Build completed in ${totalTime}s" -ForegroundColor Green
Write-Host "  Phase 1 (parallel): ${phase1Time}s" -ForegroundColor Green
Write-Host "  Phase 2+3 (sequential): $($totalTime - $phase1Time)s" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green

# Rename latest installer with timestamp + git hash
$nsisDir = Join-Path $SrcTauri "target\release\bundle\nsis"
if (Test-Path $nsisDir) {
    $latest = Get-ChildItem "$nsisDir\*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        $timestamp = Get-Date -Format "yyyyMMdd-HHmm"
        $gitHash = ""
        try {
            $gitHash = (git -C $ProjectRoot rev-parse --short=7 HEAD 2>$null)
            if (-not $gitHash) { $gitHash = "unknown" }
        } catch { $gitHash = "unknown" }
        $baseName = $latest.BaseName   # e.g. "OpenAkita Desktop_1.22.5_x64-setup"
        $tag      = "$([char]0x9884)$([char]0x89C8)$([char]0x7248)"  # 预览版
        $newName  = "${baseName}_${timestamp}_${gitHash}_${tag}.exe"
        $newPath  = Join-Path $nsisDir $newName
        Copy-Item $latest.FullName $newPath
        Write-Host ""
        Write-Host "  Installer:" -ForegroundColor Cyan
        Write-Host "    $newName ($([math]::Round((Get-Item $newPath).Length / 1MB))MB)" -ForegroundColor White
        Write-Host "    Git: $gitHash" -ForegroundColor Gray
        Write-Host "    Path: $newPath" -ForegroundColor Gray
    }
}

# Also list recent installers
Write-Host ""
Write-Host "  Recent builds:" -ForegroundColor Cyan
Get-ChildItem "$nsisDir\*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 5 | ForEach-Object {
    Write-Host "    $($_.Name) ($([math]::Round($_.Length / 1MB))MB)"
}
