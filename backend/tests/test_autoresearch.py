"""Boundaries of the sports autoresearch evaluator (app/autoresearch.py) on a synthetic NBA fixture.

Real fits run in the sandboxed candidate process through the existing engine; only the frozen dataset is synthetic
(build_dataset is patched), so no store is read. Each test has its own read-only snapshot dir, experiment root and
workspace git repository.
"""
import hashlib
import json
import math
import os
import random
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import autoresearch as ar
from app import combination_research as cr
from app.candidates import engine, features
from app.candidates.registry import Spec
from app.models.score_candidates import NBA_FEATURES

pytestmark = pytest.mark.skipif(not Path(ar.SANDBOX).is_file(), reason="trials need macOS sandbox-exec")

SEASONS = (2019, 2020, 2021, 2022, 2023, 2024)
GAMES = 250
LINES = {"spread": 0.5, "total": 220.5, "team_total_home": 110.5, "team_total_away": 110.5}

BASELINE = '''
from app.candidates import engine
from app.candidates.registry import Spec


def candidates(sport):
    return [Spec("t:logit", "nba", "logit_l2", "binary", "nba_base", "candidate", "t"),
            Spec("t:ridge", "nba", "ridge", "normal", "nba_base", "candidate", "t")]


MARKETS = {}


def fit(spec, rows):
    return engine.fit(spec, rows)


def outputs(spec, model, rows, sd):
    return engine.outputs(spec, model, rows, sd)
'''


def edit(old, new, src=BASELINE):
    assert old in src
    return src.replace(old, new)


OUT = "return engine.outputs(spec, model, rows, sd)"


def _ts(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def synthetic(period="validation"):
    """Home win probability = sigmoid(elo_diff). Winner members: a weak noisy primary `elo`, a partial `logistic`, and a
    `strong` member that knows the truth (the best existing member, which a fitted logistic cannot beat). Line members are
    near coin flips, so a fitted margin model beats the best existing line member."""
    rng = random.Random(7)
    seasons = SEASONS + ((2025, 2026) if period == "test" else ())
    rows, tables = [], {m: [] for m in ("winner", *LINES)}
    for s in seasons:
        start = datetime(s - 1, 10, 20, 23, 0, tzinfo=timezone.utc)
        for i in range(GAMES):
            tip = start + timedelta(hours=16 * i)
            z = rng.gauss(0, 1.2)
            x = {k: rng.gauss(0, 1) for k in NBA_FEATURES}
            x["elo_diff"], x["neutral"] = z, 0.0
            p = 1 / (1 + math.exp(-z))
            won = rng.random() < p
            margin = (abs(round(rng.gauss(6, 4))) + 1) * (1 if won else -1)
            total = round(rng.gauss(220, 15))
            home, away = (total + margin) // 2, (total - margin) // 2
            margin, total = home - away, home + away
            gid = f"g{s}-{i}"
            cut, done = _ts(tip - timedelta(hours=12)), _ts(tip + timedelta(hours=3))
            rows.append(features.Row(gid, s, _ts(tip), cut, x, done, {"home_won": int(won), "margin": margin, "total": total}))
            base = {"game_id": gid, "season": s, "cutoff_at": cut, "tipoff_at": _ts(tip), "result_available_at": done}
            q = 1 / (1 + math.exp(-0.3 * (z + rng.gauss(0, 1.5))))
            vec = {"elo": (q, 0.0, 1 - q), "strong": (p, 0.0, 1 - p)}
            if i % 5:
                vec["logistic"] = (q, 0.0, 1 - q)
            tables["winner"].append({**base, "line": None, "realised": 0 if won else 2, "vectors": vec})
            for m, line in LINES.items():
                c = 0.5 + rng.uniform(-0.02, 0.02)
                tables[m].append({**base, "line": line, "realised": cr._realised(m, line, home, away),
                                  "vectors": {"points_normal": (c, 0.0, 1 - c), "ridge_points": (1 - c, 0.0, c)}})
    return {"sport": "nba", "period": period, "rows": rows, "tables": tables,
            "members": {"winner": ["elo", "logistic", "strong"], "lines": ["points_normal", "ridge_points"]},
            "primary": {"winner": "elo", **{m: "points_normal" for m in LINES}}, "dataset_version": f"synthetic-{period}",
            "feature_table": "synthetic"}


def git(ws, *args):
    return subprocess.run(["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def commit_train(ws, src, msg="change"):
    (ws / "train.py").write_text(src)
    git(ws, "commit", "-qam", msg)
    return git(ws, "rev-parse", "HEAD")


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "snapshot"
    data.mkdir()
    (data / "nba.sqlite").write_bytes(b"synthetic snapshot")
    os.chmod(data / "nba.sqlite", 0o444)
    os.chmod(data, 0o555)
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    monkeypatch.setattr(ar, "build_dataset", lambda sport, period: synthetic(period))
    monkeypatch.setitem(ar.BUDGETS, "trial_timeout_s", 300)
    ws = tmp_path / "workspace"
    ws.mkdir()
    git(ws, "init", "-q")
    (ws / "program.md").write_text("instructions\n")
    (ws / "prepare.py").write_text("# fixed\n")
    (ws / "train.py").write_text(BASELINE)
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "base")
    exp = tmp_path / "experiments"
    meta = ar.prepare("nba", exp, ws)
    yield {"ws": ws, "exp": exp, "meta": meta, "root": exp / meta["protocol_id"] / "nba", "data": data}
    os.chmod(data, 0o755)


def records(root):
    return [json.loads(x) for x in (root / "trials.jsonl").read_text().splitlines()]


def finished(root):
    return [r for r in records(root) if r["event"] == "finished"]


# --- Static and path guards -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("src", ["from app.candidates import deep\nx = deep.Path", "from app.candidates import engine as e\ne.math",
                                 "import os", "import sqlite3", "from pathlib import Path", "x = open('f')",
                                 "import subprocess", "y = eval('1')", "z = getattr(object, 'x')", "import pickle",
                                 "from app import config", "q = (1).__class__.__subclasses__()",
                                 "from app.candidates.live import os", "from app.candidates.deep import Path",
                                 "import app.candidates.engine", "from app.candidates import live",
                                 "from app.candidates.engine import _X", "import numpy as np\nnp._x = 1"])
