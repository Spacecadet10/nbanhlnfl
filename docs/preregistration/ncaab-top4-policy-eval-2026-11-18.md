# Fixed rules: NCAAB top-4 card selection policy, evaluated separately from forecast quality (pre-registered 2026-11-18)

Status: **Binding** from the commit that first adds this file to `docs/preregistration/`. Paper only:
nothing here changes a card, a forecast, a grade, the selection rule or a record. Don't edit after T0; any change is a
dated addendum that applies only to days after the addendum (section 13).

Binding sha256: `PENDING-AT-COMMIT` (a file cannot contain its own hash: this line stays exactly as written; the sha256 of the
committed bytes is written in the commit message, in HANDOFF.md, and as the constant `PREREG_SHA256` of the evaluation
script, which refuses to run on any other value).

## 0. Question and limits, stated first
- **Question.** Given the shipped NCAAB model and the day's candidate set, does choosing the games with the largest
  app-minus-sportsbook gap (rule `ncaab-top4-gap-v1`, `backend/app/ncaab_cards.py`) pick bets that the market later moves
  toward (CLV), and that win more often than the sportsbook expected at selection time (excess)? Is it better than choosing
  at random from the same candidates, and do larger gaps carry more CLV/excess?
- **Separate from forecast quality.** This is a test of the *policy* (which games and sides are chosen) on fixed, stored
  candidate sets. Whether the model's probabilities are good (log loss, calibration vs the book) is a different question,
  answered elsewhere (Phase 3 learning_eval); no forecast-quality claim follows from this test and vice versa.
- **Prior expectation.** The one-time NCAAB test (2026-11-17) found DraftKings' after-game benchmark beats the model
  (LL .5734 on 2,339 vs the model's on the same games). A large positive gap is therefore at least as likely to mark model
  error as edge. The module itself says "UNTESTED". Expect "not shown"; a negative slope on gap is a plausible result.
- **Power (ROUGH, normal approximation, assumed spreads, nothing measured).** 60 days × up to 4 picks ≈ 240 P0 picks.
  Excess has per-pick SD ≈ 0.48, so SE ≈ 0.031 and the one-sided 5%/80% minimum detectable excess is ≈ +7.7 points. CLV
  per pick SD is unknown; if it is ≈ 2.5 points, SE ≈ 0.16 points and the MDE ≈ 0.4 points. CLV is the powered metric;
  excess is reported with the expectation that it will be inconclusive.
- **Assumptions.** Outcomes are independent across games given the quotes; the closing no-vig price is a better estimate
  of the true probability than the 11:00 ET price (the standard CLV premise; it is an assumption, not tested here); every
  saved selection row is an unselected stream (one row per ET day, append-only, `INSERT OR IGNORE`).

## 1. Units, time, information
- **T0** = committer timestamp of the commit that first adds this file
  (`git log --diff-filter=A --format=%cI -- docs/preregistration/ncaab-top4-policy-eval-2026-11-18.md`).
- **Source of selections:** table `ncaab_card_selections` in the delivery ledger `.telegram-delivery.sqlite` (schema
  `ncaab_cards.SCHEMA`; append-only by trigger). One row per `et_date`.
- **Qualifying day:** all hold:
  1. `rule = 'ncaab-top4-gap-v1'` and `status = 'ok'`;
  2. `selected_at >= T0`;
  3. `selected_at`, converted to America/Toronto (`ncaab_cards.EASTERN`), is in [11:00:00, 11:15:00] on `et_date`
     (late selections, e.g. 2026-11-17 at 23:26Z = 18:26 ET, are excluded: a later selection sees closer-to-close prices,
     which shrinks CLV by construction);
  4. at least one candidate has a non-null game `gap` (a "priced candidate").
  Non-qualifying days are counted by reason and never analysed. An ET day with no row at all (the tick did not run) is
  counted as "no selection row"; a `status = 'error'` row is counted as "selection error". Neither is imputed or re-run.
- **Decision-time information** = the row's `candidates` JSON only (written at `selected_at` by `ncaab_cards.compute`, with
  the model as of `selected_at` and quotes retrieved at or before it, at most 6 h old). No policy below may use anything
  else: no later quote, no forecast, no result.
- **Post-decision measurements** (allowed only as outcomes): the closing quote (section 3) and the result (section 4).
- **Candidate-game unit (for every policy):** one priced candidate game `g` of a qualifying day, with one chosen market
  entry `m(g)` (defined per policy), its `side` (a/b), `line` (null for winner), selection-time book probability
  `q_sel` and `quote_id`, `provider`, `quote_retrieved_at`, and the candidate's `tipoff_at`.
