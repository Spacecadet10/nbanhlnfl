# Player props: design playbook and phased plan

Status: **design only** (2026-10-29). Not yet reviewed. No player-prop model, forecast, quote table, API endpoint, UI or
scheduled job exists. Nothing in this document changes a live model, a live definition, the release or the
data store. Player props were out of scope for `MULTISPORT_PLAN.md` §E and `MARKETS.md` (2026-10-03
prerequisites); this document is the design that would have to be reviewed before any of that changes.

Scope: NBA and NHL player props first. NFL, NCAAF, NCAAB, MLB and soccer are deferred with the reasons in
§8 (P5) and §4. The numbers in this document are either (a) observed read-only from the current stores on 2026-10-29,
marked *observed*, or (b) proposed defaults, marked *proposed*. There are no performance results here.

Related: [MARKETS.md](MARKETS.md) (market semantics and settlement), [MULTISPORT_PLAN.md](MULTISPORT_PLAN.md),
[EVALUATION_PLAN.md](EVALUATION_PLAN.md) (point-in-time rules), [DATA_SOURCES.md](DATA_SOURCES.md) (sources and
blockers), [PLAYER_RATINGS_STUDY.md](PLAYER_RATINGS_STUDY.md) (minutes/availability).

---

## 1. Operating principles

These replace the prose rules of a qualitative prop playbook. Each is a constraint on code, not advice.

1. **Opportunity first, exposure first.** A prop is `count | exposure`. Project the exposure distribution
   (minutes, TOI, attempts, plate appearances), not just its mean. Season averages are not an input.
2. **Market first.** The line is another estimate of the same quantity, and it already contains pace, matchup
   and injury news. Model the residual against the market-implied distribution; the market is the benchmark,
   never a "trap" to fade by rule.
3. **Distribution first.** Model counts (negative binomial / Poisson mixtures), not a point projection with a
   normal CDF. Pushes, DNP voids, 0.5 lines and tail behaviour all fall out of the distribution.
4. **Price discipline.** Edge is meaningful only at the quoted price, after push handling and vig. Require a
   lower confidence bound on EV, not a point estimate.
5. **Pass by default.** Most props are legs the system should not take. A bet needs positive evidence after
   all gates; "no bet" needs no justification.
6. **No promotion without forward evidence.** Historical stat data can validate the projection layer only.
   Betting edge is claimed only against forward quotes, with family-wise error control.
7. **Predictability is not edge.** A repeatable stat can be priced correctly. The product that matters is
   `predictability × market error × capacity` (see §2.3).

The last point is the most important correction to the original playbook and is documented in §2.

---

## 2. Evidence review of the playbook

### 2.1 The source of the soccer reliability numbers