def test_train_py_may_not_touch_files_processes_or_the_evaluator(src):
    assert ar.static_check(src)


def test_the_baseline_train_py_is_admissible():
    assert ar.static_check(BASELINE) == []


def test_refuses_unset_or_live_data_dirs_and_outputs_inside_the_repository(tmp_path, monkeypatch):
    monkeypatch.delenv("NBA_DATA_DIR", raising=False)
    with pytest.raises(ar.Refused, match="not set"):
        ar.guard_paths(tmp_path / "exp")
    for live in ar.live_roots():
        monkeypatch.setenv("NBA_DATA_DIR", str(live))
        with pytest.raises(ar.Refused, match="live store"):
            ar.guard_paths(tmp_path / "exp")
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / "snap"))
    with pytest.raises(ar.Refused, match="outside the repository"):
        ar.guard_paths(ar.REPO_ROOT / "backend" / "exp")


def test_live_jobs_data_dirs_are_known_from_launchd():
    roots = {str(p) for p in ar.live_roots()}
    assert str(ar.REPO_ROOT / "data") in roots
    assert any(r.endswith("nba-predictor-live") for r in roots)


def test_a_writable_snapshot_or_raw_cache_is_refused_and_the_raw_manifest_is_identity(tmp_path, monkeypatch):
    (tmp_path / "nba.sqlite").write_bytes(b"x")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "a.json.gz").write_bytes(b"1")
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    with pytest.raises(ar.Refused, match="writable"):
        ar.snapshot_identity("nba")
    for p in (tmp_path / "nba.sqlite", tmp_path / "raw" / "a.json.gz"):
        os.chmod(p, 0o444)
    os.chmod(tmp_path / "raw", 0o555)
    with pytest.raises(ar.Refused, match="writable"):           # the directory itself is still writable
        ar.snapshot_identity("nba")
    os.chmod(tmp_path, 0o555)
    try:
        ident = ar.snapshot_identity("nba")
        assert ident["raw_manifest"] and ar.protocol_id({"nba": ident}) != ar.protocol_id({"nba": {**ident, "raw_manifest": "y"}})
    finally:
        os.chmod(tmp_path, 0o755)
        os.chmod(tmp_path / "raw", 0o755)


