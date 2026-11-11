"""Live international forecasts (app/intl_live.py) on real data only: the real CC0 excerpt
(tests/fixtures/international_real) and the real sealed international candidate."""
from dataclasses import replace
from pathlib import Path

from app import intl_eval as ie, intl_live

REAL = Path(__file__).parent / "fixtures" / "international_real"


def real_matches():
    return ie.parse({n: (REAL / n).read_bytes() for n in ie.FILES})


def test_the_live_core_is_the_sealed_evaluation_walk_on_real_matches():
    ms, sealed = real_matches(), intl_live.sealed_candidate()
    walked = {p["mid"]: p["p"] for p in ie.walk(ie.model_of(sealed), ms, range(2024, 2026))}
    days = sorted({m.day for m in ms if m.mid in walked})[-12:]          # the last real match days of 2025
    checked = 0
    for day in days:
        targets = [replace(m, label=None, exclusion="target") for m in ms if m.day == day and m.mid in walked]
        live = intl_live.forecast_core(sealed, ms, targets, day)
        for mid, p in live.items():
            assert max(abs(a - b) for a, b in zip(p, walked[mid])) < 1e-12
            checked += 1
    assert checked >= 12


def test_the_sealed_candidate_is_the_tested_one():
    s = intl_live.sealed_candidate()
    assert s["sha256"] == intl_live.CANDIDATE_SHA256 and s["model"] == "elo" and ie.valid_seal(s)
    assert set(intl_live.SCOPE) == {"continental_finals", "continental_qualification", "friendly", "nations_league",
                                    "wc_qualification"}                     # world_cup failed R3 (n < 300)
    assert "fifa.world" not in intl_live.LAUNCH and "uefa.nations" in intl_live.LAUNCH


def test_kickoff_day_keeps_americas_evenings_on_their_local_date():
    assert intl_live.match_day("2026-11-13T02:00:00Z") == "2026-11-12"     # 20:00 CST on Nov 12
    assert intl_live.match_day("2026-11-12T19:45:00Z") == "2026-11-12"
    assert ie.cutoff_for(intl_live.match_day("2026-11-13T02:00:00Z")) <= "2026-11-13T02:00:00Z"


def test_waiting_only_for_affected_teams_gives_exactly_the_settled_forecast():
    """Real 2025 matches: the first real (pending day, target day) pair, in date order, where holding back the pending
    day's results leaves some targets ready and blocks others. Every ready target must get exactly the forecast it gets
    with all results in; every blocked one involves an affected team."""
    ms, sealed = real_matches(), intl_live.sealed_candidate()
    usable = [m for m in ms if m.label is not None]
    days = sorted({m.day for m in usable if m.day.startswith("2025")})
    for a, first in enumerate(days):
        for day in days[a + 1:a + 6]:
            pending = [replace(m, label=None, exclusion="pending") for m in usable if m.day == first]
            held_back = {m.mid for m in pending}
            known = [m for m in ms if m.mid not in held_back]
            targets = [replace(m, label=None, exclusion="target") for m in usable if m.day == day]
            waiting = intl_live.affected_teams(known, pending, day)
            ready = [m for m in targets if m.home not in waiting and m.away not in waiting]
            if ready and len(ready) < len(targets):
                assert intl_live.forecast_core(sealed, known, ready, day) == intl_live.forecast_core(sealed, ms, ready, day)
                assert all(m.home in waiting or m.away in waiting for m in targets if m not in ready)
                return
    raise AssertionError("no real pair with both ready and blocked targets found")


def test_the_team_map_covers_the_ofc_friendly_teams_listed_on_2026_11_11():
    # ESPN listed Tahiti v Cook Islands friendlies (Nov 14, Nov 17); an unmapped team refuses every international run.
    m = intl_live.teams()
    for team, name in (("soccer:4863", "Tahiti"), ("soccer:4884", "Cook Islands")):
        assert m["espn"][team] == name and m["evidence"][team] == {"team": name, "basis": "exact name"}
    assert set(m["espn"]) == set(m["evidence"]) and all(m["evidence"][k]["team"] == v for k, v in m["espn"].items())
