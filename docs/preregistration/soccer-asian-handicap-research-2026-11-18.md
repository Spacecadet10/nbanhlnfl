# Fixed rules: research-only Asian handicap prices from the sealed goals model vs stored DraftKings handicap quotes (pre-registered 2026-11-18, DRAFT)

Status: **DRAFT, not binding.** Binding from the commit that first adds this file to `docs/preregistration/`.
**Research only, never served** (owner decision 2026-11-18 Q2): no AH price, pick, comparison or record appears on any page,
card, ticket, API response or Telegram message, and no AH row is written to the soccer store. Serving stays blocked
(`soccer_book.py` docstring: "Handicap quotes are not compared"). A result here permits only a separate proposal.

Binding sha256: `PENDING-AT-COMMIT` (this line stays exactly as written; the sha256 of the committed bytes goes in the commit
message, HANDOFF.md and the research script's `PREREG_SHA256` constant).

## 0. Question and limits, stated first
- **Question.** Priced from the sealed goals joint distribution (Poisson, candidate sha256
  `cfe78bf98a8e48215474de26d020fc5a0fdb45830957b38e24e47db88efbd432`, `soccer_markets_live.CANDIDATES['goals']`, protocol
  `soccer_markets_protocol.json` v1), are the app's goal-handicap probabilities as good as DraftKings' de-vigged handicap
  prices stored at the same time, on new matches?
- No new model, no fit, no tuning: the AH price is an exact function of the stored joint grid (`soccer_market_forecasts.
  joint_json`) and the settlement rules below. The goals family's reserved test was read once (2026-11-10) on outcomes only;
  "no proven skill vs bookmaker" applies to every goals market. Expect the book to be at least as good.
- Settled by the pre-T0 structure check (section 11): every stored DraftKings soccer handicap line is a half line, two-way
  (home/away only), paired at −line/+line. So in practice this is a two-way half-line handicap with no push; the whole and
  quarter rules stay defined and are applied if such lines appear after T0, but no stored quote exercises them yet.
- The joint's last cell per axis is "that count or more" (goals 10+); it is graded as exactly 10, as every served goals
  market does. The error is negligible at the handicap lines DraftKings lists.
- Power (ROUGH, assumed): per-match weighted LL difference SD ≈ 0.08. At 800 paired matches SE ≈ 0.0028, MDE (two-sided
  5%, 80%) ≈ 0.008.

## 1. Population and eligibility
- Competitions: eng.1, esp.1, ger.1, ita.1, fra.1, uefa.champions (uefa.europa is a model input only, never evaluated).
- Matches: league regular season, or UEFA league phase, as `soccer_markets_eval.eligible_stage` defines it (protocol
  `eligibility`). UEFA knockout matches are excluded entirely (ESPN scores may then include extra time), so inclusion never
  depends on the outcome.
- Counted match: kickoff after T0, and its goals forecast `generated_at` ≥ T0 (T0 = committer timestamp of the commit that
  first adds this file).

## 2. Information at decision time
- **Forecast** = the `soccer_market_forecasts` row with `family='goals'`, `candidate_sha256` = the pinned sha above, highest
  `version` for the match (each version is generated at or before the 06:00 UTC cutoff of the match's local date, before
  kickoff; schema checks `generated_at <= cutoff_at`). Decision time t_d = its `generated_at`.
- **Book quote at t_d** (DraftKings, `odds.same_provider`, every spelling one book): from `soccer_odds_quotes`,
  `market='handicap'`, `price_kind='current'`, per side the latest row with `retrieved_at <= t_d` and `retrieved_at <
  kickoff` (highest `quote_id` among them), exactly the per-side rule of `soccer_book.latest_1x2` with market `handicap`.
  The store appends a side only when it changes and writes an `offered=0` row when a side disappears, so:
  - both sides must be offered (`offered=1`);
  - **paired line** required: line_away = −line_home (|sum| < 1e-9) and line_home a multiple of 0.25;
  - otherwise "unpaired" (counted, excluded).
  q_home = `odds.devig_two_way(american_home, american_away)[0]` (proportional; sensitivity: power de-vig as defined in
  best-pick v4 section 6).
- **Close quote** (secondary): the same rule with t = kickoff − 1 s.
- Nothing later than t_d enters the primary comparison.

## 3. AH pricing and settlement (quarter lines split the stake)
- Settlement is `soccer_markets.grade_handicap(home, away, line, side)` on the **90-minute score** (regulation plus
  stoppage): half line wins/loses; whole line pushes on equality; quarter line (x.25, x.75) splits the stake over the two
  neighbouring half-steps (x.25 = x.0 and x.5; x.75 = x.5 and x+1.0), so results are win, half_win, push, half_loss, loss
  (`soccer_markets.RESULTS`). Per unit stake at decimal d: d, (d+1)/2, 1, 1/2, 0 (`return_factor`).
- Model probabilities: for the home side at line L, P(r) = Σ_{h,a} joint[h][a] · 1[grade_handicap(h, a, L, 'home') = r]
  (the same sum `soccer_markets.probabilities` does for `corners_handicap`; goals have no handicap entry in `COUNT_MARKETS`,
  so the research code calls `grade_handicap` directly, without editing served modules).
- App two-way probability (push removed, consistent with a two-way de-vig): p_home = `soccer_book.app_share(P)` =
  (win + half_win/2) / (win + half_win/2 + loss + half_loss/2).
- **Scored outcome:** for the realised result r, weight w = 1 (win, loss), 1/2 (half_win, half_loss), 0 (push);
  y = 1 (win, half_win), 0 (loss, half_loss). Then E[w·y]/E[w] = app_share, so app_share is the probability this scoring
  rewards, and a half result counts as half an observation.
- Unit check before T0 (synthetic, no data): at L = −0.5, p_home = P(home win) from the same joint; at L = 0, p_home =
  P(home win)/(P(home win)+P(away win)); home and away shares sum to 1 at paired lines.

## 4. Settlement data
`soccer_matches` latest result revision read at evaluation time: `status='final'`, `decided_in='regulation'`, `home_score`,
`away_score`. postponed/canceled/abandoned → void. `decided_in` in (extra_time, penalties, unknown) → excluded (unknown
ending), counted. Not final 7 days after kickoff → excluded, counted.

## 5. Metrics (all reported)
- **Primary:** weighted log loss, LL(P) = −Σ_m w_m [y_m ln P_m + (1 − y_m) ln(1 − P_m)] / Σ_m w_m, P clipped to
  [1e-6, 1 − 1e-6], for the app (p_home) and the book (q_home), on the paired set; Δ = LL(app) − LL(book). Scored from the
  home side (two-way, so symmetric).
- Weighted Brier; calibration intercept/slope (weighted logistic of y on logit p); reliability table, 5 bins.
- By line type: the primary table reports half lines only; whole and quarter lines (none stored before T0, section 11) are
  reported as counts, and their metrics are shown only if each type reaches 100 decided paired matches. By competition,
  and by |line| bins 0.5 / 1.5 / ≥ 2.5 (the values stored; any whole or quarter |line| goes in the bin of its nearest
  half-line below, e.g. 1.0 and 1.25 with 0.5): descriptive. With half lines only, every decided match has w = 1, so the
  weighted metrics equal the unweighted ones.
- Flat-stake return (descriptive, never a claim): 1 unit on the side with app_share − q ≥ 0 (home ties), at DraftKings'
  decimal price of that side at t_d, returns by `return_factor`.
- Line-movement (secondary): on matches whose close pair has the same line as the t_d pair, CLV = q_close − q_td for the side
  the app favours (app_share ≥ q); lines differing: counted, not converted.
- Counts: forecasts, paired / unpaired / no quote, by line type; voids, unknown endings, past 7 days.

## 6. Inference
Cluster = (competition, local match date). Cluster bootstrap B = 10,000, `default_rng(20261118)`, 95% percentile CI of Δ.
- **Primary (single hypothesis, two-sided α = 0.05):** H0: Δ = 0. "App better than DraftKings" if the CI lies entirely below
  0; "DraftKings better" if entirely above 0; otherwise "not shown".
- **Secondary (Holm over 2, α = 0.05):** S1 mean CLV of the app-favoured side > 0 (one-sided cluster-bootstrap p);
  S2 weighted calibration slope of the app = 1 vs ≠ 1 (two-sided).
- Everything else descriptive.

## 7. Sample size, horizon, stopping
- Single look at the **earlier** of 800 decided paired matches (w > 0) or kickoff date 2027-05-31; analysed after every
  counted match is final or past 7 days.
- Fewer than 300 decided paired matches at the deadline: reported, labelled "underpowered (n of 800)", no primary verdict.
- **No interim looks**: before the look only counts (forecasts, paired quotes, by line type) may be shown.
- **No extension**: 800 and 2027-05-31 are fixed.

## 8. Exclusions (counted)
Unpaired or missing quotes; lines not a multiple of 0.25; knockout UEFA matches; void; unknown/extra-time endings; pushes
(w = 0, scored as nothing); past 7 days; forecasts not under the pinned candidate sha.

## 9. What would and would not be claimed
- At most: "on these competitions and this period, the sealed goals model's AH prices were [better / worse / not shown
  different] than DraftKings' at the forecast time", with Δ and its CI.