def test_the_evaluator_has_no_path_to_live_forecasts_or_approval():
    src = Path(ar.__file__).read_text()
    for name in ("game_forecasts", "adaptive_forecasts", "candidate_forecasts", "insert_forecast", "fit_register",
                 "from . import coverage", "from . import adaptive", "candidates import live", "db.connect", "migrate"):
        assert name not in src


def test_protocol_identity_covers_library_versions_and_every_app_file():
    ident = ar.app_identity()
    assert {"numpy", "scikit-learn", "torch", "python"} <= set(ident["libraries"])
    assert ident["files"]


# --- Cutoff / label boundary (in-process stand-in for the child: the same messages) ------------------------------------
def test_fits_see_only_earlier_available_results_and_forecasts_are_asked_one_cutoff_at_a_time_without_labels():
    class Train:
        fit = staticmethod(engine.fit)
        outputs = staticmethod(engine.outputs)

    conn = ar._Local(Train)
    spec = Spec("t:logit", "nba", "logit_l2", "binary", "nba_base", "candidate", "t")
    outs, fits, _ = ar.walk(conn, [spec], synthetic()["rows"], last_season=2023)
    out, fits = outs[spec.name], fits[spec.name]
    assert [f["season"] for f in fits] == [2020, 2021, 2022, 2023] and all(f["fitted"] for f in fits)
    last_fit, latest_cut = None, ""
    for msg in conn.sent:
        if msg["op"] == "fit":
            last_fit = msg["rows"]
            continue
        cut = {r.cutoff_at for r in msg["rows"]}
        assert len(cut) == 1                                   # one cutoff per request: no later feature rows
        cut = cut.pop()
        assert cut >= latest_cut                                # in time order
        latest_cut = cut
        assert all(r.y is None and r.result_available_at is None for r in msg["rows"])
        season = msg["rows"][0].season
        assert all(r.season < season and r.result_available_at <= cut for r in last_fit)
    assert not any(gid.startswith("g2024") for gid in out)


def test_no_spec_ever_forecasts_a_game_whose_label_the_process_has_seen():
    """Season-major schedule: a label stashed during any spec's fit can never be one of a game later forecast."""
    seen, hits = set(), []

    class Stash:
        @staticmethod
        def fit(spec, rows):
            seen.update(r.game_id for r in rows if r.y is not None)
            return engine.fit(spec, rows)

        @staticmethod
        def outputs(spec, model, rows, sd):
            hits.extend(r.game_id for r in rows if r.game_id in seen)
            return engine.outputs(spec, model, rows, sd)

    specs = [Spec("t:a", "nba", "logit_l2", "binary", "nba_base", "candidate", "t"),
             Spec("t:b", "nba", "ridge", "normal", "nba_base", "candidate", "t")]
    outs, _, _ = ar.walk(ar._Local(Stash), specs, synthetic()["rows"], last_season=2024)
    assert outs["t:a"] and outs["t:b"] and hits == []


def test_torch_style_classes_pass_the_static_check():
    src = BASELINE + "\n\nclass Net:\n    def __init__(self):\n        super().__init__()\n        self._w = 1\n"
    assert ar.static_check(src) == []
    assert ar.static_check("class A:\n    def f(self, o):\n        return o._w")          # others' private attributes stay banned


def test_a_malformed_reply_is_invalid_not_a_crash():
    with pytest.raises(ar.TrialInvalid):
        ar._field({"other": 1}, "outputs")


def test_a_season_without_a_model_is_recorded_as_not_fitted_and_not_forecast():
    class NoFirst:
        @staticmethod
        def fit(spec, rows):
            return None if max(r.season for r in rows) < 2020 else engine.fit(spec, rows)
        outputs = staticmethod(engine.outputs)

    spec = Spec("t:logit", "nba", "logit_l2", "binary", "nba_base", "candidate", "t")
    outs, fits, _ = ar.walk(ar._Local(NoFirst), [spec], synthetic()["rows"], last_season=2022)
    out, fits = outs[spec.name], fits[spec.name]
    assert fits[0]["season"] == 2020 and fits[0]["fitted"] is False
    assert all(f["fitted"] for f in fits[1:]) and not any(g.startswith("g2020") for g in out)


