"""Standalone entrypoint for the packaged desktop build.

server.py defines the FastAPI app but expects to be run via
`uvicorn server:app` from a normal Python environment with a working
directory next to it. Neither is true once this is frozen into a single
executable by PyInstaller — there is no "next to it", the binary unpacks
itself into a temp folder on every launch.

This file exists so PyInstaller has one self-contained script to build
from, and so persistent data (SQLite DB, .env-equivalent config) lives in
a real OS data folder instead of vanishing when the temp extraction
folder is cleaned up.

The desktop app (Tauri) is responsible for setting these env vars before
launching this as a sidecar process:
  MARKET_DB_PATH   -> <app data dir>/market_data.db
  MEXC_API_KEY      -> from the user's saved settings (optional)
  MEXC_API_SECRET   -> from the user's saved settings (optional)
  CORS_ORIGINS      -> tauri://localhost (or the dev server origin)
  MIB_PORT          -> port to bind (defaults to 8811 below)

If you're running this manually outside the desktop app (e.g. testing the
sidecar build directly), it falls back to sane defaults so it still works.
"""
import os
import sys
from pathlib import Path


def _default_data_dir() -> Path:
    """Best-effort OS-appropriate data folder, used only if the desktop
    host didn't already set MARKET_DB_PATH (e.g. running this .exe by hand
    to sanity-check the build before wiring it into Tauri)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home()))
        return Path(base) / "mib-trader"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mib-trader"
    return Path.home() / ".local" / "share" / "mib-trader"


def _ensure_defaults() -> None:
    data_dir = _default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("MARKET_DB_PATH", str(data_dir / "market_data.db"))
    os.environ.setdefault("CORS_ORIGINS", "tauri://localhost,http://localhost:1420")

    # server.py calls load_dotenv() against a path next to itself, which
    # is meaningless inside a frozen bundle. If the desktop host wrote a
    # real .env-style file into the data dir (for locally-testing this
    # binary outside Tauri), load it from there instead.
    env_file = data_dir / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except Exception:
            pass


def main() -> None:
    _ensure_defaults()
    import uvicorn
    # Import after env defaults are set, since server.py reads some of
    # them (CORS_ORIGINS) at import time.
    from server import app

    port = int(os.environ.get("MIB_PORT", "8811"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
