from app import adaptive_replay


def _row(season, vectors, realised):
    return {"season": season, "vectors": vectors, "realised": realised}


def test_single_members_report_hit_rate_on_each_members_own_evaluated_games():
    rows = [_row(2020, {"a": (0.7, 0, 0.3), "b": (0.4, 0, 0.6)}, 0),     # a wins, b loses
            _row(2020, {"a": (0.6, 0, 0.4)}, 2),                         # a loses; b has no output
            _row(2020, {"a": (0.2, 0, 0.8), "b": (0.3, 0, 0.7)}, 2),     # both win
            _row(2019, {"a": (0.9, 0, 0.1), "b": (0.9, 0, 0.1)}, 2),     # not an evaluated season
            _row(2020, {"a": (0.5, 0, 0.5), "b": (0.8, 0, 0.2)}, 1)]     # push: not scored
    out = adaptive_replay.single_members("spread", rows, ["a", "b", "c"], [2020])
    assert set(out) == {"a", "b"}
    assert (out["a"]["n"], out["a"]["wins"], out["a"]["losses"]) == (3, 2, 1)
    assert (out["b"]["n"], out["b"]["wins"], out["b"]["losses"]) == (2, 1, 1)
    assert out["a"]["accuracy"] == 2 / 3 and out["b"]["accuracy"] == 0.5
    assert out["a"]["log_loss"] is not None and len(out["a"]["accuracy_ci95"]) == 2


def test_single_members_are_paired_with_the_primary_on_shared_games():
    rows = [_row(2020, {"primary": (0.6, 0, 0.4), "a": (0.8, 0, 0.2)}, 0) for _ in range(6)] + \
           [_row(2020, {"primary": (0.6, 0, 0.4), "a": (0.3, 0, 0.7)}, 2) for _ in range(6)] + \
           [_row(2020, {"primary": (0.6, 0, 0.4)}, 2)]                                       # a absent: not paired
    for i, r in enumerate(rows):
        r["cutoff_at"] = f"2020-0{1 + i % 3}-01T00:00:00Z"
    out = adaptive_replay.single_members("winner", rows, ["primary", "a"], [2020])
    assert out["primary"]["vs_primary"] is None
    assert out["a"]["vs_primary"]["games"] == 12 and out["a"]["vs_primary"]["mean"] < 0      # a is better: negative


def test_single_members_report_coverage_and_skip_seasons_before_the_first_fit():
    rows = [_row(2019, {"primary": (0.6, 0, 0.4), "a": (0.9, 0, 0.1)}, 2),       # before a's first fit: not scored
            _row(2020, {"primary": (0.6, 0, 0.4), "a": (0.8, 0, 0.2)}, 0),
            _row(2020, {"primary": (0.6, 0, 0.4)}, 2)]
    for r in rows:
        r["cutoff_at"] = "2020-01-01T00:00:00Z"
    out = adaptive_replay.single_members("winner", rows, ["primary", "a"], [2019, 2020], {"a": 2020})
    assert out["a"]["n"] == 1 and out["a"]["coverage"] == 1 / 3 and out["a"]["vs_primary"]["games"] == 1
    assert out["primary"]["n"] == 3 and out["primary"]["coverage"] == 1


def test_game_rows_keep_every_vector_of_evaluated_games_only():
    rows = [dict(_row(2020, {"primary": (0.6, 0, 0.4), "a": (0.1234567, 0, 0.8765433)}, 0), game_id="g1", cutoff_at="c", line=-1.5),
            dict(_row(2019, {"primary": (0.6, 0, 0.4)}, 2), game_id="g0", cutoff_at="c")]
    out = adaptive_replay.game_rows(rows, [2020])
    assert out == [{"game_id": "g1", "season": 2020, "cutoff_at": "c", "line": -1.5, "realised": 0,
                    "vectors": {"primary": [0.6, 0.0, 0.4], "a": [0.123457, 0.0, 0.876543]}}]