# --- Trials ---------------------------------------------------------------------------------------------------------
def test_the_bar_is_the_best_existing_member_and_a_real_fit_is_kept_only_where_it_beats_it(env):
    bar = env["meta"]["start_incumbent"]
    assert bar["winner"]["member"] == "strong"                     # not the weak primary; `logistic` lacks games
    assert bar["winner"]["members"]["logistic"]["coverage"] < 1
    rec = ar.run_trial("nba", env["exp"], env["ws"], "baseline")
    w, s = rec["markets"]["winner"], rec["markets"]["spread"]
    assert w["vs_primary"]["ci95"][1] < 0 and w["decision"] == "reject"   # beats the primary, not the best member
    assert w["incumbent"] == "existing member strong"
    assert s["decision"] == "keep" and s["coverage"] == 1.0 and rec["status"] == "keep"
    assert rec["fits"]["t:logit"][0]["training_games"] == GAMES          # real season-start fits recorded
    assert {f["season"] for f in rec["fits"]["t:logit"]} == {2020, 2021, 2022, 2023, 2024}
    assert rec["resources"]["wall_s"] > 0 and rec["seed"] == ar.PROTOCOL["seed"]
    assert set(rec["agreement"]["winner"]) >= {"pair:candidate+primary", "majority:candidate+existing(4)"}
    assert rec["agreement"]["winner"]["majority:existing(3)"]["incomplete"] > 0        # missing member -> incomplete
    worse = edit(OUT, "o = engine.outputs(spec, model, rows, sd)\n"
                      "    return [{'head': 'binary', 'p': 0.5} if x['head'] == 'binary' else x for x in o]")
    commit_train(env["ws"], worse, "coin flip winner")
    rec2 = ar.run_trial("nba", env["exp"], env["ws"], "worse")
    assert rec2["markets"]["winner"]["decision"] == "reject"
    assert rec2["markets"]["spread"]["incumbent"].startswith(f"trial {rec['trial_id']}")
    assert rec2["markets"]["spread"]["decision"] == "reject"              # identical to the incumbent: no improvement
    inc = ar.incumbents(env["root"], "nba", env["meta"])
    assert inc["spread"]["trial"] == rec["trial_id"] and inc["winner"]["trial"] is None
    events = [(r["event"], r.get("status")) for r in records(env["root"])]
    assert events == [("started", None), ("finished", "keep"), ("started", None), ("finished", "discard")]
    tsv = (env["root"] / "results.tsv").read_text().splitlines()
    assert len(tsv) == 3 and tsv[1].split("\t")[4] == "keep" and tsv[1].split("\t")[2].startswith("spread:-")
    assert tsv[2].split("\t")[2] == "-"                                   # rejected markets are not listed as gains


def test_dropping_games_is_a_coverage_failure_not_an_improvement(env):
    commit_train(env["ws"], edit(OUT, "o = engine.outputs(spec, model, rows, sd)\n"
                                      "    return [None if x['head'] == 'normal' and r.game_id.endswith('0') else x\n"
                                      "            for r, x in zip(rows, o)]"),
                 "drop every tenth game")
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    s = rec["markets"]["spread"]
    assert s["decision"] == "reject" and "coverage loss" in s["reason"] and s["coverage"] < 1
    assert s["candidate_matched"]["log_loss"] < s["incumbent_matched"]["log_loss"]    # better on matched, still rejected
    assert rec["status"] == "discard"


@pytest.mark.parametrize("bad,match", [("1.5", "not finite"), ("float('nan')", "not finite"), ("'x'", "not numeric")])
def test_invalid_probabilities_make_the_trial_invalid(env, bad, match):
    commit_train(env["ws"], edit(OUT, "o = engine.outputs(spec, model, rows, sd)\n"
                                      f"    return [{{'head': 'binary', 'p': {bad}}} if x['head'] == 'binary' else x for x in o]"),
                 "bad probabilities")
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    assert rec["status"] == "invalid" and match in rec["reason"]
    assert "markets" not in rec and ar.incumbents(env["root"], "nba", env["meta"])["winner"]["trial"] is None


