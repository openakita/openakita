#
# OpenAkita Quick Start Script for Windows
#
# Usage (PowerShell):
#   irm https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.ps1 | iex
#
# Or download and run:
#   .\quickstart.ps1
#

$ErrorActionPreference = "Stop"

# Colors
function Write-Color {
    param([string]$Text, [string]$Color = "White")
    Write-Host $Text -ForegroundColor $Color
}

Write-Color @"

   ____                      _    _    _ _        
  / __ \                    / \  | | _(_) |_ __ _ 
 | |  | |_ __   ___ _ __   / _ \ | |/ / | __/ _` |
 | |  | | '_ \ / _ \ '_ \ / ___ \|   <| | || (_| |
 | |__| | |_) |  __/ | | /_/   \_\_|\_\_|\__\__,_|
  \____/| .__/ \___|_| |_|                        
        |_|    Your Loyal AI Companion

"@ -Color Cyan

Write-Color "=== OpenAkita Quick Start ===" -Color Cyan
Write-Host ""

# Check Python version
Write-Color "Checking Python version..." -Color Yellow
try {
    $pythonVersion = python --version 2>&1
    $versionMatch = $pythonVersion -match "Python (\d+)\.(\d+)"
    if ($versionMatch) {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
            Write-Color "Error: Python 3.11+ is required. Found: $pythonVersion" -Color Red
            exit 1
        }
        Write-Color "OK Python $major.$minor" -Color Green
    }
} catch {
    Write-Color "Error: Python is not installed." -Color Red
    Write-Host "Please install Python 3.11 or later from https://www.python.org"
    exit 1
}
Write-Host ""

# Check pip
Write-Color "Checking pip..." -Color Yellow
try {
    python -m pip --version | Out-Null
    Write-Color "OK pip is available" -Color Green
} catch {
    Write-Color "Error: pip is not installed." -Color Red
    exit 1
}
Write-Host ""

# Create virtual environment
Write-Color "Creating virtual environment..." -Color Yellow
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Color "OK Virtual environment created" -Color Green
} else {
    Write-Color "OK Virtual environment already exists" -Color Green
}
Write-Host ""

# Activate virtual environment
Write-Color "Activating virtual environment..." -Color Yellow
& .\.venv\Scripts\Activate.ps1
Write-Color "OK Virtual environment activated" -Color Green
Write-Host ""

# Install OpenAkita
Write-Color "Installing OpenAkita..." -Color Yellow
python -m pip install --upgrade pip | Out-Null
python -m pip install openakita
Write-Color "OK OpenAkita installed" -Color Green
Write-Host ""

# Run setup wizard
Write-Color "Starting setup wizard..." -Color Cyan
Write-Host ""
openakita init

Write-Host ""
Write-Color "=== Installation Complete ===" -Color Green
Write-Host "To start OpenAkita, run: " -NoNewline
Write-Color "openakita" -Color Cyan
Write-Host "Or with Telegram: " -NoNewline
Write-Color "openakita serve" -Color Cyan
Write-Host ""
