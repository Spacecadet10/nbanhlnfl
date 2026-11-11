# Proposed change to the ticket reviewer skill (NOT applied)

The installed skill is `~/.codex/skills/courtside-ticket-review/SKILL.md`, sha256 `d7bd95bf…` on 2026-11-09. Its hash is
pinned in every snapshot. This file is not applied and stays outside the release until the user gives the go.

It must change together with the branch's `tickets.py`. With this branch live and the old skill:
- the reviewer would be told "never offer totals, BTTS, double chance or any other soccer market";
- the snapshot would list such selections anyway;
- each would need an accounting note under the coverage rule.

## Replace, in the snapshot paragraph (line 24)

**Old:**

> market `1x2_regulation` only. Each soccer pick is the model's most likely 90-minute outcome … never offer totals,
> BTTS, double chance or any other soccer market, and never treat fixtures without a live forecast as predictions.

**New:**

> Soccer markets:
> - `1x2_regulation`: the model's most likely 90-minute outcome, unchanged.
> - `double_chance` and `draw_no_bet` from that same 1X2 forecast. A draw-no-bet draw is a push.
> - For a tested goal or corner family only:
>   - `goals_total`, `goals_home`, `goals_away`, `btts`;
>   - `corners_total`, `corners_home`, `corners_away`, `corners_handicap`.
>   - Each selection is at the DraftKings line when ESPN lists one (match goals only). Otherwise it is at the half line next
>     to the model's fair line, labelled as the app's own line.
>   - Corners never have a sportsbook price in the source.
>
> Soccer market selections use the string forecast IDs exactly as the snapshot lists them, for example
> `goals:17:goals_total:over:2.5`. The 1X2 picks keep their numeric IDs.
>
> `probability` is the chance the leg wins outright. A push returns the stake. A quarter line can settle as a half win or
> half loss, and the ticket is then graded `partial`. `outcome_probabilities` has all five results.
>
> No soccer goal or corner market has proven skill against the bookmaker: disclose it, never call one a best pick. One
> match can still give only one leg per day (shared-game rule): choose its market deliberately.
>
> Never treat fixtures without a live forecast as predictions.

## Replace, in the plan paragraph (line 43)

**Old:** "Use actual numeric forecast IDs."

**New:** "Use the forecast IDs exactly as listed in the snapshot: numeric for NBA/NHL/NFL and soccer 1X2, the listed
string for soccer market selections."
