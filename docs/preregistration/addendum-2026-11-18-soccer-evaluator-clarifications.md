# Addendum 2026-11-18 (DRAFT): soccer Asian handicap evaluator clarifications

Applies to `soccer-asian-handicap-research-2026-11-18.md` (sha256 a3e1fc3b60ffb9316432213fc66567bcc8b269548576ce299e88e06ec031b2b4).
Written during the section 10 read-only review, before any counted outcome, quote price or close was read (synthetic
fixtures only). Each item resolves an ambiguity; no hypothesis, metric, sample, seed or date changes.

1. **Stop count versus primary set (sections 5, 7).** The 800 stop counts decided paired matches with w > 0 of every line
   type, in (kickoff, match_id) order. Δ, its CI and the primary verdict use the half-line matches of that look set.
   Whole and quarter metrics appear only when that type has at least 100 decided paired matches in the look set.
2. **CLV set (sections 5, 6).** S1 uses the look set's half-line matches whose close pair has the same line as the t_d
   pair; voids, pushes, unknown endings and pending matches do not enter it.
3. **|line| bins (section 5).** Lines with |line| < 0.5 (0, 0.25) go in the 0.5 bin. Bins cover the matches whose metrics are
   shown: half lines always, a whole or quarter type only once it has reached 100.
4. **Flat-stake return (section 5).** It is computed on the primary look set with `return_factor`: a push returns 1 and a
   half result returns as section 3 states. It remains descriptive only.
5. **Before the look (section 7).** Only forecasts, no-quote / unpaired / paired by line type, and the number of decided
   paired matches (needed to apply the stop rule) are shown. Push, void, unknown-ending, past-7-day and close counts
   appear only in the look report.