def test_wrong_output_shapes_and_counts_are_invalid(env):
    commit_train(env["ws"], edit(OUT, "return [] if rows[0].game_id.endswith('-5') else engine.outputs(spec, model, rows, sd)"),
                 "drops an output")
    assert "outputs for" in ar.run_trial("nba", env["exp"], env["ws"])["reason"]
    commit_train(env["ws"], edit(OUT, "return [{'head': spec.head, 'extra': 1} for _ in rows]"), "unknown keys")
    assert ar.run_trial("nba", env["exp"], env["ws"])["status"] == "invalid"


def test_unknown_games_and_impossible_pushes_are_invalid():
    data, scored = synthetic(), [2022, 2023, 2024]
    gid = next(r["game_id"] for r in data["tables"]["spread"] if r["season"] == 2023)
    assert "unknown" in ar.validate(data, {"winner": {"vectors": {"nope": [1, 0, 0]}}}, scored)[0]
    assert "push" in ar.validate(data, {"spread": {"vectors": {gid: [0.4, 0.2, 0.4]}}}, scored)[0]
    old = next(r["game_id"] for r in data["tables"]["winner"] if r["season"] == 2020)
    assert "out-of-scope" in ar.validate(data, {"winner": {"vectors": {old: [1, 0, 0]}}}, scored)[0]


def test_an_incumbent_without_decided_picks_is_not_a_pass():
    data, scored = synthetic(), [2022, 2023, 2024]
    rows = ar.cohort(data, "spread", scored)
    for r in rows:
        r["vectors"]["points_normal"] = (0.5, 0.0, 0.5)
    cand = {r["game_id"]: [0.9, 0.0, 0.1] if r["realised"] == 0 else [0.1, 0.0, 0.9] for r in rows}
    res = ar.score_market(data, "spread", cand, {"trial": None, "member": "points_normal", "label": "p"}, scored)
    assert res["decision"] == "reject" and "undefined" in res["reason"]


# --- The sandbox (the agent's code cannot read the data or the log, write anything, or patch the evaluator) ------------
def test_the_candidate_process_cannot_read_labels_or_write_the_log(env):
    cache = env["meta"]["cache"]["file"]
    log = env["root"] / "trials.jsonl"
    probe = edit("def candidates(sport):\n", "import numpy\n\n\ndef candidates(sport):\n"
                 f"    numpy.fromfile({cache!r}, dtype='uint8', count=8)\n")
    commit_train(env["ws"], probe, "reads the frozen dataset")
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    assert rec["status"] == "crash" and "PermissionError" in rec["reason"]
    other = env["ws"].parent / "another-store-copy.sqlite"            # any other copy of a store on this machine
    other.write_bytes(b"labels")
    commit_train(env["ws"], edit("def candidates(sport):\n", "import numpy\n\n\ndef candidates(sport):\n"
                                 f"    numpy.fromfile({str(other)!r}, dtype='uint8', count=4)\n"), "reads another copy")
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    assert rec["status"] == "crash" and "PermissionError" in rec["reason"]
    before = log.read_bytes()
    probe = edit("def candidates(sport):\n", "import numpy\n\n\ndef candidates(sport):\n"
                 f"    numpy.savetxt({str(log)!r}, [1])\n")
    commit_train(env["ws"], probe, "overwrites the log")
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    assert rec["status"] == "crash" and "PermissionError" in rec["reason"]
    assert log.read_bytes().startswith(before)                    # only appended by the evaluator


def test_patching_the_engine_inside_the_candidate_cannot_leak_labels(env):
    leak = edit("def candidates(sport):\n", "engine.training_rows = lambda rows, s, c: [r for r in rows if r.y is not None]\n\n\n"
                "def candidates(sport):\n")
    assert ar.static_check(leak) == []                             # allowed syntax; harmless because the walk is not there
    first = ar.run_trial("nba", env["exp"], env["ws"])
    commit_train(env["ws"], leak, "patches the engine")
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    assert rec["status"] in ("keep", "discard")
    assert rec["fits"] == first["fits"]                             # the same training rows reached every fit