- Never: profit; serving readiness (serving needs a separate owner decision and its own review); anything about corner
  handicap, 1X2, totals, other books, or other competitions.

## 10. Integrity, pre-T0 check and deviations
- **Pre-T0 structure-only check** (precedent: protocol amendment 1): done 2026-11-18, results in section 11. It changed only
  the reporting of line types (section 5), never the metrics.
- Research script: separate file outside served modules, synthetic tests, then a **fresh, independent Opus review
  (read-only)** of the script against this frozen document, recorded in HANDOFF.md with reviewer, model, UTC time, reviewed
  commit and file sha256 and `PREREG_SHA256`, before it reads any counted outcome (any later change needs a new review
  entry first); read-only on a copy (`NBA_DATA_DIR`); output to the research data directory, never the soccer store.
- Deviations: dated addendum, never after consulting any counted outcome, applying to later matches only. A new goals
  candidate (new sha) does not count here; it needs its own preregistration.

## 11. Addendum (pre-T0, 2026-11-18T14:21Z): structure-only check of stored DraftKings handicap quotes
- Source: a `.backup` copy of the live `soccer.sqlite` taken 2026-11-18T14:21:17Z under the drafts directory, opened
  immutable, deleted after the queries. Table `soccer_odds_quotes` (migration 0040), `market = 'handicap'`. Columns read:
  `match_id`, `provider`, `side`, `price_kind`, `offered`, `line`, `retrieved_at` only. **Not read**: `american`,
  `decimal_odds`, any result, score or match status.
