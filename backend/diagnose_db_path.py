"""Run this from your backend/ folder (or anywhere on the same machine)
to see exactly why resolve_db_path() picks what it picks. Does not
connect to any exchange, does not modify anything."""
import sys
sys.path.insert(0, ".")
from pathlib import Path
from src.market_data.database import resolve_db_path, _ROOT, _data_dir

names = ("market_data_clean.db", "market_data.db")
roots = [
    ("backend/ (_ROOT)", _ROOT),
    ("project root (_ROOT.parent)", _ROOT.parent),
    ("OS app-data dir", _data_dir()),
    ("Downloads/.../backend", Path.home() / "Downloads" / "mib-trader-desktop-full" / "mib-trader-desktop" / "backend"),
]

print("Scanning all 4 known locations for both filenames:\n")
for label, root in roots:
    print(f"[{label}] -> {root}")
    for name in names:
        p = root / name
        if p.is_file():
            size = p.stat().st_size
            ok = "OK (>10KB, eligible)" if size > 10_000 else "TOO SMALL, ignored (<=10KB)"
            print(f"    {name}: EXISTS, {size:,} bytes -- {ok}")
        else:
            print(f"    {name}: not found")
    print()

print("resolve_db_path() would currently choose:")
print(" ", resolve_db_path())