# Addendum 2026-11-18 (DRAFT): NCAAB top-4 evaluator clarifications

Applies to `ncaab-top4-policy-eval-2026-11-18.md` (sha256 55b8734a28eba1f56804810516fbf96ee1f0df6c2b7ce7c4dd1e6baa22bb9f3b).
Written during the section 10 read-only evaluator review, before any outcome, close or result of a counted day was read
(the review used synthetic fixtures only). Each item resolves an ambiguity or conflict in the frozen text; no hypothesis,
metric, sample, seed, threshold or date changes. Under section 13 these are pure definition clarifications with no outcome
read and apply from T0.

1. **q_sel integrity (sections 1 vs 10 conflict).** Section 10 governs: a day on which any entry evaluated by any section 2
   policy has |q_sel − re-read p_a_devig (side-adjusted)| > 1e-6, or whose stored `quote_id` is missing from the store copy,
   is excluded whole and counted as "q_sel integrity failure". The check runs at day classification, from the ledger row
   and the referenced quote rows only (no close, no result), so it is decided before any result of that day is read.
   Section 1's "flags (counts; does not exclude)" is read as: every mismatch is always counted and reported, and has no
   effect beyond section 10's day rule.
2. **Stale versus same-quote close (section 3).** Staleness is tested first, on the close row's `retrieved_at`, for every
   close. A same-`quote_id` close that is stale is a stale close (out of primary CLV; CLV = 0 exactly in the stale-included
   sensitivity). A non-stale same-`quote_id` close has CLV = 0 exactly in the primary set.
3. **Date limit (section 7).** `et_date` 2027-03-15 is a counted day (inclusive), as for the 60th qualifying day.
4. **Bootstrap mechanics (section 6).** Each test (H1, H2, H4, H5) starts a fresh `default_rng(20261118)` and draws
   `integers(0, D, size=(B, D))` (replicate-major; identical to `choice(D, size=(B, D), replace=True)`), D = counted days.
   A replicate with no valid unit (or no x-variance for a slope) has θ* = 0. If the observed P0 CLV mean is undefined
   (no valid CLV unit), H1 and H3 are reported as not testable with p = 1.
5. **Gap bins (section 6).** ≤0; (0, 5); [5, 10); [10, 20); ≥20, on the game `gap` (points).
6. **Operational audit (section 2B).** Structured records = `published_picks` rows with `basis = 'live'` and `best_picks`
   rows, each joined to a `deliveries` row with `status = 'sent'`, matched by sport and `game_id`. Buckets per P0 game:
   delivered and equal (market, side, line); delivered and different; delivered (a `best_picks` link or a non-live
   `published_picks` row) but pick not structurally recorded; no structured delivery record. The last bucket does not
   distinguish "not delivered" from "delivered without a structured record", because `deliveries` has no game column and
   section 2B forbids inferring from message text. Side equivalence: a = home = over, b = away = under (section 3).
