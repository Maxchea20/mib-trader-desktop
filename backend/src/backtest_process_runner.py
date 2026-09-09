"""Process-isolated walk-forward backtest runner.

Moves the CPU-heavy bar-by-bar simulation from a background THREAD
(sharing the FastAPI server's own GIL) to a genuinely separate OS
PROCESS. The web server's own event loop is now completely unaffected
by however heavy the agent computation gets, since a separate process
has its own independent GIL entirely — this was the actual root cause
behind status polls stalling/timing out under heavy backtest load.

Uses multiprocessing's 'spawn' start method explicitly (Windows' only
option, and what this app runs on) rather than relying on Linux's
'fork' default. This also happens to be what makes the existing
database.py module-level connection safe to use completely unchanged:
spawn re-imports every module fresh in the child process rather than
inheriting the parent's memory, so each process gets its own
independent `_conn` starting at None — never an inherited, already-open
connection object from the parent. No changes needed there.

Does NOT touch backtest_walkforward.py's actual trading/agent/Brain
computation. The only addition to that file is a single optional
progress_callback hook (a no-op unless provided) — this module is what
actually uses it.
"""
from __future__ import annotations

import multiprocessing as mp
import queue as queue_module
import time
import traceback
from typing import Dict, List, Optional

MAX_RUNS_KEPT = 3
PROGRESS_PUSH_INTERVAL_S = 0.3  # throttle — avoids IPC overhead on every
# single bar of a multi-thousand-bar backtest. Purely a reporting-
# frequency choice, independent of the actual trading calculation.


def _worker_main(run_id: str, params: Dict, progress_queue, stop_event) -> None:
    """Runs entirely in the CHILD process. Must stay a genuine top-level,
    module-level function (not a closure/bound method) — Windows' spawn
    needs to pickle this by reference and re-import it in the child."""
    try:
        # Imported here rather than at module top level, so merely
        # importing THIS module from the parent (e.g. at server.py
        # startup) never itself triggers heavy imports or any premature
        # execution — matches "avoid process-starting code at import time."
        from .backtest_walkforward import WalkForwardBacktest

        progress_queue.put({"type": "started", "run_id": run_id})
        last_push = [0.0]

        def _on_progress(bt):
            # Bridges the stop_event (the only thing visible from the
            # parent process) into bt's own existing, UNCHANGED _stop
            # flag, which the bar loop already checks once per bar. No
            # new stop mechanism — just wiring the existing one across
            # the process boundary.
            if stop_event.is_set():
                bt._stop = True
            now = time.time()
            if now - last_push[0] >= PROGRESS_PUSH_INTERVAL_S:
                last_push[0] = now
                progress_queue.put({"type": "progress", "run_id": run_id, "progress": bt.progress})

        bt = WalkForwardBacktest(**params, progress_callback=_on_progress)
        bt.run()  # unchanged trading engine — same call as the old thread path

        final_type = "done" if bt.status == "done" else ("stopped" if bt.status == "stopped" else "error")
        progress_queue.put({
            "type": final_type, "run_id": run_id, "progress": bt.progress,
            "result": bt.result, "error": bt.error,
        })
    except Exception as e:
        # Any exception anywhere above — including inside the trading
        # engine itself — is reported as a real, visible error, never
        # silently lost. This is what makes "worker crashed" distinct
        # from "still running" on the parent side.
        progress_queue.put({
            "type": "error", "run_id": run_id,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        })


class _RunState:
    def __init__(self):
        self.status = "pending"
        self.progress = 0.0
        self.error: Optional[str] = None
        self.result: Optional[Dict] = None
        self.logged = False  # only set True on an actual successful
        # walkforward_log.record() — same "don't silently give up
        # forever after one transient failure" fix already applied to
        # the thread-based path, carried over here too.


