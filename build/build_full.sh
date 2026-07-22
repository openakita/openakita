#!/usr/bin/env bash
# OpenAkita Full Package Build Script (Linux/macOS)
# Output: Installer with all dependencies and models (~1GB)
# Usage: build_full.sh [--fast]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SETUP_CENTER_DIR="$PROJECT_ROOT/apps/setup-center"
RESOURCE_DIR="$SETUP_CENTER_DIR/src-tauri/resources"

FAST_FLAG=""
if [[ "${1:-}" == "--fast" ]]; then
    FAST_FLAG="--fast"
    echo "============================================"
    echo "  OpenAkita Full Package Build [FAST MODE]"
    echo "============================================"
else
    echo "============================================"
    echo "  OpenAkita Full Package Build"
    echo "============================================"
fi

# Step 1: Build the shared web frontend once before either packager consumes it
echo ""
echo "[1/5] Building web frontend..."
cd "$SETUP_CENTER_DIR"
if [[ ! -d node_modules ]]; then
    npm install
fi
npm run build:web

# Step 2: Package Python backend (full mode)
echo ""
echo "[2/5] Packaging Python backend (full mode)..."
cd "$PROJECT_ROOT"
python3 "$SCRIPT_DIR/build_backend.py" --mode full --skip-web-build $FAST_FLAG

# Step 3: Pre-bundle optional modules
echo ""
echo "[3/5] Pre-bundling optional modules..."
python3 "$SCRIPT_DIR/bundle_modules.py"

# Step 4: Copy to Tauri resources
echo ""
echo "[4/5] Copying backend and modules to Tauri resources..."
DIST_SERVER_DIR="$PROJECT_ROOT/dist/openakita-server"
MODULES_DIR="$SCRIPT_DIR/modules"
TARGET_SERVER_DIR="$RESOURCE_DIR/openakita-server"
TARGET_MODULES_DIR="$RESOURCE_DIR/modules"

rm -rf "$TARGET_SERVER_DIR" "$TARGET_MODULES_DIR"
mkdir -p "$RESOURCE_DIR"
cp -r "$DIST_SERVER_DIR" "$TARGET_SERVER_DIR"
if [ -d "$MODULES_DIR" ]; then
    cp -r "$MODULES_DIR" "$TARGET_MODULES_DIR"
fi
echo "  Backend: $TARGET_SERVER_DIR"
echo "  Modules: $TARGET_MODULES_DIR"

# Step 5: Build Tauri app (add modules resource via TAURI_CONFIG)
echo ""
echo "[5/5] Building Tauri app..."
cd "$SETUP_CENTER_DIR"
# Full package needs additional modules resource directory
export TAURI_CONFIG='{"bundle":{"resources":["resources/openakita-server/","resources/modules/"]}}'
npx tauri build

echo ""
echo "============================================"
echo "  Full package build completed!"
echo "  Installer at: $SETUP_CENTER_DIR/src-tauri/target/release/bundle/"
echo "============================================"
