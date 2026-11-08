"""International soccer protocol guards (app/intl_eval.py; docs/INTERNATIONAL_PROTOCOL.md).

Real data only: tests/fixtures/international_real/ is a byte-for-byte excerpt of martj42/international_results (CC0) at
commit b0948cb5 (every match since 1990 of six national teams plus three rows named in its README). Nothing invented."""
import json
import shutil
from pathlib import Path

import pytest

from app import intl_eval as ie

REAL = Path(__file__).parent / "fixtures" / "international_real"


def real_files() -> dict:
    return {n: (REAL / n).read_bytes() for n in ie.FILES}


def cache(tmp_path) -> Path:
    c = tmp_path / "cache"
    shutil.copytree(REAL, c)
    (c / "manifest.json").write_text(json.dumps({"files": {n: ie.sha((c / n).read_bytes()) for n in ie.FILES}}))
    return c


def match(ms, day, home, away):
    return next(m for m in ms if (m.day, m.home, m.away) == (day, home, away))


def test_ninety_minute_labels_on_real_matches():
    ms = ie.parse(real_files())
    assert match(ms, "2022-12-18", "Argentina", "France").label == 1          # 3-3, shootout: level after 90
    assert match(ms, "2022-12-09", "Croatia", "Brazil").label == 1            # 1-1 a.e.t., shootout
    assert match(ms, "2016-06-25", "Croatia", "Portugal").label == 1          # 0-1, Quaresma 117': extra time
    assert match(ms, "2023-07-12", "United States", "Panama").label == 1     # 1-1 a.e.t. (99', 105'), shootout
    ed = match(ms, "2021-07-07", "England", "Denmark")                         # Kane 104': stoppage or extra time?
    assert ed.label is None and "91-105" in ed.exclusion
    ms_ = match(ms, "2026-01-18", "Morocco", "Senegal")                       # final tournament, no goal minutes
    assert ms_.label is None and "incomplete" in ms_.exclusion
    assert match(ms, "2015-03-12", "Timor-Leste", "Mongolia").label == 2      # qualifier: 0-3 stands
    assert match(ms, "2021-06-29", "England", "Germany").label == 0
    assert match(ms, "2022-12-18", "Argentina", "France").group == "world_cup"


def test_forecasts_never_see_results_of_their_own_day_or_later():
    ms = ie.parse(real_files())

    class Spy(ie.Frequency):
        def __init__(self):
            super().__init__({"home": [.45, .27, .28], "neutral": [.4, .3, .3]})
            self.seen, self.violations = [], 0

        def observe(self, m):
            self.seen.append(m.day)

        def predict(self, m):
            self.violations += sum(1 for d in self.seen if d >= m.day)
            return super().predict(m)
    spy = Spy()
    preds = ie.walk(spy, ms, range(2010, 2022))
    assert preds and spy.violations == 0 and len(spy.seen) > 1000


def test_an_extra_time_win_updates_ratings_as_a_ninety_minute_draw():
    m = match(ie.parse(real_files()), "2016-06-25", "Croatia", "Portugal")
    e = ie.Elo(20, 75, True)
    e.observe(m)
    assert e.r["Croatia"] == e.r["Portugal"] == 1500.0                       # a draw between equal new teams


def test_reserved_years_need_a_seal_are_logged_and_2026_is_never_evaluated(tmp_path):
    c = cache(tmp_path)
    with pytest.raises(ie.Locked):
        ie.load(c, through=2023)
    with pytest.raises(ie.Locked):
        ie.load(c, through=2026, sealed=ie.backtest.seal({"model": "frequency"}), access_log=tmp_path / "log")
    sealed = ie.backtest.seal({"model": "frequency"})
    log = tmp_path / "access.jsonl"
    ms, _ = ie.load(c, through=2023, sealed=sealed, access_log=log, purpose="guard test")
    assert max(m.day for m in ms) < "2024" and json.loads(log.read_text())["candidate_sha256"] == sealed["sha256"]
    with pytest.raises(ie.Locked):
        ie.load(c, through=2023, sealed={**sealed, "model": "elo"}, access_log=log)


def test_changed_data_is_refused(tmp_path):
    c = cache(tmp_path)
    lines = (c / "results.csv").read_bytes().splitlines(keepends=True)
    (c / "results.csv").write_bytes(b"".join(lines[:-1]))                      # one real row removed
    with pytest.raises(ie.FrozenMismatch):
        ie.load(c, through=2019)


def test_the_test_runs_once_on_the_frozen_candidate_and_refuses_changed_code(tmp_path, monkeypatch):
    c = cache(tmp_path)
    ms, manifest = ie.load(c, through=2021)
    sealed = ie.backtest.seal({"model": "frequency", "config": None, "draw_model": None, "frequency": ie.fit_frequency(ms),
                               "protocol_sha256": ie.PROTOCOL_SHA256, "data": manifest, "code_sha256": ie.code_digest()})
    log = tmp_path / "access.jsonl"
    rep = ie.run_test(c, sealed, access_log=log, out_dir=tmp_path / "out")
    assert rep["test"]["overall"]["n"] > 100 and rep["test"]["paired_vs_frequency"]["overall"]["mean"] == 0
    assert ie.read_test(rep)["R1"] is False                                  # the baseline is never "better than itself"
    with pytest.raises(ie.Locked):
        ie.run_test(c, sealed, access_log=log, out_dir=tmp_path / "again")
    monkeypatch.setattr(ie, "code_digest", lambda: "changed")
    with pytest.raises(ie.FrozenMismatch):
        ie.run_test(c, sealed, access_log=tmp_path / "other.jsonl", out_dir=tmp_path / "out3")
