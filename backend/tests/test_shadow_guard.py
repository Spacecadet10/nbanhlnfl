"""Review finding (Nov 15), not fixed in place: coverage._existing's pre-canonical night-before winner fallback excludes only
`research-%` and near-tipoff versions, so a live shadow-challenger row (shadow.py) would be graded as the primary forecast.
coverage.py is sealed in the NFL activation (nfl_live.definition): editing it would switch NFL off, so the fix waits for the
next NFL re-seal (HANDOFF.md). It is harmless while no shadow row can be written; these tests keep that true. If shadow
recording is ever brought back, fix coverage._existing first (filter on the primary model version, as LATEST_LIVE_SQL)."""
import pytest

from app import cli, jobs, shadow


def test_no_production_path_records_shadow_forecasts(monkeypatch):
    calls = []
    monkeypatch.setattr(shadow, "predict", lambda conn, **kw: calls.append(kw) or {"status": "ok"})
    assert cli.main(["shadow-predict", "--record"]) == 0
    assert all(not kw.get("write") for kw in calls)            # the --record flag is refused before any write
    with pytest.raises(RuntimeError):
        jobs.shadow_forecasts(None)                              # the scheduled job refuses too
