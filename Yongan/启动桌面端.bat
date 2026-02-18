@echo off
setlocal
title OpenAkita Setup Center Launcher

cd /d "%~dp0\.."
cd /d "apps\setup-center" || (
  echo [ERROR] Cannot enter apps\setup-center
  pause
  exit /b 1
)

where node >nul 2>nul || (
  echo [ERROR] node not found. Please install Node.js.
  pause
  exit /b 1
)

where npm >nul 2>nul || (
  echo [ERROR] npm not found. Please install Node.js/npm.
  pause
  exit /b 1
)

where cargo >nul 2>nul || (
  echo [ERROR] cargo not found. Tauri requires Rust toolchain.
  echo Install Rust from: https://rustup.rs/
  pause
  exit /b 1
)

echo [INFO] Installing dependencies...
call npm install
if errorlevel 1 (
  echo [ERROR] npm install failed.
  pause
  exit /b 1
)

echo [INFO] Starting Tauri dev...
call npm run tauri dev
if errorlevel 1 (
  echo [ERROR] tauri dev failed.
  pause
  exit /b 1
)
