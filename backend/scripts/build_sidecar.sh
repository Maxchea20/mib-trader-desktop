#!/usr/bin/env bash
# Builds the backend into a single executable and drops it into
# desktop/src-tauri/binaries/ with the filename Tauri expects for a
# "sidecar" binary (name suffixed with the Rust target triple).
#
# Run this from the backend/ folder:
#   cd backend
#   bash scripts/build_sidecar.sh
#
# Requirements: Python 3.11+ with the project's requirements.txt already
# installed, plus `pyinstaller` (pip install pyinstaller --break-system-packages).
# Also needs `rustc` on PATH just to auto-detect your platform's target
# triple — it's already required for the Tauri build anyway.
set -euo pipefail

cd "$(dirname "$0")/.."   # -> backend/

if ! command -v pyinstaller >/dev/null 2>&1; then
  echo "pyinstaller not found. Install it first:"
  echo "  pip install pyinstaller --break-system-packages"
  exit 1
fi

if ! command -v rustc >/dev/null 2>&1; then
  echo "rustc not found on PATH. Install Rust first (https://rustup.rs) —"
  echo "you'll need it for the Tauri build step anyway."
  exit 1
fi

TARGET_TRIPLE="$(rustc -vV | sed -n 's/host: //p')"
echo "Detected target triple: $TARGET_TRIPLE"

echo "Building backend executable with PyInstaller (this can take a few minutes)..."
pyinstaller --onefile --name mib-backend \
  --collect-all uvicorn \
  --collect-all fastapi \
  --collect-all starlette \
  --collect-all pydantic \
  --collect-all pydantic_core \
  --collect-all websockets \
  --collect-all httpx \
  --collect-all numpy \
  --collect-all pandas \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols \
  --hidden-import uvicorn.protocols.http \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan \
  --hidden-import uvicorn.lifespan.on \
  run_server.py

OUT_DIR="../desktop/src-tauri/binaries"
mkdir -p "$OUT_DIR"
DEST="$OUT_DIR/mib-backend-${TARGET_TRIPLE}"
cp "dist/mib-backend" "$DEST"
chmod +x "$DEST"

echo ""
echo "Done. Backend sidecar built at:"
echo "  $DEST"
echo ""
echo "Next: run the Tauri build from the desktop/ folder (see BUILD_INSTRUCTIONS.md)."