# --- Workspace protection, failures, retries, concurrency -------------------------------------------------------------
def test_protected_files_other_files_and_uncommitted_changes(env):
    ws = env["ws"]
    (ws / "train.py").write_text(BASELINE + "\n# uncommitted\n")
    with pytest.raises(ar.Refused, match="uncommitted"):
        ar.run_trial("nba", env["exp"], ws)
    git(ws, "checkout", "-q", "train.py")
    for f in ("prepare.py", "program.md"):
        original = (ws / f).read_text()
        (ws / f).write_text(original + "tampered\n")
        git(ws, "commit", "-qam", f"tamper {f}")
        rec = ar.run_trial("nba", env["exp"], ws)
        assert rec["status"] == "invalid" and f"{f} differs" in rec["reason"]
        (ws / f).write_text(original)
        git(ws, "commit", "-qam", f"restore {f}")
    (ws / "notes.py").write_text("x = 1\n")
    git(ws, "add", "notes.py")
    git(ws, "commit", "-qm", "extra file")
    rec = ar.run_trial("nba", env["exp"], ws)
    assert rec["status"] == "invalid" and "notes.py" in rec["reason"]


def test_a_forbidden_import_is_logged_as_invalid_without_running(env):
    commit_train(env["ws"], "import os\n" + BASELINE, "reads files")
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    assert rec["status"] == "invalid" and "'os'" in rec["reason"] and "resources" not in rec


def test_postgame_goalie_inputs_are_refused(env):
    commit_train(env["ws"], edit('"binary", "nba_base"', '"binary", "nhl_goalie"'), "goalie features")
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    assert rec["status"] == "invalid" and "postgame goalie" in rec["reason"]


def test_dataset_and_app_code_drift_are_refused(env, monkeypatch):
    real_fn = ar.app_identity
    real = real_fn()
    monkeypatch.setattr(ar, "app_identity", lambda: {**real, "files": "changed"})
    with pytest.raises(ar.Refused, match="not prepared"):         # a changed evaluator is a different protocol id
        ar.run_trial("nba", env["exp"], env["ws"])
    monkeypatch.setattr(ar, "app_identity", real_fn)
    os.chmod(env["meta"]["cache"]["file"], 0o644)
    Path(env["meta"]["cache"]["file"]).write_bytes(b"tampered")
    with pytest.raises(ar.Refused, match="frozen dataset changed"):
        ar.run_trial("nba", env["exp"], env["ws"])


def test_timeouts_kill_the_process_group_crashes_are_logged_and_retries_bounded(env, monkeypatch):
    hang = edit("def fit(spec, rows):\n    return engine.fit(spec, rows)",
                "def fit(spec, rows):\n    import numpy\n    numpy.zeros(1)\n    while True:\n        pass")
    commit_train(env["ws"], hang, "hangs")
    rec = ar.run_trial("nba", env["exp"], env["ws"], timeout_s=5)
    assert rec["status"] == "timeout" and rec["resources"]["timed_out"]
    trial_dir = str(env["root"] / "trials" / rec["trial_id"])
    assert subprocess.run(["pgrep", "-f", trial_dir], capture_output=True, text=True).stdout.split() == []
    assert ar.run_trial("nba", env["exp"], env["ws"], timeout_s=5)["cached"]       # no silent rerun
    monkeypatch.setitem(ar.BUDGETS, "max_retries", 1)
    assert ar.run_trial("nba", env["exp"], env["ws"], retry=True, timeout_s=5)["status"] == "timeout"
    last = ar.run_trial("nba", env["exp"], env["ws"], retry=True, timeout_s=5)
    assert last["cached"] and last["note"] == "retry budget exhausted"
    commit_train(env["ws"], edit("return engine.fit(spec, rows)", "raise RuntimeError('boom')"), "crashes")
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    assert rec["status"] == "crash" and "boom" in rec["reason"]
    assert [r["status"] for r in finished(env["root"])] == ["timeout", "timeout", "crash"]


