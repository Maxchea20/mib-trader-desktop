"""Standalone entrypoint for the packaged desktop build."""
import os
import sys
from pathlib import Path


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home()))
        return Path(base) / "mib-trader"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mib-trader"
    return Path.home() / ".local" / "share" / "mib-trader"


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _usable(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 10_000
    except OSError:
        return False


def _pick_db(folder: Path) -> Path:
    clean = folder / "market_data_clean.db"
    legacy = folder / "market_data.db"
    if _usable(clean):
        return clean
    if _usable(legacy):
        return legacy
    return clean


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
    except Exception:
        pass


def _ensure_defaults() -> None:
    backend_dir = Path(__file__).resolve().parent
    data_dir = _default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    _load_env_file(data_dir / ".env")
    _load_env_file(backend_dir / ".env")

    picked = _pick_db(data_dir if _is_frozen() else backend_dir)
    # Always prefer a real clean DB over an env path that points at empty market_data.db
    env_path = Path(os.environ.get("MARKET_DB_PATH", "") or "")
    if _usable(picked) and picked.name == "market_data_clean.db":
        os.environ["MARKET_DB_PATH"] = str(picked)
    elif not _usable(env_path):
        os.environ["MARKET_DB_PATH"] = str(picked)

    print(f"[db] MARKET_DB_PATH={os.environ.get('MARKET_DB_PATH')}", flush=True)

    os.environ.setdefault(
        "CORS_ORIGINS",
        ",".join([
            "tauri://localhost",
            "https://tauri.localhost",
            "http://tauri.localhost",
            "https://asset.localhost",
            "http://asset.localhost",
            "http://localhost:1420",
            "http://127.0.0.1:1420",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]),
    )


def main() -> None:
    _ensure_defaults()
    import uvicorn
    from server import app
    from src import http_cors

    # Idempotent — server.py already attached the same stack.
    http_cors.attach(app)

    port = int(os.environ.get("MIB_PORT", "8811"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
