#!/usr/bin/env bash
# OpenAkita Core Package Build Script (Linux/macOS)
# Output: Installer with core dependencies only (~180MB)
# Usage: build_core.sh [--fast]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SETUP_CENTER_DIR="$PROJECT_ROOT/apps/setup-center"
RESOURCE_DIR="$SETUP_CENTER_DIR/src-tauri/resources"

FAST_FLAG=""
if [[ "${1:-}" == "--fast" ]]; then
    FAST_FLAG="--fast"
    echo "============================================"
    echo "  OpenAkita Core Package Build [FAST MODE]"
    echo "============================================"
else
    echo "============================================"
    echo "  OpenAkita Core Package Build"
    echo "============================================"
fi

# Step 1: Package Python backend (core mode)
echo ""
echo "[1/3] Packaging Python backend (core mode)..."
python3 "$SCRIPT_DIR/build_backend.py" --mode core $FAST_FLAG

# Step 2: Copy package result to Tauri resources
echo ""
echo "[2/3] Copying backend to Tauri resources..."
DIST_SERVER_DIR="$PROJECT_ROOT/dist/openakita-server"
TARGET_DIR="$RESOURCE_DIR/openakita-server"

rm -rf "$TARGET_DIR"
mkdir -p "$RESOURCE_DIR"
cp -r "$DIST_SERVER_DIR" "$TARGET_DIR"
echo "  Copied to: $TARGET_DIR"

# Step 3: Build Tauri app
echo ""
echo "[3/3] Building Tauri app..."
cd "$SETUP_CENTER_DIR"
npm run tauri build

echo ""
echo "============================================"
echo "  Core package build completed!"
echo "  Installer at: $SETUP_CENTER_DIR/src-tauri/target/release/bundle/"
echo "============================================"
