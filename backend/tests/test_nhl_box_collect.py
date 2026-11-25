"""NHL player-box collection under the stats API's 100-row response cap (Nov 18 2026), and partial failures made visible:
a fake API that caps every response, synthetic stores, no network."""
import json
import re
import sqlite3

import httpx
import pytest

from app import cli, config, jobs, nhl_box_collect, nhl_player_ratings as pr, training_inputs
from app.sources import nhl_injuries, nhl_pbp
from test_adaptive import TRAIN_AT, store  # noqa: F401  (fixture)
from test_live_markets import add_upcoming, quote, sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)

CAP = 100


def games_db(n, first=2026020344):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE games(game_id,season,home_team_id,away_team_id,status,season_type);"
                       "CREATE TABLE teams(team_id,name);"
                       "INSERT INTO teams VALUES ('nhl:6','Boston Bruins'),('nhl:10','Toronto Maple Leafs');")
    conn.executemany("INSERT INTO games VALUES (?,2027,'nhl:6','nhl:10','final','regular')",
                     [(f"nhl:{first + i}",) for i in range(n)])
    return conn


def rows_for(group, ids, skaters=18):
    out = []
    for gid in ids:
        for side, base in (("H", 0), ("R", 100)):
            if group == "skater":
                out += [{"gameId": gid, "homeRoad": side, "playerId": base + i, "skaterFullName": f"Player {base + i}",
                         "timeOnIcePerGame": 1200., "plusMinus": 1} for i in range(skaters)]
            else:
                out.append({"gameId": gid, "homeRoad": side, "playerId": base + 50, "goalieFullName": f"Goalie {base}",
                            "timeOnIce": 3600.})
    return out


@pytest.fixture
def api(monkeypatch):
    """The stats API as observed on 2026-11-25: at most `cap` rows per response, whatever `limit` says."""
    state = {"cap": CAP, "absent": set(), "status": {}, "requests": [], "skaters": 18, "raise": set()}

    def serve(request):
        group = "goalie" if "/goalie/" in request.url.path else "skater"
        ids = [int(x) for x in re.findall(r"\d{10}", request.url.params["cayenneExp"])]
        state["requests"].append((group, ids))
        if any(i in state["raise"] for i in ids):
            raise httpx.ConnectError("down", request=request)
        status = next((state["status"][i] for i in ids if i in state["status"]), 200)
        rows = rows_for(group, [i for i in ids if i not in state["absent"]], state["skaters"])
        return httpx.Response(status, json={"data": rows[:state["cap"]], "total": len(rows)}, request=request)

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(serve)))
    monkeypatch.setattr(nhl_box_collect.time, "sleep", lambda s: None)
    return state


def test_a_backlog_larger_than_one_response_is_collected_completely(tmp_path, api):
    """Live failure 2026-11-18..25: 42 games are 1,512 skater rows; one request returned 100 and every run failed."""
    out = nhl_box_collect.collect(games_db(42), 2027, 2027, directory=tmp_path / "boxes")
    assert (out["stored"], out["failed"], out["missing"], out["errors"]) == (42, 0, 0, []), out
    assert all(len(ids) <= nhl_box_collect.BATCH for _, ids in api["requests"])
    boxes = pr.Inputs(tmp_path).boxes                                   # the member's own loader verifies every raw file
    assert len(boxes) == 42
    assert all(sorted(len(p) for p in b["teams"].values()) == [19, 19] for b in boxes.values())


def test_a_second_run_asks_for_nothing_and_changes_no_file(tmp_path, api):
    conn = games_db(5)
    nhl_box_collect.collect(conn, 2027, 2027, directory=tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.name != "last-bulk-run.json"}
    api["requests"].clear()
    out = nhl_box_collect.collect(conn, 2027, 2027, directory=tmp_path)
    assert (out["stored"], out["cached"], out["failed"], api["requests"]) == (0, 5, 0, [])
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.name != "last-bulk-run.json"}


def test_a_truncated_pair_is_asked_again_one_game_at_a_time(tmp_path, api):
    api["cap"] = 60                                                     # the cap drops below two games, not below one
    out = nhl_box_collect.collect(games_db(3), 2027, 2027, directory=tmp_path / "boxes")
    assert (out["stored"], out["failed"]) == (3, 0), out
    assert len(pr.Inputs(tmp_path).boxes) == 3


def test_a_truncated_single_game_is_never_saved(tmp_path, api):
    api["cap"] = 20
    out = nhl_box_collect.collect(games_db(2), 2027, 2027, directory=tmp_path)
    assert (out["stored"], out["failed"], out["missing"]) == (0, 2, 2), out
    assert {e["game_id"] for e in out["errors"]} == {"nhl:2026020344", "nhl:2026020345"}
    assert "truncated" in out["errors"][0]["error"]
    assert not list(tmp_path.glob("[0-9]*.json"))