def test_the_trial_timeout_cannot_exceed_the_budget(env, monkeypatch):
    monkeypatch.setitem(ar.BUDGETS, "trial_timeout_s", 60)
    ar.run_trial("nba", env["exp"], env["ws"], timeout_s=10_000)
    assert [r["timeout_s"] for r in records(env["root"]) if r["event"] == "started"] == [60]


def test_an_interrupted_run_is_recorded_and_the_same_trial_is_idempotent(env):
    env["root"].mkdir(parents=True, exist_ok=True)
    with (env["root"] / "trials.jsonl").open("a") as fh:
        fh.write(json.dumps({"trial_id": "x", "key": "k", "commit": "c" * 40, "sport": "nba", "protocol_id": "p",
                             "event": "started", "train_sha256": "s"}) + "\n")
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    assert (finished(env["root"])[0]["trial_id"], finished(env["root"])[0]["status"]) == ("x", "interrupted")
    n = len(records(env["root"]))
    again = ar.run_trial("nba", env["exp"], env["ws"])
    assert again["cached"] and again["trial_id"] == rec["trial_id"] and len(records(env["root"])) == n


def test_identical_code_under_a_new_commit_is_recorded_as_cached_and_never_counts_as_a_new_keep(env):
    first = ar.run_trial("nba", env["exp"], env["ws"])
    assert first["status"] == "keep"
    git(env["ws"], "commit", "--allow-empty", "-qm", "empty")       # same train.py, but the incumbents changed: re-run
    second = ar.run_trial("nba", env["exp"], env["ws"])
    assert not second.get("cached_from") and second["markets"]["spread"]["decision"] == "reject"
    git(env["ws"], "commit", "--allow-empty", "-qm", "empty again")  # same code, same incumbents: identical evaluation
    third = ar.run_trial("nba", env["exp"], env["ws"])
    assert third["cached_from"] == second["trial_id"] and third["commit"] != second["commit"]
    assert ar.incumbents(env["root"], "nba", env["meta"])["spread"]["trial"] == first["trial_id"]
    assert len([r for r in records(env["root"]) if r["event"] == "started"]) == 2


def test_a_second_concurrent_run_is_refused(env):
    with ar.Lock(env["root"]):
        with pytest.raises(ar.Refused, match="concurrency"):
            ar.run_trial("nba", env["exp"], env["ws"])


def test_trials_never_change_the_snapshot(env):
    before = hashlib.sha256((env["data"] / "nba.sqlite").read_bytes()).hexdigest()
    ar.run_trial("nba", env["exp"], env["ws"])
    assert hashlib.sha256((env["data"] / "nba.sqlite").read_bytes()).hexdigest() == before
    assert not os.access(env["meta"]["cache"]["file"], os.W_OK)


# --- Holdout and daily ------------------------------------------------------------------------------------------------
def test_the_holdout_tests_the_kept_incumbents_once_after_a_terminal_confirmation(env):
    with pytest.raises(ar.Refused, match="no market has a kept trial"):
        ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: True)
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    with pytest.raises(ar.Refused, match="not confirmed"):
        ar.holdout("nba", env["exp"], env["ws"])                      # pytest has no terminal: the agent case
    asked = []
    res = ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: asked.append(p) or True)
    assert asked == [f"spend holdout nba {env['meta']['protocol_id']}"]
    kept = {m for m, x in rec["markets"].items() if x["decision"] == "keep"}
    assert "spread" in kept and res["status"] == "scored" and res["seasons"] == [2025, 2026] and set(res["markets"]) == kept
    s = res["markets"]["spread"]
    assert s["trial"] == rec["trial_id"] and s["vs_reference"]["cohort"] == 2 * GAMES
    assert s["vs_reference"]["coverage"] == 1.0 and s["vs_reference"]["incumbent"] == f"existing member {env['meta']['start_incumbent']['spread']['member']}"
    with pytest.raises(ar.Refused, match="already used"):
        ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: True)
    assert [h["event"] for h in ar._holdout_log(env["root"])] == ["pending", "completed"]