- `q_sel` = the stored `book` of the entry; the evaluator re-reads `market_quotes.p_a_devig` of the stored `quote_id`
  (side a: p, side b: 1 − p) and flags (counts; does not exclude) any |difference| > 1e-6 as a data-integrity finding.

## 2. Policies (all on the same qualifying days and the same priced candidates)
Ties everywhere: earlier `tipoff_at`, then lower `game_id` (the rule's own order). "Best entry" of a game = the stored
`best_market` entry (largest gap; tie: market order winner, spread, total, as `compute` does). Fewer qualifying games than 4
→ take all that qualify.
| id | games chosen per day | entry evaluated per game |
|---|---|---|
| P0 stored | the stored `selected` list (must equal the replay: game gap > 0, top 4 by gap; a mismatch is a recorder defect, section 10) | best entry |
| P1 random-4 ×1000 | 1,000 independent draws per day of min(4, n_priced) games uniformly without replacement from all priced candidates; numpy `default_rng(20261118)`, days in `et_date` order, candidates sorted by (`tipoff_at`, `game_id`), one `rng.choice(n, k, replace=False)` per day per replicate, replicate-major order | best entry |
| P2 top-4 confidence | 4 priced games with the largest `app` over their priced entries | the entry with that largest `app` (tie: market order) |
| P3 all | every priced candidate | best entry |
| P4 bottom-4 gap | 4 priced games with the smallest game `gap` (may be negative) | best entry |
| P5 winner only | 4 games with the largest winner-entry `gap` > 0 | winner entry |
| P6 capped | 4 games with largest game `gap` with 0 < gap < 20.0 | best entry |
| P7 none | nothing | — (every per-day sum is 0; the zero reference) |

### 2A. Unit of P0 (owner decision 2026-11-18: Alternative A)
The unit of P0 is the 11:00 ET selection's own recorded best market, side and line (section 2 table), priced at `q_sel` from
the selection's stored quote. Each counted selection keeps the forecast and quote references stored with it at selection
time (`candidates` JSON: forecast/quote ids, provider, `quote_retrieved_at`); the evaluator never substitutes a later forecast
or quote for them. The card's sent pick is not a confirmatory unit.
*Practical effect.* Measures the rule (which games and which market/side the gap points at, as of 11:00 ET), not what the
subscriber received. Multiplicity: primary Holm over 2, secondary Holm over 3. Power as section 0 (about 240 picks; CLV MDE
about 0.4 points if SD about 2.5; excess MDE about 7.7 points).

### 2B. Operational audit of selected vs delivered picks (descriptive, not a test)
Reported alongside the evaluation and never part of any hypothesis or family: for each P0 game of a qualifying day, whether
a card was delivered (`status = 'sent'`), and whether the delivered card's market, side and line equal the selection's
entry, using the structured sent-card records where they exist (published/best-pick rows linked to the delivery); where no
structured record exists the game is counted as "delivered pick not structurally recorded" rather than inferred from message
text (no text matcher unless a later, separately reviewed change makes one necessary). Counts only; no CLV, excess or win
figures are computed for delivered picks.
Selection-rule results are never presented as evidence about the performance of cards as delivered. A formal evaluation of
delivered cards, if wanted later, needs its own preregistration frozen before its own evaluation period.

## 3. Closing line (CLV source), from the code as it stands
- Quotes: NCAAB store table `market_quotes` (migration 0042 schema). Fetched by the NCAAB odds cycle (`jobs.odds_snapshot`
  → `ncaab_app.ingest_day`, the D-I day scoreboard's DraftKings block), cadence `jobs.ODDS_CYCLE_EVERY['ncaab']` = 30 min,
  each row stamped with its own `retrieved_at`. There is no stored "closing" row: the close is defined here.
- **Close time** `t_c` = the candidate's stored `tipoff_at` (if the game's start changed, the game is void: section 4).
- **Close quote** = the latest row with the same game, same market, **same sportsbook as the selection quote**
  (`odds.same_provider`, i.e. "Draft Kings" = "DraftKings"), `retrieved_at < t_c`:
  - winner: the row `odds.winner_quote(conn, 'ncaab', game_id, at, provider=<selection provider>)` returns with
    `at` = `t_c` − 1 s (latest `retrieved_at`, `price_label='current'` first, then highest `quote_id`);
  - spread/total: the row `market_live.latest_quote` would return at `t_c` − 1 s restricted to that provider
    (`price_label='current'`, latest `retrieved_at`, then highest `quote_id`).
- **Fallback (none across books):** no same-provider row → "no close" (counted; excluded from CLV, kept for excess). There
  is no cross-book fallback whatever the coverage. **Close coverage** (share of P0 picks with a non-stale same-provider
  close; also for P1–P6) is reported. Coverage below 80% of selected sides is reported as a limitation of the CLV result;
  it is not a reason to change this rule, the close source, or the staleness limit. A close
  retrieved earlier than `t_c` − 60 min → "stale close" (counted; excluded from primary CLV; included in a sensitivity). A close
  whose `quote_id` equals the selection quote → CLV = 0 (valid: no new price was offered).
- **Closing no-vig probability of the selected side**, `q_close`:
  - winner, or spread/total whose close `line` equals the selection `line`: `p_a_devig` (side a) or `1 − p_a_devig` (side b).
  - spread/total whose line moved: normal line conversion with frozen constants from
    `backend/app/models/ncaab_frozen_decision.json` `sigmas`: σ_spread = 11.319247889236534 (`eff_margin`, the shipped spread
    model's), σ_total = 17.762583342156923 (`points_total`, the shipped total model's). With p_c = close `p_a_devig`, L_c =
    close line, L_s = selection line, Φ the standard normal CDF:
    - spread (side a = home covers, `markets.settle`: home − away + line > 0): μ = σ·Φ⁻¹(p_c) − L_c;
      P(a at L_s) = Φ((μ + L_s)/σ);
    - total (side a = over): μ = L_c + σ·Φ⁻¹(p_c); P(a at L_s) = Φ((μ − L_s)/σ);
    - side b = 1 − P(a). Push mass is ignored (continuous approximation), as the two-way de-vig also ignores it.
- **CLV** = q_close − q_sel (probability points ×100 when reported). **Points CLV** (sensitivity, lined markets only): spread
  side a: L_s − L_c, side b: L_c − L_s; total over: L_c − L_s, under: L_s − L_c.

## 4. Settlement and excess
- Result: NCAAB `games` row read at evaluation time (`status`, `home_score`, `away_score`). Grade = `markets.settle(market,
  line_sel, status=, home=, away=, tipoff_at=<games.tipoff_at>, forecast_tipoff_at=<candidate tipoff_at>)`. Side won: grade
  equals the entry's side.
- Decided = grade in {a, b}. `mid` (push), `void` (postponed/canceled or start changed), `unscorable`: excluded from excess and
  from CLV, counted. A game with no final grade 7 days after its `tipoff_at` is excluded and counted.
- **Excess** = 1[side won] − q_sel.

## 5. Metrics (every one reported for every policy, whatever the result)
- Per policy: n picks, n days; mean CLV (pooled over picks), mean points CLV; mean excess; hit rate (Wilson 95%); mean q_sel;
  per-day sums of CLV and excess (P7 = 0).
- Per market (winner/spread/total) and per model_version, descriptive.
- Data counts: non-qualifying days by reason; picks with no close, stale close, line-converted close, voids, pushes,
  past-7-day; close coverage (section 3); integrity flags (q_sel mismatch, P0 replay mismatch).

## 6. Hypotheses (one look, at the stop point)
Inference unit = day. Day-cluster bootstrap: B = 10,000, `default_rng(20261118)`, resample qualifying days with replacement,
recompute the pooled mean; one-sided p = (1 + #{θ*_b ≤ 0}) / (B + 1); 95% percentile CI reported.
- **Primary family (Holm, α = 0.05 over 2):**
  - **H1 CLV:** mean CLV of P0 > 0 (primary CLV set: non-stale closes, line conversion included).
  - **H2 excess (co-primary):** mean excess of P0 > 0. Reported with its stated MDE (≈ +7.7 points at 240 picks, section
    0); a non-rejection is reported as "not shown (underpowered for excess: MDE ≈ 7.7 points)".
- **Secondary confirmatory family (Holm, α = 0.05 over 3, separate from the primary family; no gatekeeping):**
  - **H3 selection beats random:** pooled mean CLV of P0 > pooled mean CLV of P1. Test: conditional randomisation on the fixed
    candidate sets and outcomes: p = (1 + #{replicates r: CLV(P1_r) ≥ CLV(P0)}) / (1 + 1,000). Excess version reported with
    the same p, not counted in the family.
  - **H4 CLV slope:** on all P3 units, OLS slope of CLV on game `gap` (points) > 0. Day-cluster bootstrap p as above.
  - **H5 excess slope:** same, excess on `gap` > 0.
- **Descriptive only (no success claim):** P2, P4, P5, P6 vs P0 and vs P1; P0 − P4 contrast; per market; per model_version;
  sensitivity sets (stale closes included; same-line closes only; points CLV); a binned table of CLV and excess by gap
  (≤0, 0–5, 5–10, 10–20, ≥20) with n.

## 7. Sample size, horizon, stopping
- Stop at the **earlier** of: the 60th qualifying day (by `et_date`), or `et_date` 2027-03-15. The analysis runs once, after
  every game of the last counted day is final or past its 7-day limit.
- Below 60 qualifying days at the deadline: analysed and reported, labelled "short of planned n (k of 60 days)".
- **No extension.** Neither the 60-day count nor the 2027-03-15 date may be extended or shortened for any reason, including
  low power for excess or low close coverage. Further evidence needs a new preregistration counted on later days.
- **No interim looks.** Before the stop, reports may show only counts (qualifying days, candidates, selected); never CLV,
  excess, hit rates or slopes of any policy. Game results and forecast grades remain visible in the app as they are today;
  what is forbidden is computing any quantity in sections 5–6 on counted days before the stop.
- A change of `RULE` in `ncaab_cards.py` ends counting at the last day under `ncaab-top4-gap-v1`; the stop is then that day.

## 8. Exclusions (all counted and reported)
Days: not qualifying (section 1). Games: unpriced candidates (no game gap); void; push; unscorable; past 7 days. CLV only:
no close, stale close (primary set). Nothing is imputed.

## 9. What would and would not be claimed
- **Policy "shown to add value"** only if H1 is rejected (Holm) and H3 is rejected (Holm). H2 alone, without H1, is reported
  as "outcomes better than the book's selection-time price; not confirmed by line movement".
- **Shown false** for CLV if the 95% CI of mean CLV(P0) lies entirely below 0; for the gap premise if the H4 slope CI lies
  entirely below 0. Otherwise "not shown", never "shown false".
- Never claimed: profit or ROI (no price/vig model is part of this test); forecast quality of the model; anything about the
  card's actually-sent pick (the card shows the official two-hour forecast, whose market/side may differ from the
  selection's best entry; that is out of scope here); anything about other sports or other rules.
- A positive result permits proposing that the rule be kept, as a separate owner decision. A negative or null result
  permits proposing a different rule only as a new preregistration counted on later days.

## 10. Integrity
- Evaluation code (Phase 3 increment I4) is written and tested on synthetic data only, then reviewed by a **fresh,
  independent Opus session, read-only**, against this frozen document (by its binding sha256), before it ever reads a
  counted day's closes or results. The review's HANDOFF.md entry records: reviewer and model, UTC time, the evaluator
  commit and file sha256 reviewed, `PREREG_SHA256`, findings and their resolution. Any change to the evaluator after that
  review needs a new review entry before the next outcome read. It hard-codes
  `PREREG_SHA256`, reads stores read-only on a copy (`NBA_DATA_DIR`), never `data/` directly, and writes its report outside
  the live stores.
- P0 replay mismatch or q_sel integrity failure on any day: that day is excluded whole, the defect written in HANDOFF.md,
  decided before any result of that day is read.
- `model_version` may change during the window (planned NCAAB refits): the policy is "top 4 by gap of whatever model is
  shipped"; days are stratified by model_version descriptively; no change stops the evaluation.

## 11. Previously inspected data (disclosed before T0)
- A weekly report viewed before this document was frozen showed NCAAB outcome counts: 275 decided picks through 2026-11-18.
  The one-time NCAAB test of 2026-11-17 (section 0) also read outcomes. Selection rows of 2026-11-17 and 2026-11-18 exist.
- None of these enters the analysis: a day counts only if `selected_at >= T0` (section 1), so every counted selection, close
  and result is later than T0, and every counted game tips off after its selection. No pre-T0 selection, pick or game is
  pooled, used as a baseline, or used to set a threshold, and no rule in this document may be justified by those figures.
- Structure-only facts used here (not outcomes): the rule's code, the store schemas, the odds cadence.

## 12. Specification checklist (where each item is fixed)
T0 rule: section 1. Eligible forecasts (selections): section 1, qualifying day. Comparisons: sections 2, 2A, 6. Metrics:
section 5. Sample and stopping: section 7 (no extension). Missing data (no selection row, error row, unpriced candidate,
no close, stale close, not final after 7 days): sections 1, 3, 4, 8, counted, never imputed. Pushes (`mid`), voids and
unscorable: section 4, excluded from CLV and excess, counted.

## 13. Deviations
Any deviation (code path changed, quote source changed, a definition found ambiguous) is a dated addendum committed before
any affected day's result is read, never after consulting any counted day's outcome or close, applying to later days only
unless it is a pure data-quality fix with no outcome read (then it states its effect on counts only). Amendments before T0
are new versions (v2, …) with the superseded file kept byte-identical, as in best-pick v1–v4.
