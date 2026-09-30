"""Swing AI: a separate, paper-first experiment.  The AI reasons over a multi-timeframe market snapshot and returns
LONG / SHORT / NO_TRADE with entry, SL and TP; deterministic code validates, sizes and (paper) executes.

Isolated by design: nothing here imports Hunt, S1/S2, the scalp engine or Trend Break, and nothing outside this
package imports it except the opt-in startup hook and read-only routes in server.py.  Delete this folder to remove it."""