# --- Bookmaker lines (2026-10-16: line markets were graded only at the primary's own line, e.g. NHL puck line at ±0.5,
# so no comparison with the books' ±1.5 was possible) ------------------------------------------------------------------
def _hockey_rows(n=6):
    from app.models.base import PregameGame
    from app.nhl_eval import HockeyRow
    return [HockeyRow(PregameGame(f"nhl:{i}", 2024, "regular", f"2024-01-{i + 1:02d}T23:00:00Z", "nhl:6", "nhl:10", False,
                                  f"2024-01-{i + 1:02d}T21:00:00Z"),
                      f"2024-01-{i + 1:02d}T21:00:00Z", 3, 1, f"2024-01-{i + 2:02d}T03:00:00Z", "REG") for i in range(n)]


def test_book_lines_replace_the_model_line_for_every_vector_and_the_settlement():
    from app import combination_research as cr
    from app.models.hockey import GoalsConfig, GoalsPoisson, HockeyElo, HockeyEloConfig
    rows = _hockey_rows()
    book = {"nhl:4": {"spread": -1.5, "total": 3.5}, "nhl:5": {"spread": 1.5}}
    tables, _ = cr._tables(rows, [HockeyElo(HockeyEloConfig())], [GoalsPoisson(GoalsConfig())], ["reg3", "spread", "total"],
                           decision_of=lambda r: r.decision, book_lines=book)
    spread = {r["game_id"]: r for r in tables["spread"] if r["line_basis"] == "book"}
    assert set(spread) == {"nhl:4", "nhl:5"} and [r["game_id"] for r in tables["total"] if r["line_basis"] == "book"] == ["nhl:4"]
    assert spread["nhl:4"]["line"] == -1.5 and spread["nhl:4"]["realised"] == 0       # 3-1: home covers -1.5
    assert spread["nhl:5"]["line"] == 1.5
    assert next(r for r in tables["total"] if r["line_basis"] == "book")["realised"] == 0     # 4 goals over 3.5
    plain, _ = cr._tables(rows, [HockeyElo(HockeyEloConfig())], [GoalsPoisson(GoalsConfig())], ["reg3", "spread"],
                          decision_of=lambda r: r.decision)
    assert len(tables["reg3"]) == len(tables["winner"]) == 6 and len(plain["spread"]) == 6
    # priced at the book line: covering -1.5 is unlikely, +1.5 likely (near-even teams)
    assert spread["nhl:4"]["vectors"]["goals_poisson"][0] < 0.5 < spread["nhl:5"]["vectors"]["goals_poisson"][0]


def test_the_replay_period_is_development_or_validation_only():
    import pytest
    with pytest.raises(ValueError):
        adaptive_replay.periods("nhl", "test")
    with pytest.raises(ValueError):
        adaptive_replay.periods("nba", "test")
    assert adaptive_replay.periods("nhl", "validation")[0] == (2022, 2023, 2024)
    assert adaptive_replay.periods("nhl", None)[0] == (2018, 2019, 2020, 2021)
    assert adaptive_replay.periods("nba", None)[0] == (2022, 2023, 2024)


def test_games_without_a_book_line_keep_the_model_line_for_learning_and_are_marked():
    from app import combination_research as cr
    from app.models.hockey import GoalsConfig, GoalsPoisson, HockeyElo, HockeyEloConfig
    tables, _ = cr._tables(_hockey_rows(), [HockeyElo(HockeyEloConfig())], [GoalsPoisson(GoalsConfig())], ["spread"],
                           decision_of=lambda r: r.decision, book_lines={"nhl:4": {"spread": -1.5}})
    basis = {r["game_id"]: r["line_basis"] for r in tables["spread"]}
    assert len(basis) == 6 and basis["nhl:4"] == "book" and basis["nhl:0"] == "model"
    assert adaptive_replay.scored_rows([{"line_basis": "model"}, {"line_basis": "book"}, {}], True) == [
        {"line_basis": "book"}]
    assert len(adaptive_replay.scored_rows([{"line_basis": "model"}, {}], False)) == 2


def test_only_the_default_replay_writes_the_served_evidence_summary():
    assert adaptive_replay.writes_evidence("nhl", "development", None)
    assert not adaptive_replay.writes_evidence("nhl", "validation", None)
    assert not adaptive_replay.writes_evidence("nba", "validation", {"g": {"spread": -3.5}})
