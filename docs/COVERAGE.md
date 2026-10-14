# Every-game coverage (coverage-v2)

Requirement (user, 2026-10-13): every NBA and NHL game — preseason, regular season, play-in, playoffs — gets a pregame
prediction for every supported market, and every recorded prediction is graded once its result is final. Confidence
thresholds only filter recommendations and threshold views; they never decide what is recorded or graded.

## Recording (`coverage.record`, ledger `game_forecasts`, migration 0029)
- Runs last in every two-hour cycle (`jobs.predict_now`), also when the primary step fails. The cycle now opens for any
  forecastable game type (`coverage.FORECAST_TYPES`: preseason, regular, play_in, postseason); exhibition games
  (`season_type='other'`, All-Star events) are not forecast and are counted as such.
- One row per game and market per cycle outcome, from the first source that can give a probability (order
  `coverage-v2` since 2026-10-14, recorded on every row as `order_version`; `coverage-v1` rows stay as recorded):
  1. `adaptive` — an APPROVED adaptive definition's latest forecast for the game and market made in its two-hour window.
     Product decision (2026-10-14): no adaptive definition is approved for canonical use; a forecast row, a valid fit or
     a successful retrain is not approval. `coverage._adaptive_rejected` is that explicit "no approvals exist" policy, not
     an approval system (no approval format, registry or configuration exists). Every adaptive forecast row (weekly or
     daily definition) is therefore skipped and named in the next source's `fallback_reason` ("definition … (weekly
     schedule) forecast, not approved for canonical use …"); adaptive forecasts stay research (`adaptive_forecasts`,
     unchanged). The coverage report's reading of games before every-game recording (`_existing`) applies the same rule.
     Open (user, 2026-10-14): the promotion rule (D1), approval scope = exact definition + operating policy (D2), NBA
     missing-injury semantics (D3, separate task), fallback-subset coverage (D4); see HANDOFF.
  2. `primary_recorded` — the sealed primary model's two-hour forecast as recorded by the primary writers (near-tipoff Elo
     for the winner; the sealed market model for the other markets).
  3. `primary_direct` — the sealed primary computed for this ledger (games the primary writers do not cover, e.g.
     preseason), with the primary writers' guards: no forecast from stale results (last refresh older than 60 min) or
     while an earlier game of either team has no final result. Results of competitive games only; preseason results never
     update the models.
  4. otherwise a `missing` row (`source='none'`, no probability) with every source's reason.
- Each row records the source, the source row (`source_table`, `source_id`, `source_generated_at`), the model version,
  why earlier sources were not used (`fallback_reason`), the exact line and its origin (`line_source`: `bookmaker` = the
  latest quote at that line, `model` = a model-generated half-point line; `none` for winner/3-way), the quote id, and the
  confidence (favoured side's probability; two-way markets conditional on no push). Missing odds never block a row.
- Append-only (no update/delete), rows only within two hours before the start of a still-scheduled game, idempotent
  (unchanged inputs return the stored row), every game and market isolated (an exception becomes that cell's `missing`
  row with the error; the others are recorded).
- Nothing trains on this ledger: ensemble training reads `adaptive_forecasts` only (`adaptive.prospective_rows`), so
  fallback rows can never enter it, and fallback and preseason records are reported apart from the ensemble's.

## Grading and the report (`coverage.games_report`, `GET /api/coverage`, Performance page card)
- Computed on read from the stored results (`coverage.grade` on `markets.settle`): re-reading never grades twice and a
  corrected result re-grades once (counted as `corrected_results`). States: win / loss / push, pending, void (postponed,
  canceled, or the start moved after the forecast), unscorable (no score, no NHL regulation decision, no line), and
  unresolved (still not final 48 h after the start, or a status the source never resolves).
- A start-time edit received after a game is final no longer moves `tipoff_at` (ingest), so it can no longer void the
  forecasts graded for the game that was played.
- Per Eastern date range (≤ 31 days): scheduled games by type and status; the source schedule re-parsed from the raw cache
  and compared with the store (missing games, field differences); per market forecast / main / fallback / missing and
  grade counts; missing reasons; the unfiltered record (all, main, fallback; preseason separately) and the same record
  by confidence threshold (filter only); per game the canonical cell; and every stored row of every ledger graded (all
  rows, no threshold; retrospective ledgers flagged).
- Games before every-game recording began show the pregame forecast the existing ledgers hold (an approved adaptive
  ensemble — none is, see Recording — then the two-hour primary, then the discontinued night-before primary). Nothing is reconstructed; a game none of them covered
  stays missing, and a post-start reconstruction is only flagged as "retrospective available".

## Canonical forecast (read path; 2026-10-14, NBA winner on the pages so far)
- `coverage.canonical(conn, games, market, as_of)` is the one read path behind Today (`/api/games?policy=canonical`,
  each game's `canonical`), the game page (`canonical`), History (`/api/canonical/forecasts`) and Performance
  (`/api/canonical/performance`). The UI shows it for the NBA winner market; NHL and the other markets are unchanged.
- Selection: the latest `forecast` row made before the start for the game's current start (else for an earlier start, which
  grades void). A later `missing` row never replaces it; it is shown as `later_failure`. Without a forecast row: the
  latest `missing` reason, else "not recorded: no forecast cycle ran…" (a missed run), `upcoming`, or `not_recorded`
  (before recording began: legacy forecasts stay in the legacy views and are never mixed in).
- Preseason is `unvalidated` on every page: no probability, the reason shown (the sealed models were validated on
  regular-season and playoff games only). Its experimental fallback rows stay in the ledger, unchanged, and out of the
  record.
- Performance: every started competitive game since recording began is forecast or missing; missing = coverage gap, never
  a loss; metrics over settled forecasts only; market comparison only with a quote retrieved by the forecast's time.
- Identity: a cycle whose decision equals the latest row is a retry; a different decision (including A → B → A) is a new
  row (revision key, as `market_live`), so the latest row is always the latest decision.
- Coverage card (`games_report`): NBA winner cells use the same selection (`_select`): record id, grade and `later_failure`
  match the canonical pages; preseason is `unvalidated` with the stored row under `experimental` ("Experimental /
  unvalidated"). Other markets and NHL still show the latest row made before the start (a later missing row hides an
  earlier forecast there: open). Every preseason row is labelled experimental; its record is never in the accepted one.
- Today (canonical): the confidence filter hides only forecasts below the level; games without a canonical forecast are
  always listed with their reason, counted by state (`selection.without_forecast`, `below_threshold`).

## Not done / limits
- The adaptive ensemble itself does not forecast preseason (its members train on competitive games); preseason is covered
  by the sealed primary and graded separately.
- No stored grade history: corrections are visible as a count, the old score only in the raw cache.
- `game_decisions` (NHL) has no availability time; a changed decision is caught by the adaptive results digest (members
  missing, then a retrain is due), not by a correction record.
