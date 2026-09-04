# Builds the backend into a single executable and drops it into
# desktop/src-tauri/binaries/ with the filename Tauri expects for a
# "sidecar" binary (name suffixed with the Rust target triple).
#
# Run this from the backend/ folder in PowerShell:
#   cd backend
#   .\scripts\build_sidecar.ps1
#
# Requirements: Python 3.11+ with requirements.txt installed, plus
# `pyinstaller` (pip install pyinstaller). Also needs `rustc` on PATH
# (you'll need it for the Tauri build step anyway).

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")   # -> backend/

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "pyinstaller not found. Install it first:"
    Write-Host "  pip install pyinstaller"
    exit 1
}

if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
    Write-Host "rustc not found on PATH. Install Rust first (https://rustup.rs) -"
    Write-Host "you'll need it for the Tauri build step anyway."
    exit 1
}

$hostLine = (rustc -vV | Select-String "^host:")
$TargetTriple = $hostLine.ToString().Split(" ")[1].Trim()
Write-Host "Detected target triple: $TargetTriple"

Write-Host "Building backend executable with PyInstaller (this can take a few minutes)..."
pyinstaller --onefile --name mib-backend `
  --collect-all uvicorn `
  --collect-all fastapi `
  --collect-all starlette `
  --collect-all pydantic `
  --collect-all pydantic_core `
  --collect-all websockets `
  --collect-all httpx `
  --collect-all numpy `
  --collect-all pandas `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols `
  --hidden-import uvicorn.protocols.http `
  --hidden-import uvicorn.protocols.http.auto `
  --hidden-import uvicorn.protocols.websockets `
  --hidden-import uvicorn.protocols.websockets.auto `
  --hidden-import uvicorn.lifespan `
  --hidden-import uvicorn.lifespan.on `
  run_server.py

$OutDir = "..\desktop\src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Dest = Join-Path $OutDir "mib-backend-$TargetTriple.exe"
Copy-Item "dist\mib-backend.exe" $Dest -Force

Write-Host ""
Write-Host "Done. Backend sidecar built at:"
Write-Host "  $Dest"
Write-Host ""
Write-Host "Next: run the Tauri build from the desktop\ folder (see BUILD_INSTRUCTIONS.md)."