The playbook's soccer section cites a study of 372,650 player starts. The numbers match StatPair's study,
[Which Football Stats Are Actually Predictable?](https://statpair.com/blog/most-predictable-football-stats-data-study)
(90 competitions, starts April 2025 – October 2026). That study is a vendor blog, not a peer-reviewed paper,
and its own limitations section says:

> "Split-half reliability measures stability, not profitability. A predictable stat can still be priced
> correctly by bookmakers."

The playbook then recommends *targeting* the most repeatable stats (shots 0.89, fouls won 0.84, tackles 0.82)
and *avoiding* goals (0.66) and cards (0.35). That converts a ranking of variance stability into a betting
rule. It is backwards: shots/SOG are among the markets books price most carefully, which is why they are
stable. Nothing in that study measures mispricing.

### 2.2 Claims and what to do with them

| Claim in the playbook | Verdict | Treatment |
|---|---|---|
| NCAAF usage lag beats "blended projections" by +2.22 fantasy points | unverified: "blended projections" is undefined and no primary source is given | a prior to reproduce on our data; not a parameter |
| Unders on top-quartile pass-yard lines | untested | registered hypothesis: residual by line quartile, pre-registered split |
| Unders on low rush-yard lines | internally inconsistent with "fade public overs" | separate hypothesis with a mechanism, or drop |
| Fade popular stars / public overs | folk belief with a plausible price-bias mechanism | test over/under rate and CLV by popularity bucket; if real it is a *price* bias, not a projection adjustment |
| Anytime touchdown / goalscorer is high variance | correct about variance | price it; do not ban it. Variance belongs in the confidence bound and stake |
| Baby lines (0.5) have no edge | wrong in general | a 0.5 line is mostly "does he play"; that is exactly the availability model's turf. Evaluate after vig |
| High NHL total ⇒ saves Over | plausible mechanism (shots faced) | joint model of shots faced and saves; test the residual, do not apply as a rule |
| "Sharp bettors bet unders on inflated star lines" | testable claim | needs line history and popularity proxy |
| Pick one sport/stat by viewing habits | wrong selection criterion | choose by data at cutoff, line history, capacity (§4) |

### 2.3 The edge model

```
edge = predictability × market error × capacity
```

- **Predictability** is necessary but not sufficient. It controls the variance of our estimate.
- **Market error** is where the money is: books under-resource some markets (limits, attention), move late
  on lineup/role news, shade popular sides, or leave stale lines. Each is a testable hypothesis, not a rule.
- **Capacity** is the expected dollars: edge per bet × the stake the market accepts. A 10% edge on a market
  with a $50 limit is worth $5/unit.

The system should therefore screen candidates on all three, not on a stability ranking.

---

## 3. Architecture

```
props snapshots (props_collect) ──> parser ──> prop quotes (append-only, exact line)
                                                     │
player identity ──> participation/minutes ──┐        │
                                            ▼        ▼
                              projection distribution   market-implied distribution
                                            └────┬─────┘
                                                 ▼
                                  edge / EV / LCB / Kelly  ──> selector (pass by default)
                                                 │
                                                 ▼
                                  paper trades, CLV, promotion gates
```

### 3.1 Layer firewall

Three layers, with information restrictions enforced in code and tests:

| Layer | May see | May not see |
|---|---|---|
| Projection | pre-cutoff player/team features, outcome history up to cutoff | the prop line (no fitting to the market), the game's result |
| Market | quotes, line movement, ladder, de-vig | outcomes, model projections |
| Selector | projection and market outputs | anything dated after the decision cutoff |

Why: if the projection model can see the line, it can learn to imitate it and the evaluation stops measuring
anything. If the selector can see outcomes, every backtest is invalid. This mirrors the sealed-model discipline
already used for game markets.

### 3.2 Exposure model (the dominant variance term)

```
P(active)              from injury report / confirmed lineup at cutoff
M | active             empirical minutes distribution by role bucket, shrunk; blowout adjustment
                       uses the *pregame* spread/total, never the realised margin
Y | M, rate            negative binomial (or Poisson-lognormal)
rate                   partial-pooled per-minute rate, matchup-adjusted
```

Rules:

- Minutes must be a distribution, not last-10 mean. The variance of minutes is where most prop miscalibration
  comes from.
- Blowout adjustment is a pregame feature (spread, total, win probability), never the final margin.
- If the player is inactive at the decision cutoff: no bet. If he is scratched after the bet: the ticket is
  void (settlement rule, §6).
- For NHL the exposure variable is time on ice; **TOI is not currently cached** (`DATA_SOURCES.md`,
  `PLAYER_RATINGS_STUDY.md`), so NHL families are blocked until the data-feasibility audit in Phase 0.

### 3.3 Rate and distribution

- Rate = per-minute (NBA) or per-90 (soccer) or per-game (NHL SOG, saves) rate, partially pooled across
  players (empirical Bayes) and adjusted for opponent, pace and role.
- Count distribution: negative binomial for overdispersed counts, Poisson for rare events, binomial for
  3PM-style attempts. Simulate the joint draw `(active, minutes, rate, count)`.
- Composite markets (PRA, points+rebounds) come from the same simulation, which gives their correlation for
  free. Never price a composite from a sum of independent means.
- This is also the mechanism that handles integer lines (push probability) and 0.5 lines correctly.

### 3.4 Market benchmark

The archived ESPN `propBets` payload contains a **ladder** per player and stat type: a main total plus
alternate lines, and a separate "Milestones" representation (`observed` 2026-10-29). The ladder is more
informative than a single line: it lets us recover the book's implied distribution.

Proposed market-layer steps:

1. Parse each quote into `(sport, game, athlete, type, line, price, open_price, provider lastUpdated,
   our retrieved_at)`. Store append-only at the exact line; a new line is a new quote.
2. Classify `type.name` into a family and a representation: total (over/under) vs milestone (`X+`). Do not
   assume semantics from the name alone; pin them against a sample of games before storage.
3. De-vig: proportional de-vig is the current game-market method (`odds.devig_two_way`); for props compare
   with Shin/power as a sensitivity check, because ladder prices are not symmetric around -110.
4. Recover the implied CDF from the ladder (monotone interpolation across lines), then compare the model CDF
   to the book CDF at every stored line. The main line is not special.
5. Store the exact quote used and its retrieval time. No historical prop lines exist for free
   (`DATA_SOURCES.md`), so all market evaluation is prospective. Do not synthesize a "closing line".

### 3.5 Edge, EV, staking

For a side with decimal price `d`, `p_win`, `p_push` (integer lines only), `p_loss`:

```
EV per unit       = p_win·(d − 1) − p_loss
push-aware Kelly  = EV / ((d − 1)·(1 − p_push))
```

Stake rules (proposed defaults, to be revised by sign-off, not by backtest peeking):

- use a **fraction** (e.g. 0.25) of Kelly computed on the **lower confidence bound** of EV, not the point EV;
- per-bet cap 2% of bankroll; per-game cap 5%; per-player cap 3%; daily cap 10% (proposed);
- `LCB(EV) > 0` is required after a minimum-EV margin; the margin and the LCB level are pre-registered per
  family, not chosen per slate;
- no parlays/SGPs in the first version; correlation is used to cap same-game exposure, not to build tickets.

The LCB comes from bootstrapping the projection simulation and the rate parameters (and, where available, the
market's own estimation noise). This replaces arbitrary "high-variance markets need 8–10% edge" rules: a
high-variance family has a wider LCB, so it needs a larger point edge automatically, with no magic constant.

### 3.6 The selector

- Inputs: model distribution, market ladder, price, stake caps, data-quality state.
- Output: `BET` or `PASS` with a reason code. Reason codes (proposed): `no_line`, `line_moved`,
  `insufficient_edge`, `lcb_not_positive`, `lineup_unconfirmed`, `stale_news`, `model_uncalibrated`,
  `correlated_exposure`, `limit_unavailable`, `data_missing`.
- Ranking, when a board is shown: fractional Kelly on the LCB (EV per unit of risk), not raw probability edge.
- "Confidence" is a calibrated probability that the bet beats break-even, not a letter grade.
- Every candidate and every pass is logged with its inputs and price, so opportunity cost can be measured.

---

## 4. Family registry

Status is the *current* state (2026-10-29), not a prediction. `blocked` means a named prerequisite is missing.

| Family | Sport | Outcome source | Exposure | Status | First blocker |
|---|---|---|---|---|---|
| Points | NBA | player boxes 2019-20 … 2023-24 (research); live summaries store minutes only, not stat outcomes | minutes | blocked | live 2026-27 outcome ingestion + minutes backtest |
| Rebounds | NBA | as points | minutes | blocked | same |
| Assists | NBA | as points | minutes | blocked | same |
| 3-pointers made | NBA | as points | minutes/attempts | blocked | same; binomial model |
| PRA (composite) | NBA | as points | minutes | deferred | depends on points/rebounds/assists; correlation-aware simulation |
| Steals, blocks | NBA | as points | minutes | deferred | low counts, high variance; later, stronger evidence bar |
| Shots on goal | NHL | play-by-play (cached 2015-26, ~92% of games) | TOI | blocked | TOI not cached; data-feasibility audit |
| Goalie saves | NHL | box/goalie summaries (feasibility unconfirmed) | shots faced / TOI | blocked | outcome source + starter confirmation |
| Goals, points | NHL | play-by-play | TOI | deferred | low reliability; later |
| Anytime goalscorer | NHL | play-by-play | — | deferred | high variance; needs the strongest gate |
| Other sports (NFL, NCAAF, NCAAB, MLB, soccer) | — | not collected | various | deferred | no free prop-line history, player identity/role data or settlement rules; see §5.1 |

YAML exemplars (one block per family once Phase 1 starts; this is the schema, not a loaded config):

```yaml
family: nba_points
sport: nba
outcome: player points, official ESPN box, first final
cutoff: tipoff - 2h (primary); re-run at confirmed lineups before start
exposure: [participation_probability, minutes_distribution]
rate: per-minute points, empirical-Bayes shrinkage, opponent and pace adjustment
distribution: negative binomial conditional on minutes
features: [recent_minutes, role_bucket, start_rate, usage_proxy, rest, back_to_back, opp_def_rating,
           pace, team_total_implied, injury_status, teammate_absences]
baselines: [season_avg, shrunk_recent_rate, minutes_x_rate]
market: ESPN propBets ladder; de-vigged implied distribution; main + alternate lines
settlement: DNP/0 min void; whole line push; OT counts; stat corrections window
gates: see section 7
status: blocked on outcome ingestion + minutes backtest
```

```yaml
family: nhl_sog
sport: nhl
outcome: player shots on goal, official box (shootout excluded)
cutoff: puck drop - 2h
exposure: [dressed, time_on_ice_distribution]
rate: shots per minute, partial pooling, opponent shots-allowed, score state
distribution: negative binomial
features: [recent_toi, line_role, power_play_unit, opp_shots_allowed, team_corsi_proxy, rest, score_state]
baselines: [season_avg, shrunk_recent_rate, toi_x_rate]
market: ESPN propBets ladder
settlement: DNP void; OT counts; shootout shots excluded; whole line push
gates: see section 7
status: blocked on TOI and outcome storage
```

---

## 5. Data reality and Phase 0 measurements

### 5.1 What is observed (read-only, 2026-10-29)

- **Snapshots exist.** `data/nba.sqlite` holds 221 `espn_props` raw responses from 2026-10-03 to 2026-10-29
  (three are "no props listed" 404 markers); `data/nhl.sqlite` holds 255. They are unparsed.
- **Payload shape (newest NBA sample):** `count = 1108`, `pageCount = 2`, but only **1000 items are stored** —
  `props_collect` requests `limit=1000` with no `pageIndex` loop, so roughly 10% of each snapshot is silently
  omitted. Any availability study must fix this first or it will understate coverage.
- **Fields:** per item `athlete.$ref` (ESPN athlete id), `type.name`, `odds.american/decimal/fraction`
  (value + open), `odds.total` (value + open), `lastUpdated`, `current.target`, `open.target`; team totals
  have no athlete. A sample game had 46 distinct types (main totals plus milestone/alternate grids).
- **NHL payload** (545–570 items on one page in the samples inspected) includes `Total Shots on Goal`, `Total Saves`,
  `Goalkeeper Saves Milestones`, goalscorer markets and period totals.
- **No historical prop lines exist for free** (`DATA_SOURCES.md`), so the betting layer can only be evaluated
  prospectively from the 2026-10-20 regular-season opener onward.

### 5.2 Phase 0 requirements (measurement only, no model)

1. **Pagination fix** in `props_collect`: request `pageIndex` 0..`pageCount-1`, keep the time budget, and
   record `count`/`pageCount` in the snapshot metadata.
2. **Capture windows:** add earlier snapshots (proposed: T−6h, T−2h, T−45m) so line movement and CLV are
   measurable; keep the per-run budget. Review the scheduler placement before changing it.
3. **Parser** (proposed `backend/app/props_lines.py`): payload → normalized quotes, tolerant of missing
   `athlete`, milestones and team totals; validate semantics against a sample of known games; append-only.
4. **Availability report** (no UI): per family, per game — share of games with a quote, median first-quote
   time before start, lines per player, main-line presence, price range, stale `lastUpdated` share.
5. **Identity mapping:** ESPN athlete ref → player id used by box scores; verify join rates per season.
6. **NBA outcome ingestion:** store per-player box stats as outcomes (currently the live summary path stores
   minutes for availability, not outcomes). Follow the existing idempotency and timestamp rules.
7. **NHL data-feasibility audit:** TOI, saves and dressed-lineup sources; no model until it passes.

Acceptance: a reproducible report and tests; still no prop forecast, API field or job.

---

## 6. Settlement and void rules (proposal — needs review before code)

`MARKETS.md` states prop settlement rules are not written. These are the proposed rules; they must be
reviewed, dated in `MARKETS.md`, and covered by tests before any prop forecast is stored.

| Rule | Proposal |
|---|---|
| Side / line identity | Every stored quote and forecast is at one exact line; a new line is a new row, never an update |
| Whole-number lines | Push is possible; stake refunded (`mid`), included in EV and CLV |
| DNP / did not dress / 0 minutes | Void, not Under (confirm per book) |
| Late scratch after the quote | Void if the bet was priced on a player who did not play |
| Overtime | NBA and NHL player stats include OT; shootout goals/shots are excluded from player stats |
| Stat corrections | Settle on first official box, marked provisional for a review window (proposed 24h); corrections append a new settlement row, never overwrite |
| Game moved / postponed | Void (existing game-market rule) |
| Player on a roster but team/stat missing | `unscorable`, never guessed |
| Milestones (`X+`) | Map to the equivalent over line (e.g. `10+` = over 9.5); validate the mapping in Phase 0 |
| Multiple books | Not available today (ESPN/DraftKings only); a single-book price is a lower bound on achievable EV |

---

## 7. Evaluation and promotion

### 7.1 Baselines (the ladder)

Every family is compared against the rungs before it, on held-out games:

| Rung | Content |
|---|---|
| B0 | season-to-date average per game |
| B1 | shrunk recent rate (empirical Bayes) |
| B2 | expected minutes × per-minute rate |
| B3 | B2 + matchup / pace / rest / injury / role |
| B4 | market-implied distribution (benchmark only, never a "model") |

Accept a rung only if it improves the proper score with a confidence interval excluding 0; otherwise stop.
The market benchmark is B4: if the model does not beat B4 out-of-sample, there is no edge to claim.

### 7.2 Metrics

- Proper scores: log loss / Brier for Over/Under; CRPS for the full distribution.
- Calibration: reliability curve, ECE, calibration slope, by family and season.
- CLV: mean and share > 0, against the last same-line quote at or before (proposed) start − 30 min; count
  "no CLV" when the line moved away, never impute.
- ROI and bankroll path: block-bootstrap CI clustered by game (props within a game are correlated), max
  drawdown, exposure concentration.
- Data quality: quote availability, parser error rate, outcome match rate, stat-correction rate.

### 7.3 Design rules

- Walk-forward only; refit on a fixed cadence (e.g. weekly) using results available at the cutoff; no
  train/eval overlap; no repeated test-season access outside the existing logged procedure.
- Pre-register the primary families and the gates before looking at forward data. Secondary families are
  exploratory and cannot be promoted on the same pass.
- Family-wise error control at promotion (Holm across families). Hierarchical shrinkage across families with
  similar structure (all "yards", all "shots") is preferred to independent fits.
- The selector is frozen before its evaluation window; every candidate is logged at decision time.

### 7.4 Promotion gates (provisional defaults — require sign-off)

| Gate | Requirement |
|---|---|
| G0 data | line available for ≥ 90% of games in the family; parser error < 0.5%; outcome coverage ≥ 99% |
| G1 projection | beats B2 (and B3 if present) on walk-forward, CI excludes 0; calibration slope 0.8–1.2; N ≥ pre-registered minimum |
| G2 market | positive CLV over ≥ 300 candidate bets with 95% CI > 0; LCB(EV) > 0 after price |
| G3 economics | positive paper ROI after realistic prices and limits; drawdown within the pre-set budget; no single game/player concentration |
| G4 display | family appears in any UI as "monitored/paper", clearly labelled Demo, never as a recommendation |

The thresholds are proposals. They are deliberately conservative; changing them after seeing results is a
protocol violation and must be recorded with the reason.

### 7.5 Kill rules

Demote a promoted family to paper if, over a pre-registered trailing window: CLV is negative, calibration
drifts outside its band, data quality fails G0, or a settlement-rule incident occurs. Demotion is automatic and
logged; no discretionary overrides.

---

## 8. Phased plan

| Phase | Deliverable | Modules (proposed) | Acceptance | Not in scope |
|---|---|---|---|---|
| P0 | Measurement: pagination, parser, availability report, identity map, NBA outcome ingestion, NHL feasibility audit | `props_collect.py`, new `props_lines.py`, `raw_cache.py`, `ingest.py`/`models` paths | Report reproducible; tests for parser and coverage | no forecast, no UI, no job changes without review |
| P1 | Participation/minutes model + backtest vs last-10 baseline | `availability.py`, `availability_live.py`, `player_ratings.py` | Minutes score beats baseline with CI; participation calibration | no prop stat model yet |
| P2 | Projection layer for one family (proposed: NBA points), walk-forward, calibration | new `props.py` model module; reuse `backtest.py`, `metrics.py` | Beats B0–B2; calibration report; no lines visible to the model | no betting evaluation |
| P3 | Market layer: ladder parse, de-vig, implied distribution, EV/LCB/Kelly, paper trades | new `props_market.py`; reuse `odds.py`, `markets.py` (extend), `paper_trading.py` | Shadow decisions recorded; CLV computed where possible; no money | no promotion |
| P4 | Gates and selector; dashboard surface (sport/market/player, Demo labels, loading/error/empty) | API + the existing four screens | Gate report; UI verified at desktop and phone widths | no claims of edge before G2–G3 |
| P5 | Second family, then second sport | same | same gates per family | other sports until one family passes |

P0 must not be skipped: the pagination gap alone means today's snapshots understate what is available.

---

## 9. Risks and what could make this wrong

- **Leakage:** player boxes in the research cache have no publish time; the live summary path is not a
  timestamped outcome feed. Any projection backtest must re-derive availability from the existing
  point-in-time rules and say so.
- **Line availability bias:** props are posted for a subset of players and games; missing quotes are not
  random (bench players, low-liquidity games). A model trained on quoted players only has selection bias.
- **Pagination bias:** the current collector omits page 2; all availability conclusions are biased until
  fixed.
- **Popularity bias:** if books shade popular players, the bias is in the price, not in the stat; test it as
  a price effect.
- **De-vig:** proportional de-vig is approximate on ladders with asymmetric prices; compare methods.
- **Vendor evidence:** the reliability study is a commercial blog and explicitly not about profitability.
- **Multiple testing:** ~20+ families × many players × daily. Promotion must be family-wise controlled, or
  the first promoted family will be a false discovery.
- **Settlement:** DNP/void/push/correction rules can dominate small edges; they must be tested before use.
- **Single book:** ESPN/DraftKings only; no line shopping, no true closing line, limits unknown.
- **Commercial terms:** ESPN data use is a personal-research decision (2026-09-30); extending collection
  frequency needs that review, not just a code change.

## 10. Open decisions (need sign-off, not code)

1. Bankroll, maximum stake and loss limits (the staking defaults in §3.5 assume a bankroll input).
2. Whether to buy a historical prop-line source (the only way to backtest the betting layer). Requires
   approval and a licence review; nothing here assumes it.
3. Which books/bet placement, if any. This design is paper-only.
4. Whether any UI ever says more than "paper/monitored" and, if so, under what gate.
5. NHL data-feasibility scope (TOI, saves, dressed lineups) and whether it is worth the effort before the NBA
   family passes its gates.

## 11. References

- StatPair, *Which Football Stats Are Actually Predictable?*
  https://statpair.com/blog/most-predictable-football-stats-data-study (vendor study; reliability ≠ edge).
- [MARKETS.md](MARKETS.md) — market specification, de-vig, settlement.
- [EVALUATION_PLAN.md](EVALUATION_PLAN.md) — point-in-time rules, test-season access.
- [DATA_SOURCES.md](DATA_SOURCES.md) — prop source, no historical prop lines.
- [PLAYER_RATINGS_STUDY.md](PLAYER_RATINGS_STUDY.md) — minutes/availability research.
- [MULTISPORT_PLAN.md](MULTISPORT_PLAN.md) — current scope and phase state.
