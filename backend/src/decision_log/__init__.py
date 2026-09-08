"""Decision & Market State Logging System.

Purely observational. Nothing in this package can change a trading
decision, agent score, threshold, execution, or timing — every call site
into this package is wrapped in try/except at the call site itself, so a
bug here can never take down the trading loop.

See db.py for the SQLite schema, setup_tracker.py for M15 setup_id
lifecycle, risk_assessor.py for the (log-only, non-blocking) risk
metrics, explain.py for human-readable explanation generation, and
logger.py for the orchestration that ties it all together.
"""
# Explicitly import submodules here so `from . import decision_log` +
# `decision_log.logger.observe(...)` works from any call site, in any
# import order. Python does NOT automatically expose submodules as
# attributes of a package just because the package itself was imported —
# only explicitly importing a submodule (here, or anywhere earlier in
# the process) registers it as an attribute. Without this, every call
# site relying on `decision_log.logger.xxx()` after only `from . import
# decision_log` would silently AttributeError and get swallowed by the
# try/except wrapping it — exactly the bug this line exists to prevent.
from . import db
from . import setup_tracker
from . import risk_assessor
from . import explain
from . import logger