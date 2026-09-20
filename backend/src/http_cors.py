"""CORS origins for CRA dev and the Tauri webview. No trading logic."""
import os
from starlette.middleware.cors import CORSMiddleware

BASE = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "tauri://localhost",
    "https://tauri.localhost",
    "http://tauri.localhost",
    "https://tauri.localhost/",
]


def origins():
    extra = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    out = []
    for o in BASE + extra:
        if o not in out:
            out.append(o)
    return out


def attach(app):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
