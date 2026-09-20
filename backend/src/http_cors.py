"""CORS for CRA dev and the Tauri WebView2.

The engine listens on 127.0.0.1:8811. The UI origin is a different scheme
(https://tauri.localhost / https://asset.localhost / tauri://localhost),
so PUT /api/autotrade always sends an OPTIONS preflight first.

Starlette CORSMiddleware returns 400 on a disallowed preflight origin.
A 400 here is exactly the "toggle Off snaps back to On" bug: GET status
still works, PUT never runs, the 3s poll restores enabled=true.

Do not stack two CORS middlewares with different origin lists. Attach once.
Local engine does not use cookies — credentials stay off so '*' is valid.
"""
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
    "https://asset.localhost",
    "http://asset.localhost",
    "null",
]

# Any localhost / Tauri / WebView2 asset origin, with or without port.
ORIGIN_RE = (
    r"^(https?://(localhost|127\.0\.0\.1|tauri\.localhost|asset\.localhost)"
    r"(:\d+)?|tauri://localhost|https?://tauri\.localhost|null)$"
)


def origins():
    extra = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    out = []
    for o in BASE + extra:
        if o not in out:
            out.append(o)
    return out


def attach(app):
    if getattr(app.state, "cors_attached", False):
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_origin_regex=ORIGIN_RE,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.cors_attached = True
