# Market specification and settlement

Recorded 2026-09-30, before any market forecast was generated. Code: `backend/app/markets.py` (`SPEC`, `settle`).
Changes must be dated here. Nothing in this document claims a betting edge or expected profit.

## Shared rules
- **Sides.** Every market has side `a`, an optional middle `mid` and side `b`. A forecast stores `p_a`, `p_mid`, `p_b`
  (each in [0, 1], summing to 1 within 1e-6) for one game, market and **exact line**. A later line move is a different
  forecast; it never changes an earlier one.
- **Line convention.** `spread` lines are the **home** handicap: home covers when `home_margin + line > 0`
  (`line = -1.5`: home must win by 2+). `total` and team-total lines are the points/goals threshold.
- **Final score.** Settlement uses the official final score including overtime. For the NHL this includes the one goal
  credited to a shootout winner (verified on 2024-25: the 77 regular-season games with `period = 5` equal the 77
  shootout wins, and their stored finals are e.g. 3-2), which is how sportsbooks settle puck lines and totals.
- **Policies.** `pregame` (night-before: cutoff 00:00 America/New_York on the game's Eastern date) and `near_tipoff`
  (cutoff = scheduled start; manual; refused after the start, with stale or missing results). Both exist per sport.
- **Line source.** `market`: the line of the latest stored bookmaker quote retrieved at or before generation.
  `model`: no quote existed; the line is the model's own median rounded to the market's step (half-point/half-goal), and
  the app labels the forecast "model-only line". A bookmaker line or price is never invented.
- **Market comparison.** Only quotes for the same game, market and line, retrieved at or before the forecast's
  generation time. Two-way markets are de-vigged proportionally (`p_i = q_i / (q_a + q_b)`). A three-way market is
  de-vigged over its three prices only when all three are quoted; two-way normalisation is never applied to it.
- **Outcomes.** `win` (side a), `loss` (side b), `push`/`draw` (mid), plus:
  - `pending`: the game is not final yet.
  - `void`: the game was postponed or canceled, or its scheduled start changed at all after the forecast was generated
    (any amount; until 2026-10-02 the rule was "more than 12 hours"; no stored live forecast had a moved start then, so no
    recorded settlement changed). The forecast's information no longer describes the game that is played. New forecasts are made for
    the rescheduled game.
  - `unscorable`: final, but data required for this market is missing (for example the NHL decision type); reported,
    never guessed.
  Suspended/abandoned games stay `pending` until the source marks them final, postponed or canceled.

## Market table
| Sport | Market id | Sides (a / mid / b) | Line | OT / SO | Settlement data | Quotes (free) |
|---|---|---|---|---|---|---|
| NBA | `winner` | home / – / away | – | OT counts | final score | ESPN DraftKings moneyline |
| NBA | `spread` | home covers / push / away covers | home handicap | OT counts | final score | ESPN `pointSpread` |
| NBA | `total` | over / push / under | points | OT counts | final score | ESPN `total` |
| NBA | `team_total_home`, `team_total_away` | over / push / under | points | OT counts | final score | none found (model-only) |
| NHL | `winner` | home / – / away | – | OT and SO count | final score | ESPN DraftKings moneyline (two-way) |
| NHL | `reg3` | home / draw / away | – | regulation only: any OT/SO game is a draw | final score + decision (REG/OT/SO) | none found (model-only) |
| NHL | `spread` (puck line) | home covers / push / away covers | home handicap (usually ±1.5) | OT and SO count (SO goal included) | final score | ESPN `pointSpread` |
| NHL | `total` | over / push / under | goals | OT and SO count (SO goal included) | final score | ESPN `total` |
| NHL | `team_total_home`, `team_total_away` | over / push / under | goals | OT and SO count | final score | none found (model-only) |

Period/quarter markets and player props are **not implemented**; see docs/DATA_SOURCES.md for the exact blockers.

## Distribution consistency checks (enforced in code and tests)
- All markets of one game and policy come from one predicted score distribution, so `winner`, `spread`, `total` and team
  totals cannot contradict each other.
- `P(over L)` is non-increasing in `L`; `P(home covers L)` is non-decreasing in `L`; `p_a + p_mid + p_b = 1`;
  half-point lines have `p_mid = 0`.
- NBA `winner` forecasts in the existing ledger (frozen Elo) remain the primary NBA winner record. The distribution model's
  own winner probability is **not stored** (corrected 2026-10-02: `market_forecasts` has no `winner` market) and never
  replaces them.

## Changes
- **2026-10-02, model-only line choice:** a model-only line is now the half-point line where the model's two sides are closest to 50/50
  (previously the median rounded down + 0.5). The old rule put NHL team totals at 3.5 goals with "under" near 70% for almost every team, so
  the pick reflected the line's placement, not the game. Forecasts already recorded keep their lines (append-only); new forecasts at a new
  line are separate rows. Market-quoted lines are unaffected.
- **2026-10-02, research candidates:** each line / 3-way market now also has two research candidates and combinations
  (docs/MODEL_SELECTION.md). They are recorded at exactly the primary forecast's line, line source and quote in the same run,
  as `research-v3:<model>:<definition>-window=2h-policy=near_tipoff`; the primary market forecast is unchanged.
