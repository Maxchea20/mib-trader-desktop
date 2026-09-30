"""List the chat models your OPENAI_API_KEY can actually use (read-only).

  .\\.venv\\Scripts\\python.exe scripts\\swing_ai_models.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)
    if os.environ.get("APPDATA"):
        load_dotenv(os.path.join(os.environ["APPDATA"], "mib-trader", ".env"), override=False)
except Exception:
    pass
from src.swing_ai import service  # noqa: E402

r = service.accessible_models(force=True)
print(f"OPENAI_API_KEY in use ends with {r['key_tail']}")
if r["error"]:
    print("error:", r["error"])
else:
    print("models this key can use:")
    for m in r["models"]:
        print("  ", m)
