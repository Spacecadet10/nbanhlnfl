# Shortlist of exact rules for prospective tracking — 2026-10-11

**What this is:** a choice of exploratory candidates for future tracking under exact-rules-v1. It is **not** a fresh holdout
result and it proves nothing about any rule. Every number below comes from the frozen research exports already in this
folder (`nba-winner-pool.md`, `nhl-reg3-pool.md`, `nhl-winner-pool.md`; sources `oof-nba-{screen,assess}.json.gz`
70699e2fa2f175be / fdf536466fb40ca4 and `oof-nhl-{screen,assess}.json.gz` bd4824b99f882a83 / 69b587f71047f577) and was
re-read with the existing code (`combination_thresholds.evaluate_rule`, `_qualifying`) on a scratch copy of the dev data.
No rule was searched or tuned against the later period here; the later-period overlap between the listed rules is the only
new computation and it is descriptive. Nothing was trained, registered, migrated, released or restarted.

Reading the columns: "later" = the frozen check period (NBA 2024-10-22–2026-06-14, 2643 games; NHL 2024-10-04–2026-06-15,
2792 games), which **includes the spent reserved-test seasons** and is exploratory. "Same games" = the earlier-chosen best
single model's own pick on exactly the rule's games. "Matched count" = that model's same number of most confident later
picks, possibly different games. Win rate = wins ÷ (wins + losses), Wilson 95% interval. No odds exist: nothing here is profit.

## Where the evidence does and does not warrant a candidate

| Sport / market | Source | Verdict | Reason |
|---|---|---|---|
| NBA winner | pool | 3 candidates | 18 of 20 "reliable" rules beat the matched-count comparator later, but by 1–2 pts inside ±4.5-pt intervals; same-games records are identical. |
| NHL reg3 (regulation 3-way) | pool | 2 candidates, weak | 5–6 of 20 rules beat the comparator, 3 distinct pick sets; each edge is one game. |
| NHL winner | pool | none warranted | 0 of 40 rules beat the comparator; same-games records identical. One evidence-neutral option listed only because NHL is the sport that is live now. |
| NBA spread | pool | none | 1–2 of 40; every frozen rule ran 55–60 % later against a 58–62 % comparator (negative). |
| NBA winner, NHL winner | adaptive | not registrable | exact-rules-v1 refuses `…@adaptive` members (the tracking inputs do not record them). |
| NBA total, NHL total / puckline | — | no export | line-market model lines were not saved; nothing to choose from. |