class ProcessBacktestRunner:
    """Process-based replacement for the old thread-based BacktestRunner.
    Same run_id lifecycle and external shape (start/status/result/stop),
    so server.py's endpoints change minimally."""

    def __init__(self):
        self._ctx = mp.get_context("spawn")  # explicit, not relying on
        # any platform default — Windows only has spawn anyway, but
        # being explicit means this behaves identically wherever it's
        # tested rather than silently exercising fork's different (and
        # here, not what we want verified) semantics.
        self.states: Dict[str, _RunState] = {}
        self.processes: Dict[str, object] = {}
        self.queues: Dict[str, object] = {}
        self.stop_events: Dict[str, object] = {}
        self.order: List[str] = []

    def start(self, run_id: str, params: Dict) -> None:
        state = _RunState()
        q = self._ctx.Queue()
        stop_event = self._ctx.Event()
        proc = self._ctx.Process(
            target=_worker_main, args=(run_id, params, q, stop_event),
            daemon=True, name=f"wfbt-{run_id}",
        )

        self.states[run_id] = state
        self.queues[run_id] = q
        self.stop_events[run_id] = stop_event
        self.processes[run_id] = proc
        self.order.append(run_id)

        for old in self.order[:-MAX_RUNS_KEPT]:
            self._cleanup(old)
        self.order = self.order[-MAX_RUNS_KEPT:]

        proc.start()
        state.status = "running"

    def _cleanup(self, run_id: str) -> None:
        proc = self.processes.pop(run_id, None)
        if proc is not None and proc.is_alive():
            proc.terminate()
        self.queues.pop(run_id, None)
        self.stop_events.pop(run_id, None)
        self.states.pop(run_id, None)

    def _drain(self, run_id: str) -> None:
        """Non-blocking — pulls whatever progress/completion messages
        have arrived since the last check and updates this run's state.
        Called from the (lightweight, async) status/result endpoints,
        never doing any heavy work itself."""
        state = self.states.get(run_id)
        q = self.queues.get(run_id)
        if state is None or q is None:
            return
        while True:
            try:
                msg = q.get_nowait()
            except queue_module.Empty:
                break
            except (EOFError, OSError):
                break
            mtype = msg.get("type")
            if mtype == "started":
                continue
            if mtype == "progress":
                state.progress = msg.get("progress", state.progress)
            elif mtype in ("done", "stopped", "error"):
                state.status = mtype
                state.progress = msg.get("progress", state.progress)
                state.result = msg.get("result")
                state.error = msg.get("error")

        # Crash detection (spec section 11): the process genuinely exited
        # without ever sending a terminal message — e.g. a hard crash
        # during Python startup in the child, before _worker_main's own
        # try/except had a chance to run at all. Without this check the
        # UI would show "running" forever with no way to know.
        proc = self.processes.get(run_id)
        if proc is not None and not proc.is_alive() and state.status == "running":
            state.status = "error"
            state.error = "Backtest worker exited unexpectedly."

    def get_status(self, run_id: str) -> Optional[Dict]:
        if run_id not in self.states:
            return None
        self._drain(run_id)
        state = self.states[run_id]
        return {"id": run_id, "status": state.status, "progress": round(state.progress, 3), "error": state.error}

    def get_result(self, run_id: str) -> Optional[Dict]:
        if run_id not in self.states:
            return None
        self._drain(run_id)
        state = self.states[run_id]
        if state.status not in ("done", "error", "stopped"):
            return {"error": "not_finished", "status": state.status, "progress": round(state.progress, 3)}
        if state.error:
            return {"error": state.error}
        return state.result

    def mark_logged(self, run_id: str) -> None:
        state = self.states.get(run_id)
        if state is not None:
            state.logged = True

    def is_logged(self, run_id: str) -> bool:
        state = self.states.get(run_id)
        return bool(state and state.logged)

    def stop(self, run_id: str) -> bool:
        if run_id not in self.stop_events:
            return False
        self.stop_events[run_id].set()
        return True


runner = ProcessBacktestRunner()