def test_one_game_the_source_does_not_have_yet_never_stops_the_others(tmp_path, api):
    api["absent"] = {2026020345}
    out = nhl_box_collect.collect(games_db(6), 2027, 2027, directory=tmp_path)
    assert (out["stored"], out["failed"], out["missing"]) == (5, 1, 1)
    assert [e["game_id"] for e in out["errors"]] == ["nhl:2026020345"]


def test_a_network_error_on_one_batch_never_stops_the_others(tmp_path, api):
    api["raise"] = {2026020344}
    out = nhl_box_collect.collect(games_db(6), 2027, 2027, directory=tmp_path)
    assert (out["stored"], out["failed"], out["missing"]) == (4, 2, 2), out


@pytest.mark.parametrize("code", [403, 429])
def test_a_refusal_stops_asking_and_reports_every_game_left(tmp_path, api, code):
    api["status"] = {2026020346: code}
    out = nhl_box_collect.collect(games_db(8), 2027, 2027, directory=tmp_path)
    assert (out["stored"], out["missing"], out["not_requested"]) == (2, 6, 4), out
    assert sum(g == "skater" for g, _ in api["requests"]) == 2          # nothing is asked after the refusal


def test_the_same_game_list_and_file_format_as_the_members_collector(tmp_path, api):
    """Where one old request still fits, both collectors write the same game file (times aside)."""
    conn = games_db(2)
    old, new = tmp_path / "old", tmp_path / "new"
    pr.collect_bulk_boxes(conn, 2027, 2027, directory=old, log=lambda m: None, delay=0)
    nhl_box_collect.collect(conn, 2027, 2027, directory=new)
    assert sorted(p.name for p in old.iterdir()) == sorted(p.name for p in new.iterdir())
    for p in old.glob("[0-9]*.json"):
        a, b = json.loads(p.read_text()), json.loads((new / p.name).read_text())
        assert a.pop("retrieved_at") and b.pop("retrieved_at")
        assert list(a) == list(b) and a == b


def test_an_interrupted_write_leaves_no_file_that_counts_as_present(tmp_path, api, monkeypatch):
    def dies(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(nhl_box_collect.os, "link", dies)
    out = nhl_box_collect.collect(games_db(2), 2027, 2027, directory=tmp_path)
    assert out["stored"] == 0 and out["missing"] == 2
    assert [p.name for p in tmp_path.iterdir()] == ["last-bulk-run.json"]


# --- visibility -------------------------------------------------------------------------------------------------------
@pytest.fixture
def ready(store, monkeypatch):
    monkeypatch.setenv("NHL_PLAYER_BOX_REFRESH", "1")
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(training_inputs, "collect_recent_shots", lambda conn, now=None: {"status": "skipped"})
    monkeypatch.setattr(training_inputs, "check", lambda conn, now=None: {"ready": True, "blocking": {}, "report": {}})
    return store


def boxes(monkeypatch, **kw):
    out = {"stored": 0, "cached": 0, "failed": 0, "missing": 0, "not_requested": 0, "errors": [], **kw}
    monkeypatch.setattr(nhl_box_collect, "collect", lambda conn, a, b, **k: out)


@pytest.mark.parametrize("result, step", [
    ({"failed": 2, "missing": 2, "errors": [{"game_id": "x", "error": "y"}]}, "failed"),
    ({"stored": 3, "failed": 1, "missing": 1, "errors": [{"game_id": "x", "error": "y"}]}, "partial"),
    ({"missing": 1}, "failed")])
def test_a_failed_required_step_exits_3_and_names_itself(ready, monkeypatch, capsys, result, step):
    boxes(monkeypatch, **result)
    assert cli.main(["--sport", "nhl", "training-inputs"]) == cli.PARTIAL == 3
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "failed" and out["failed_steps"] == ["nhl_player_boxes"]
    assert out["steps"]["nhl_player_boxes"]["status"] == step
    from app import training_monitor
    assert "nhl_player_boxes" in training_monitor.status(ready)["inputs"]["training_inputs"]["last_failure"]["reason"]


def test_a_complete_collection_exits_0(ready, monkeypatch, capsys):
    boxes(monkeypatch, stored=3)
    assert cli.main(["--sport", "nhl", "training-inputs"]) == 0
    assert json.loads(capsys.readouterr().out)["failed_steps"] == []


def test_shots_that_failed_beside_shots_that_arrived_are_a_partial_failure(ready, monkeypatch, capsys):
    boxes(monkeypatch)
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "success", "imported": 3, "errors": {"nhl:1": "x"}})
    assert cli.main(["--sport", "nhl", "training-inputs"]) == 3
    assert json.loads(capsys.readouterr().out)["failed_steps"] == ["nhl_shots"]