def test_a_holdout_used_under_another_protocol_needs_a_logged_reason(env):
    ar.run_trial("nba", env["exp"], env["ws"])
    old = env["exp"] / "sports-autoresearch-v0-old" / "nba"
    old.mkdir(parents=True)
    (old / "holdout_access.jsonl").write_text(json.dumps({"event": "completed", "at": "x"}) + "\n")
    with pytest.raises(ar.Refused, match="reuse-reason"):
        ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: True)
    res = ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: True, reuse_reason="test")
    assert res["earlier_uses"][0]["protocol_id"] == "sports-autoresearch-v0-old"
    assert ar._holdout_log(env["root"])[0]["reuse_reason"] == "test"


def test_the_holdout_refuses_a_saved_train_py_that_differs_from_the_log(env):
    rec = ar.run_trial("nba", env["exp"], env["ws"])
    (env["root"] / "trials" / rec["trial_id"] / "train.py").write_text("# swapped\n")
    with pytest.raises(ar.Refused, match="differs from the logged sha256"):
        ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: True)
    assert ar._holdout_log(env["root"]) == []                          # refused before any access


def test_a_commit_without_train_py_is_invalid_and_daily_moves_on(env):
    ws = env["ws"]
    git(ws, "rm", "-q", "train.py")
    git(ws, "commit", "-qm", "deletes train.py")
    (ws / "train.py").write_text(BASELINE.replace("t:ridge", "t:ridge3"))
    git(ws, "add", "train.py")
    git(ws, "commit", "-qm", "restores it")
    r = ar.daily(["nba"], env["exp"], ws, max_trials=5, max_minutes=30, timeout_s=300)
    assert [t["status"] for t in r["trials"]][1] == "invalid" and "missing" in r["trials"][1]["reason"]
    assert len(r["trials"]) == 3 and r["stopped"] == "queue empty"


def test_a_failed_holdout_is_logged_and_may_be_repeated(env, monkeypatch):
    ar.run_trial("nba", env["exp"], env["ws"])

    def broken(sport, period):
        raise OSError("disk")

    monkeypatch.setattr(ar, "build_dataset", broken)
    with pytest.raises(OSError):
        ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: True)
    monkeypatch.setattr(ar, "build_dataset", lambda sport, period: synthetic(period))
    assert ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: True)["status"] == "scored"
    assert [h["event"] for h in ar._holdout_log(env["root"])] == ["pending", "failed", "pending", "completed"]


def test_daily_evaluates_queued_commits_within_budget_without_touching_the_working_tree(env):
    ws = env["ws"]
    branch = git(ws, "rev-parse", "--abbrev-ref", "HEAD")
    first = ar.daily(["nba"], env["exp"], ws, max_trials=1, max_minutes=30, timeout_s=300)
    assert [t["commit"] for t in first["trials"]] == [env["meta"]["workspace_base"]]
    assert first["outcome"].startswith("kept vs incumbent (exploratory")
    commit_train(ws, "import os\n" + BASELINE, "invalid")
    commit_train(ws, BASELINE.replace("t:ridge", "t:ridge2"), "renamed spec: a real run")
    (ws / "prepare.py").write_text("# tampered\n")
    git(ws, "commit", "-qam", "tamper")
    head = git(ws, "rev-parse", "HEAD")
    (ws / "train.py").write_text("uncommitted, must stay\n")
    second = ar.daily(["nba"], env["exp"], ws, max_trials=1, max_minutes=30, timeout_s=300)
    assert [t["status"] for t in second["trials"]] == ["invalid", "discard"]   # invalid runs nothing: not counted
    assert second["stopped"] == "trial budget reached"
    third = ar.daily(["nba"], env["exp"], ws, max_trials=5, max_minutes=30, timeout_s=300)
    assert [t["status"] for t in third["trials"]] == ["invalid"] and third["stopped"] == "queue empty"
    assert third["outcome"] == "no improvement found"
    assert Path(third["report_file"]).is_file() and third["sports"]["nba"]["promotion"].startswith("none")
    assert (ws / "train.py").read_text() == "uncommitted, must stay\n"       # never checked anything out
    assert git(ws, "rev-parse", "--abbrev-ref", "HEAD") == branch and git(ws, "rev-parse", "HEAD") == head
