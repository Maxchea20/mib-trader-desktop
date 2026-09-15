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


def _pick_db(folder: Path) -> Path:
    clean = folder / "market_data_clean.db"
    legacy = folder / "market_data.db"
    return clean if clean.exists() else legacy


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

    # Desktop data folder first (packaged app), then backend/.env (dev).
    # override=False so the first file that set a key wins.
    _load_env_file(data_dir / ".env")
    _load_env_file(backend_dir / ".env")

    if _is_frozen():
        os.environ.setdefault("MARKET_DB_PATH", str(_pick_db(data_dir)))
    else:
        os.environ.setdefault("MARKET_DB_PATH", str(_pick_db(backend_dir)))

    os.environ.setdefault("CORS_ORIGINS", "tauri://localhost,http://localhost:1420,http://localhost:3000")


def main() -> None:
    _ensure_defaults()
    import uvicorn
    from server import app

    port = int(os.environ.get("MIB_PORT", "8811"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
