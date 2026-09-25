"""Fail-open wrappers. Learning must never raise into Hunt / Isolated."""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger("mib.learning_brain")


def fail_open(default: Any = None) -> Callable:
    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.exception("learning_brain.%s failed (fail-open)", fn.__name__)
                return default

        return wrapped

    return deco