- Rows: 1,630, all provider DraftKings (one provider in the table), 201 matches, retrieved 2026-11-15T16:43Z to
  2026-11-18T13:25Z (about three days). `current`: 605 home + 623 away; `open`: 201 + 201. No `offered = 0` rows.
- Sides: only `home`/`away` (the schema allows no draw side for `handicap`): the market is two-way.
- Line types (offered rows): half 1,630; whole 0; quarter 0; not a multiple of 0.25: 0. Distinct |line|: 0.5, 1.5, 2.5, 3.5,
  4.5, 5.5.
- Pairing: rows with an opposite-side row at the identical `retrieved_at`: `current` 1,092 of 1,228 (88.9%), `open` 402 of 402;
  every one of those has line_away = −line_home (0 mismatches). As-of pairing (the section 2 rule, latest row per side at
  each distinct `current` retrieval time): 682 of 682 states paired, 0 one-sided, 0 line mismatches, 0 withdrawn. The
  unpaired same-time rows are one-side-only changes, which the append-on-change store writes alone.
- In-scope competitions (section 1): 111 matches; `current` 405 rows, `open` 222 rows, all half lines.
- Verdict: two-way Asian-style handicap at half lines only, not a 3-way/European handicap. **The draft is feasible as
  written**; only the line-type reporting was amended (section 5). Limitation: three days of quote history; whether
  DraftKings later lists whole/quarter lines is unknown and handled by section 5.

## 12. Previously inspected data (disclosed before T0)
- The goals family's reserved test (2026-11-10) read outcomes of pre-T0 matches; served soccer scorecards may have been
  viewed. The section 11 check read quote structure only, no prices or outcomes.
- None of these enters the analysis: a match counts only if its kickoff is after T0 and its goals forecast
  `generated_at >= T0` (section 1). No pre-T0 match is pooled, used as a baseline or used to set a threshold.

## 13. Specification checklist (where each item is fixed)
T0 rule and eligible forecasts: sections 1–2. Comparisons: section 2 (app vs DraftKings at t_d). Metrics: section 5.
Sample and stopping: section 7 (no extension). Missing data (no quote, unpaired, withdrawn side, not final after 7 days):
sections 2, 4, 8, counted, never imputed. Pushes (w = 0) and half results: section 3; voids and unknown endings: section 4.
