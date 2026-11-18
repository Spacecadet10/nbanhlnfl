# Addendum 2026-11-18 (DRAFT): NFL line evaluator clarifications

Applies to `nfl-line-eval-2026-11-18.md` (sha256 5ea6fef126ca9d4b8555f689d255a590f08261aac758a0e11101294d419e6e6c).
Written during the section 9 read-only evaluator review, before any counted outcome, close or result was read (synthetic
fixtures only). Each item resolves an ambiguity; no hypothesis, metric, n, alpha, seed or deadline changes.

1. **Cluster (section 5).** The store has no week column. NFL week = the Eastern-time (America/New_York) Tuesday-anchored
   week of the kickoff (Tuesday through Monday), keyed with `games.season`.
2. **The look (section 6).** The look sample of a market is the first 600 decided bookmaker-line games in (kickoff,
   game_id) order. The look is taken only when no game at or before the 600th is pending (neither final nor past 7 days);
   until then the report says "waiting". Games decided later never enter it (no extension). The same rule applies to each
   challenger's 600 paired games.
3. **Deadline (section 6).** Only games with kickoff on or before 2029-02-28 (Eastern date) count toward 600. A market or
   challenger hypothesis below 600 such games is reported once as "insufficient evidence" and closed.
4. **Team totals (sections 4.3, 6).** They have no bookmaker line, so the section 6 gate is applied to 600 decided
   forecasts of each team-total market (home and away separately). No hypothesis or test exists for them (section 5:
   everything else descriptive).
5. **Challenger counting start (section 3).** Literal: start = max(T0, first `generated_at` of any row under the policy
   sha); a game counts only if its cutoff ≥ start. Games of the first recording pass whose cutoff precedes their own row's
   `generated_at` are not counted.
6. **Challenger pairing (sections 3, 4.4).** Δ_c pairs every decided incumbent game with a challenger row at the same line,
   bookmaker or model line; the vs-book figure uses the bookmaker-line subset. For the two points challengers the
   team-total Δ_c (both team-total markets) is computed descriptively at 600 paired decided games, with no test.
