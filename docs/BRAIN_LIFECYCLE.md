# Brain lifecycle (dev/brain-lifecycle)

Recovered spec slice. **Not on main. Do not merge until reviewed.**

One Brain owns PRE-ENTRY → FIRE → HOLD / TRAIL / EXIT.

## This branch

- `backend/src/brain/lifecycle.py`
  - `Thesis` recorded at FIRE
  - size from equity → risk → SL distance
  - `reevaluate()` → HOLD / TRAIL / EXIT
- No votes. No separate trade manager.
- `evaluate_hunt()` on main is unchanged.

## After-FIRE rules (V1)

EXIT when:
- 15m CHoCH against the thesis
- 1h trend flips against
- entry level lost
- hard SL
- target reached

TRAIL when:
- price ≥ +1R → protect at least BE, then 1 ATR behind best
- trail only tightens

HOLD when thesis still valid.

## Not in V1 (later slices)

- Structural HL/LH trail from structure swings
- FVG / S/R as TP
- Volume / order-book re-read
- Time stop / fail-to-move scalp clock
- Wiring into live execution
- Frontend

## Proof

`backend/tests/test_brain_lifecycle.py`