Members that the registry would refuse on this machine even though they appear in the exports: `ngboost:nba_base`
(the `research` optional group is not installed in this checkout's venv; it **is** installed in the release venv), any goalie
candidate, any `@adaptive` member.

## NBA winner (pool) — three candidates

Best single chosen on the earlier period: `elastic_net:nba_base`. All three rules were validated by `exact_rules.validate`
(cohort `core`) and found in the explorer's frozen "reliable" list (`subsets.json`, sha256 ce50b1034e5f5082…).

| # | Rule (member ≥ threshold) | Later record (coverage) | Win rate [95 %] | Same games, best single | Matched count, best single | By season (2024-25 / 2025-26) | Explorer rank |
|---|---|---|---|---|---|---|---|
| N1 | `logistic` ≥80 % & `efficiency_margin` ≥75 % & `gbt_v1` ≥80 % | 243 picks (9.2 %) 209–34–0P | 86.0 % [81.1, 89.8] | 209–34 (identical, same side 243/243) | 204–39, 84.0 % [78.8, 88.0] | 96–15 / 113–19 | reliable #2 |
| N2 | `gbt_v1` ≥80 % & `mlp:nba_base` ≥80 % & `elastic_net:nba_base` ≥80 % | 251 picks (9.5 %) 215–36–0P | 85.7 % [80.8, 89.5] | 215–36 (identical) | 211–40, 84.1 % [79.0, 88.1] | 98–14 / 117–22 | reliable #3, improvement #1 |
| N3 | `logistic` ≥80 % & `efficiency_margin` ≥75 % & `mlp:nba_base` ≥80 % | 254 picks (9.6 %) 219–35–0P | 86.2 % [81.4, 89.9] | 219–35 (identical) | 214–40, 84.3 % [79.3, 88.2] | 103–16 / 116–19 | reliable #10 |

Earlier-period records (the search period, not evidence): N1 163–23 (87.6 %), N2 114–15 (88.4 %), N3 166–25 (86.9 %).

**Overlap (later period, game IDs; no conflicting sides anywhere):** N1∩N2 220 shared of 243/251; N1∩N3 231 of 243/254;
N2∩N3 220 of 251/254. The three rules are largely one bet on the same ~230 high-confidence games; tracking all three is close
to tracking one rule three times. For reference, the core-only pair `elo` ≥75 % & `efficiency_margin` ≥75 % contains every N1
and N3 pick (324 picks, 275–49, 84.9 %) and does **not** beat its matched comparator (276–48), which is the clearest sign that
the edge, if any, sits in the 80 % thresholds, not in agreement.

**Why these merit prospective testing (not a claim they are best):** they are the only market where most frozen rules ran above
the matched-count comparator later, the effect is consistent across both later seasons, and the rules pick ~9–10 % of games
(roughly 110–130 picks per NBA season), so a forward record accumulates at a usable pace. The ~2-pt edge over the comparator
is five games in 250 and sits well inside the interval; the same-games comparison shows the best single model picks the same
side on every one of these games, so what is being tested is the game *selection*, not a different pick.

**Support and deployment:**
- N1 uses core cohort members only: no candidate forecast, no optional dependency, no season-start fit. It is the only NBA
  candidate that can record from the first NBA cycle of the season without any candidate artifact.
- N2 and N3 need `mlp:nba_base` (N2 also `elastic_net:nba_base`) as live-recorded candidate forecasts. No NBA candidate forecast
  exists yet in either store (`candidate_forecasts` for NBA is empty) and the dev copy holds no fitted sklearn NBA candidate
  artifact (only `keras_mlp` / `torch_mlp` folders), so these fits must be produced by the live cycle at the season start
  (`candidates/live.ensure_fit`, season-start fit from earlier seasons). Until that happens every N2/N3 observation would be
  `incomplete`, visibly. No dependency group is required (`candidate_requirements` → none).
- The NBA regular season starts 2026-10-20; nothing can be observed before then.

## NHL reg3 (pool) — two weak candidates

Best single chosen on the earlier period: `multinomial_l2:nhl_base`, which is also a member of every candidate. Grid for this
market: 33.3 %, 40, 45, 50, 55, 60, 65 %. Both rules validated (cohort `core`) and found in the frozen "reliable" list
(`subsets.json` sha256 0dab69122429af8b…).

| # | Rule | Later record (coverage) | Win rate [95 %] | Same games, best single | Matched count, best single | By season | Explorer rank |
|---|---|---|---|---|---|---|---|
| H1 | `goals_glm` ≥60 % & `multinomial_l2:nhl_base` ≥65 % | 48 picks (1.7 %) 35–13–0P | 72.9 % [59.0, 83.4] | 35–13 (identical) | 34–14, 70.8 % [56.8, 81.8] | 26–11 / 9–2 | reliable #5 (same pick set as #2, #15) |
| H2 | `goals_glm` ≥55 % & `multinomial_l2:nhl_base` ≥65 % & `poisson_draw:nhl_base` ≥55 % | 68 picks (2.4 %) 48–20–0P | 70.6 % [58.9, 80.1] | 48–20 (identical) | 47–21, 69.1 % [57.4, 78.8] | 32–15 / 16–5 | reliable #20, improvement #15 |

Earlier-period records: H1 121–53 (69.5 %), H2 207–103 (66.8 %).

**Overlap:** H1 ⊂ H2 (all 48 H1 picks are H2 picks; H2 adds 20). The pair `multinomial_l2:nhl_base` ≥65 % &
`poisson_draw:nhl_base` ≥55 % (70 picks, 48–22) contains H2 and is *identical* to its matched comparator, so `goals_glm`'s only
contribution is dropping 2 (H2) or 22 (H1) games. Each candidate's edge over the comparator is exactly one game.

**Why they are listed at all:** reg3 is the one NHL market where some frozen rules ran above the comparator later, the NHL
season is live now (candidate forecasts for `multinomial_l2:nhl_base` and `poisson_draw:nhl_base` are already recorded in
the live store every cycle), and the rules are cheap to carry. **Why the evidence is weak:** ~1–2 % coverage means roughly
20–30 picks per NHL season, so even a full season will not separate these from the single model; the interval widths are
±12 pts. If only one is tracked, H2 accumulates faster; H1 is the stricter version of the same bet.

**Support and deployment:** both members are live-recorded in the NHL live store already (`candidate_forecasts` holds
`multinomial_l2:nhl_base` and `poisson_draw:nhl_base` for reg3 since 2026-10-02); `goals_glm` is a core cohort member. No
optional dependency group. Observation can start at the first two-hour cycle after registration and release.

## NHL winner (pool) — not warranted; one evidence-neutral option

0 of 40 frozen rules beat the matched comparator later and the best single (`hockey_logistic_xg`) picks the same side on
every rule's games. If an NHL winner rule is wanted anyway because NHL is the only live sport this month, the least-bad option
is `hockey_logistic` ≥75 % & `extra_trees:nhl_base` ≥70 % & `multinomial_l2:nhl_base` ≥75 % (reliable #1): later 44 picks
(1.6 %) 38–6, 86.4 % [73.3, 93.6]; same games 38–6; matched count 38–6 — identical in all three columns, 28–5 / 10–1 by
season. It validates (cohort `core`), its members are live-recorded now, and it would add exactly nothing beyond what the
tracking-v2 `single` group for `hockey_logistic_xg` at 75 % already records. Listed so the decision is explicit, not recommended.

## Decisions needed

1. **NBA winner:** track N1 only, or N1 plus one of N2/N3? (They overlap ~90 %; N1 alone has no candidate-fit dependency.)
2. **NHL reg3:** track H2, H1, both, or none, accepting that a season of data will stay inconclusive?
3. **NHL winner:** none (evidence) or the neutral option (operational reasons only)?
4. **Evidence file:** register with `--evidence <subsets.json>` (provenance from the frozen list, file sha recorded) or
   `--note` only. The frozen-list files live in `~/nba-predictor-dev-data/reports/combination-thresholds-*/subsets.json`
   (read them in place or copy them; that folder is not to be modified).
5. **Timing:** NHL rules can start now; NBA rules record nothing before 2026-10-20. Register NBA rules before the season or wait?

## Proposed activation plan (one coordinated release; not executed)

Order matters: the registry file `backend/app/models/exact_rules.json` is a tracked file, the release guard refuses an unclean
release checkout and a schema mismatch, and the live stores are at schema 26 while this code ships 0027 and 0028.

1. **Register in this checkout** (dev venv, writes only the registry file):
   `cd backend && .venv/bin/python -m app.cli --sport nba exact-rules-register --market winner --rule logistic@0.8,efficiency_margin@0.75,gbt_v1@0.8 --evidence <nba-winner-pool subsets.json> --dry-run`, then without `--dry-run`; repeat for each chosen rule
   (`--sport nhl --market reg3 --rule goals_glm@0.55,multinomial_l2:nhl_base@0.65,poisson_draw:nhl_base@0.55 …`).
   `exact-rules-list` to confirm. `effective_from` is the registration time; only games starting after it are observed.
2. **Commit** the pending work on `model-audit` (including the registry) after resolving the recorded ownership notes;
   `backend/.venv/bin/python scripts/check_project.py --ready` (known: 8 `test_deep_study.py` failures in the main venv, torch missing).
3. **Move the release between cycles** (no game within 2 h): `git -C ~/nba-predictor-live merge --ff-only <commit>`;
   `uv pip sync` only if `requirements-release.txt` changed; `npm ci && npm run build` in the release frontend.
4. **Back up, then migrate both live stores from the release checkout** (`migrate`, each sport, `NBA_RELEASE_GUARD=1`), taking
   them from 26 to 28. Until this step the tracking report shows `schema_required` and the cycle skips exact rules.
5. **Restart API and web** (`launchctl kickstart -k …api / …web`), then `release-check` for nhl and nba.
6. **Verify the first live NHL cycle:** the two-hour job detail carries `out["exact"]` with the rule ids; Performance →
   research → "Registered exact rules · Prospective only" shows the rules with eligible counts and reasons; game cards show
   exact picks or `below_threshold` / `disagree`. NBA rules show "no observations" until 2026-10-20.
7. **Roll back if needed:** `git -C ~/nba-predictor-live checkout -B release f7ee505`, rebuild, restart. Rows written under
   0028 stay in the store; the release at f7ee505 would refuse a schema-28 store, so the rollback also needs a restore of the
   pre-migration backup, which is why step 4 is the real commitment point.

Known limitations carried forward (unchanged): member availability is checked on the registering machine, not the release
environment (irrelevant for the listed rules: no optional group); `exact-rules-register --evidence <missing file>` ends in a
traceback; `--dry-run` creates the gitignored lock file; the game card's top-12 cut can hide an exact pick; the
excluded-games period label spans both periods.
