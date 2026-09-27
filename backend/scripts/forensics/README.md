# TEMPORARY forensic code — not part of the trading engine

Nothing in this folder is imported by `src/`, the app, or the live loop.
Delete the whole folder when the study is done.

* `hunt_fac4d0e/` — byte-for-byte copies of the Hunt C-FI brain modules from
  commit `fac4d0e` (last intact Hunt C-FI). The runner checks them against
  `git show fac4d0e:...` at start-up when git is available.
* `hunt_3d98c41/` — the same modules from `3d98c41`, Hunt C-FI BEFORE the S1/S2 timing layer
  (run with `--hunt 3d98c41`).
* `hunt_rearm_forensic.py` — replays those modules on `market_data_clean.db`
  as two books (A original, B re-arm disabled) and reports the difference.

```
cd backend
python scripts/forensics/hunt_rearm_forensic.py
python scripts/forensics/hunt_rearm_forensic.py --start 2026-08-01 --end 2026-09-20
```