def test_not_ready_still_exits_1(ready, monkeypatch, capsys):
    boxes(monkeypatch, failed=1, missing=1)
    monkeypatch.setattr(training_inputs, "check", lambda conn, now=None: {"ready": False, "blocking": {"x": 1}, "report": {}})
    assert cli.main(["--sport", "nhl", "training-inputs"]) == 1


def _daily(monkeypatch, inputs, **steps):
    from app import training_monitor
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: steps.get("pbp", {"status": "skipped", "errors": {}}))
    monkeypatch.setattr(training_monitor, "status", lambda conn, now=None: {"inputs": inputs})


OK = {"stale": False, "last_failure": None}


@pytest.mark.parametrize("inputs, pbp, expect", [
    ({"refresh": OK, "odds": OK, "training_inputs": OK}, None, []),
    ({"training_inputs": {"stale": False, "last_failure": {"at": "t", "reason": "collection step(s) failed: nhl_player_boxes"}}},
     None, ["training_inputs"]),
    ({"training_inputs": {"stale": True, "last_failure": None, "last_success": "2026-11-18T20:30:02Z"}}, None, ["training_inputs"]),
    ({"refresh": {"stale": True, "last_failure": None}}, None, ["refresh"]),
    ({"odds": {"stale": True, "last_failure": None}}, None, []),                       # no game near: odds may rest
    ({}, {"status": "success", "imported": 2, "errors": {"nhl:1": "x"}}, ["nhl_pbp"])])
def test_daily_names_what_needs_attention_and_never_exits_0_with_it(store, monkeypatch, capsys, inputs, pbp, expect):
    _daily(monkeypatch, inputs, **({"pbp": pbp} if pbp else {}))
    code = cli.main(["--sport", "nhl", "daily"])
    out = json.loads(capsys.readouterr().out)
    assert [a["input"] for a in out["attention"]] == expect
    assert out["outcome"] == ("attention" if expect else "success")
    assert code == (3 if expect else 0)


# --- the scheduled commands (the templates' own shell text, run with a stub interpreter) ------------------------------
def _run_template(tmp_path, name, codes):
    """Runs the template's `/bin/sh -c` text with every Python replaced by a stub that logs its arguments and exits as told."""
    import plistlib
    import subprocess
    plist = plistlib.loads((config.REPO_ROOT / "scripts" / "launchd" / f"com.nba-predictor.{name}.plist").read_bytes())
    args = plist["ProgramArguments"]
    text = args[args.index("-c") + 1]
    log, stub = tmp_path / "calls", tmp_path / "python"
    cases = "".join(f'  "{k}") exit {v};;\n' for k, v in codes.items())
    stub.write_text(f'#!/bin/sh\nshift 2\necho "$*" >> {log}\ncase "$*" in\n{cases}esac\nexit 0\n')
    stub.chmod(0o755)
    text = re.sub(r"/Users/andry/[\w./-]*/python", str(stub), text)
    text = re.sub(r"cd /Users/andry/[\w./-]+", f"cd {tmp_path}", text)
    assert "/Users/andry" not in text
    code = subprocess.run(["/bin/sh", "-c", text], timeout=30).returncode
    return code, log.read_text().splitlines()


TRAIN = ["--sport {s} training-inputs", "--sport {s} adaptive-train", "--sport {s} adaptive-train --cadence daily"]
ALL = [c.format(s=s) for s in ("nhl", "nba") for c in TRAIN]


@pytest.mark.parametrize("codes, expect, calls", [
    ({}, 0, ALL),
    ({"--sport nhl training-inputs": 3}, 3, ALL),                                      # trained, and not a plain success
    ({"--sport nba training-inputs": 3}, 3, ALL),
    ({"--sport nhl training-inputs": 1}, 1, [c for c in ALL if c not in ALL[1:3]]),    # not ready: no training for that sport
    ({"--sport nhl training-inputs": 3, "--sport nba adaptive-train": 1}, 1, ALL),     # a failed training is the louder one
    ({"--sport nhl training-inputs": 139}, 1, [c for c in ALL if c not in ALL[1:3]])])
def test_the_trainer_trains_on_partial_inputs_and_reports_them(tmp_path, codes, expect, calls):
    assert _run_template(tmp_path, "adaptive-train", codes) == (expect, calls)


@pytest.mark.parametrize("codes, expect", [({}, 0), ({"odds": 1}, 1), ({"daily": 1}, 1), ({"daily": 3}, 3), ({"odds": 1, "daily": 3}, 1)])
def test_a_failed_odds_step_is_not_hidden_by_a_good_daily_step(tmp_path, codes, expect):
    code, calls = _run_template(tmp_path, "nhl-daily", {f"--sport nhl {k}": v for k, v in codes.items()})
    assert (code, calls) == (expect, ["--sport nhl odds", "--sport nhl daily"])
