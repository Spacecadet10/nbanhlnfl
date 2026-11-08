"""Soccer history from openfootball (app/soccer_history.py, app/soccer_clubs.py, migration 0036).

Payload shapes copy the pinned openfootball files (football.json league files; champions-league football.txt). What these
tests pin down: bytes are cached with their commit and path; import is idempotent per retrieval; a replay into a fresh
store gives identical rows; corrections append revisions and are never backdated; same-second retrievals are ordered by
fetch id; unknown kickoff times stay unknown; every UEFA deciding match and every extra-time note is excluded from
90-minute grading; a wrong-season date is rejected, never re-dated; club identity is explicit."""
import json
import sqlite3

import pytest

from app import db, soccer_clubs, soccer_history as h

SHA_A = "a" * 40
SHA_B = "b" * 40
T1, T2, T3 = "2026-11-08T10:00:00Z", "2026-11-08T11:00:00Z", "2026-11-08T12:00:00Z"


def league(matches, name="English Premier League 2023/24"):
    return json.dumps({"name": name, "matches": matches}).encode()


def m(rnd, day, home, away, score=None, time="15:00", **extra):
    out = {"round": rnd, "date": day, "team1": home, "team2": away, **extra}
    if time is not None:
        out["time"] = time
    if score is not None:
        out["score"] = score
    return out


EPL = [m("Matchday 1", "2023-08-12", "Arsenal FC", "Nottingham Forest FC", {"ft": [2, 1], "ht": [2, 0]}),
       m("Matchday 1", "2023-08-12", "Burnley FC", "Manchester City FC", {"ft": [0, 3]}),
       m("Matchday 2", "2023-08-19", "Manchester City FC", "Arsenal FC", [0, 0]),
       m("Matchday 2", "2023-08-19", "Nottingham Forest FC", "Burnley FC", None, time=None)]

CL = """= UEFA Champions League 2023/24

# Matches    6

▪ Group A
  Tue Sep 19 2023
    18:45  AC Milan (ITA)          v Newcastle United FC (ENG)  0-0
           BSC Young Boys (SUI)    v RB Leipzig (GER)         1-3 (1-1)
▪ Finals, Round of 16
  Tue Feb 13
    21:00  FC Porto (POR)          v Arsenal FC (ENG)         1-0 (0-0)
  Tue Mar 12
    21:00  Arsenal FC (ENG)        v FC Porto (POR)           4-2 pen. 1-0 a.e.t. (1-0, 1-0)
  Wed Mar 13
    21:00  FC Barcelona (ESP)      v SSC Napoli (ITA)         3-1 (3-1)
▪ Finals, Final
  Sat Jun 1
    21:00  Borussia Dortmund (GER) v Real Madrid CF (ESP)     0-2 (0-0)
"""


class FakeHub:
    """openfootball as served by GitHub: commit -> {path: bytes}."""

    def __init__(self, commits):
        self.commits, self.calls = commits, []

    def __call__(self, url):
        self.calls.append(url)
        for sha, files in self.commits.items():
            if url.endswith(f"/git/trees/{sha}?recursive=1"):
                return json.dumps({"sha": sha, "truncated": False,
                                   "tree": [{"path": p, "type": "blob", "sha": h.git_blob_sha(b)} for p, b in files.items()]}).encode()
            for p, body in files.items():
                if url.endswith(f"/{sha}/{p}"):
                    return body
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c)
    db.claim_store(c, "soccer")
    c.commit()
    yield c
    c.close()


def clock(*times):
    it = iter(times)
    return lambda: next(it)


def fetch_league(c, sha, body, at, path="2023-24/en.1.json"):
    hub = FakeHub({sha: {path: body}})
    h.download(c, pins={"football.json": sha}, fetch=hub, sleep=0, now=clock(at, at), only={"football.json"})
    return h.replay(c)


def fetch_uefa(c, sha, body, at, path="2023-24/cl.txt"):
    hub = FakeHub({sha: {path: body}})
    h.download(c, pins={"champions-league": sha}, fetch=hub, sleep=0, now=clock(at, at), only={"champions-league"})
    return h.replay(c)


def rows(c, comp="eng.1"):
    return {(r["home_name"], r["away_name"]): r for r in
            c.execute("SELECT * FROM soccer_hist_matches WHERE competition_id = ?", (comp,))}


# --- Provenance and download --------------------------------------------------------------------------------------
def test_download_caches_bytes_with_commit_path_and_retrieval(store):
    body = league(EPL)
    fetch_league(store, SHA_A, body, T1)
    f = store.execute("SELECT * FROM soccer_source_files WHERE kind = 'results'").fetchone()
    assert (f["repo"], f["commit_sha"], f["path"], f["competition_id"], f["season"], f["retrieved_at"]) == \
        ("football.json", SHA_A, "2023-24/en.1.json", "eng.1", 2023, T1)
    raw = store.execute("SELECT * FROM raw_responses WHERE raw_id = ?", (f["raw_id"],)).fetchone()
    assert raw["source"] == "openfootball" and raw["importer_version"] == h.IMPORTER_VERSION
    assert raw["endpoint"] == f"https://raw.githubusercontent.com/openfootball/football.json/{SHA_A}/2023-24/en.1.json"
    from app import raw_cache
    assert raw_cache.load(store, f["raw_id"]) == body
    tree = store.execute("SELECT * FROM soccer_source_files WHERE kind = 'tree'").fetchone()
    assert tree["commit_sha"] == SHA_A and tree["competition_id"] is None


def test_download_resolves_head_and_caches_the_commit_response(store):
    hub = FakeHub({SHA_A: {"2023-24/en.1.json": league(EPL)}})
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/commits/HEAD"):
            return json.dumps({"sha": SHA_A}).encode()
        return hub(url)
    rep = h.download(store, fetch=fetch, sleep=0, now=clock(T1, T1, T1), only={"football.json"})
    assert rep["commits"] == {"football.json": SHA_A}
    assert store.execute("SELECT commit_sha FROM soccer_source_files WHERE kind = 'commit'").fetchone()[0] == SHA_A


def test_download_refuses_a_truncated_tree_or_a_short_sha(store):
    with pytest.raises(ValueError):
        h.download(store, pins={"football.json": "abc"}, fetch=lambda u: b"", sleep=0, only={"football.json"})

    def truncated(url):
        return json.dumps({"truncated": True, "tree": []}).encode()
    with pytest.raises(RuntimeError, match="truncated"):
        h.download(store, pins={"football.json": SHA_A}, fetch=truncated, sleep=0, only={"football.json"})


def test_out_of_scope_files_are_not_downloaded():
    tree = {"truncated": False, "tree": [{"path": p, "type": "blob"} for p in (
        "2023-24/en.1.json", "2023-24/en.2.json", "2023-24/uefa.cl.json", "2025-26/en.1-full.json", "2019/br.1.json",
        "2023-24/clq.txt")]}
    assert h.results_files("football.json", tree) == [("eng.1", 2023, "2023-24/en.1.json")]
    tree2 = {"truncated": False, "tree": [{"path": p, "type": "blob"} for p in (
        "2023-24/cl.txt", "2023-24/el.txt", "2023-24/conf.txt", "2024-25/clq.txt", "2024-25/elq.txt")]}
    assert h.results_files("champions-league", tree2) == [("uefa.champions", 2023, "2023-24/cl.txt"),
                                                         ("uefa.europa", 2023, "2023-24/el.txt")]


def test_soccer_history_refuses_a_store_of_another_sport(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="nba")
    db.migrate(c)
    db.claim_store(c, "nba")
    with pytest.raises(db.StoreMismatch):
        h.download(c, pins={"football.json": SHA_A}, fetch=lambda u: b"", only={"football.json"})


# --- Parsing ---------------------------------------------------------------------------------------------------------
def test_league_json_score_shapes_statuses_and_unknown_time(store):
    ms = [*EPL,
          m("Matchday 3", "2023-08-26", "Arsenal FC", "Burnley FC", {}),
          m("Matchday 3", "2023-08-26", "Manchester City FC", "Nottingham Forest FC", None, status="cancelled"),
          m("Matchday 4", "2023-09-02", "Burnley FC", "Arsenal FC", {"ft": [0, 3]}, status="awarded"),
          m("Matchday 4", "2023-09-02", "Nottingham Forest FC", "Manchester City FC", {"ft": [1, 1]}, status="weird")]
    fetch_league(store, SHA_A, league(ms), T1)
    r = rows(store)
    assert (r[("Arsenal FC", "Nottingham Forest FC")]["ft_home"], r[("Arsenal FC", "Nottingham Forest FC")]["ht_home"]) == (2, 2)
    assert r[("Burnley FC", "Manchester City FC")]["ht_home"] is None                   # ft only
    assert (r[("Manchester City FC", "Arsenal FC")]["ft_home"], r[("Manchester City FC", "Arsenal FC")]["status"]) == (0, "final")
    unplayed = r[("Nottingham Forest FC", "Burnley FC")]
    assert unplayed["status"] == "result_missing" and unplayed["local_time"] is None and unplayed["local_time_listed"] is None
    assert r[("Arsenal FC", "Burnley FC")]["status"] == "result_missing"               # {} before the retrieval day
    assert r[("Manchester City FC", "Nottingham Forest FC")]["grading"] == "cancelled"
    awarded = r[("Burnley FC", "Arsenal FC")]
    assert (awarded["status"], awarded["grading"]) == ("awarded", "awarded")
    assert ("Nottingham Forest FC", "Manchester City FC") not in r
    assert store.execute("SELECT reason FROM soccer_hist_rejections").fetchall()[0][0] == "unknown_status"


def test_a_future_match_without_a_score_is_scheduled(store):
    body = league([m("Matchday 1", "2026-11-20", "Arsenal FC", "Burnley FC")], name="EPL 2026/27")
    fetch_league(store, SHA_A, body, T1, path="2026-27/en.1.json")
    assert rows(store)[("Arsenal FC", "Burnley FC")]["status"] == "scheduled"


def test_a_league_match_dated_before_the_season_is_rejected_not_redated(store):
    ms = [m("Matchday 1", "2019-08-10", "Arsenal FC", "Burnley FC", {"ft": [1, 0]}),
          m("Matchday 32", "2019-07-02", "Manchester City FC", "Liverpool FC", {"ft": [4, 0]})]   # really 2020-07-02
    fetch_league(store, SHA_A, league(ms), T1, path="2019-20/en.1.json")
    assert set(rows(store)) == {("Arsenal FC", "Burnley FC")}
    rej = store.execute("SELECT reason, detail FROM soccer_hist_rejections").fetchone()
    assert rej["reason"] == "date_before_season_start" and "2019-07-02" in rej["detail"]


def test_txt_layout_years_times_extra_time_and_deciding_matches(store):
    fetch_uefa(store, SHA_A, CL.encode(), T1)
    r = rows(store, "uefa.champions")
    milan = r[("AC Milan", "Newcastle United FC")]
    assert (milan["local_date"], milan["local_time"], milan["local_time_listed"], milan["stage"]) == \
        ("2023-09-19", "18:45", 1, "group")
    yb = r[("BSC Young Boys", "RB Leipzig")]
    assert (yb["local_time"], yb["local_time_listed"]) == ("18:45", 0)                  # carried from the line above
    first_leg = r[("FC Porto", "Arsenal FC")]
    assert (first_leg["local_date"], first_leg["deciding"], first_leg["grading"]) == ("2024-02-13", 0, "gradable")
    second = r[("Arsenal FC", "FC Porto")]
    assert (second["ft_home"], second["ft_away"], second["aet_home"], second["pen_home"], second["pen_away"]) == (1, 0, 1, 4, 2)
    assert (second["deciding"], second["grading"]) == (1, "extra_time_noted")
    # a second leg without any extra-time note is still not gradable: notes are incomplete in this source
    single = r[("FC Barcelona", "SSC Napoli")]
    assert (single["deciding"], single["grading"]) == (1, "deciding_match_unverified")
    final = r[("Borussia Dortmund", "Real Madrid CF")]
    assert (final["local_date"], final["deciding"], final["grading"]) == ("2024-06-01", 1, "deciding_match_unverified")
    assert store.execute("SELECT COUNT(*) FROM soccer_hist_rejections").fetchone()[0] == 0


def test_txt_yearless_date_takes_the_year_its_weekday_fits():
    body = ("▪ Finals, Quarterfinals\n  Fri Aug 7\n    21:00  Manchester City (ENG) v Real Madrid (ESP)  2-1 (1-1)\n"
            "▪ Group A\n  Tue Sep 17\n    21:00  AC Milan (ITA) v Arsenal FC (ENG)  1-1 (0-0)\n").encode()
    p = h.parse_txt(body, "uefa.champions", 2019, "2026-11-08")
    assert [x.local_date for x in p.matches] == ["2020-08-07", "2019-09-17"]
    bad = h.parse_txt(b"\xe2\x96\xaa Group A\n  Mon Sep 17\n    A FC (ENG) v B FC (ENG)  1-0\n", "uefa.champions", 2019,
                      "2026-11-08")
    assert [r[1] for r in bad.rejections] == ["weekday_mismatch", "no_round_or_valid_date"] and not bad.matches


def test_txt_two_leg_playoffs_split_by_matchday_pair_into_one_tie():
    body = ("▪ Playoffs, Matchday 1\n  Tue Feb 11 2025\n    21:00  Celtic FC (SCO) v FC Bayern München (GER)  1-2 (0-1)\n"
            "▪ Playoffs, Matchday 2\n  Tue Feb 18 2025\n    21:00  FC Bayern München (GER) v Celtic FC (SCO)  1-1 (0-0)\n"
            "▪ League, Matchday 1\n  Tue Sep 17 2024\n    21:00  Juventus FC (ITA) v PSV (NED)  3-1 (1-0)\n").encode()
    p = h.parse_txt(body, "uefa.champions", 2024, "2026-11-08")
    assert [(x.round, x.stage, x.deciding) for x in p.matches] == [
        ("Playoffs, Matchday 1", "knockout", 0), ("Playoffs, Matchday 2", "knockout", 1),
        ("League, Matchday 1", "league_phase", 0)]


def test_txt_cancelled_awarded_and_unparsed_lines():
    body = ("# Matches 4\n▪ Group B\n  Thu Sep 16 2021\n    18:45  A FC (ENG) v B FC (GER)  [cancelled]\n"
            "           C FC (ESP) v D FC (ITA)  3-0    [awarded]\n           E FC (ENG) v F FC (GER)  2:1\n"
            "    xx\n").encode()
    p = h.parse_txt(body, "uefa.europa", 2021, "2026-11-08")
    assert [(x.status, x.ft) for x in p.matches] == [("cancelled", None), ("awarded", (3, 0))]
    assert sorted(r[1] for r in p.rejections) == ["unparsed_line", "unparsed_score"]
    assert p.header_matches == 4


def test_header_count_disagreement_is_recorded(store):
    fetch_uefa(store, SHA_A, CL.replace("# Matches    6", "# Matches    7").encode(), T1)
    rej = store.execute("SELECT locator, reason FROM soccer_hist_rejections").fetchall()
    assert [tuple(r) for r in rej] == [("file", "header_count_mismatch")]


# --- Identity and clubs ----------------------------------------------------------------------------------------------
def test_league_and_uefa_names_resolve_to_one_club_and_aliases_are_explicit(store):
    fetch_league(store, SHA_A, league([m("Matchday 1", "2019-08-10", "Manchester City", "Tottenham Hotspur", {"ft": [2, 2]})]),
                 T1, path="2019-20/en.1.json")
    fetch_uefa(store, SHA_A, ("▪ Group C\n  Tue Sep 17 2019\n    21:00  Manchester City FC (ENG) v FC Porto (POR)  1-0\n"
                              "  Wed Sep 18 2019\n    21:00  AS Monaco FC (MCO) v Manchester City (ENG)  0-1\n").encode(), T2,
               path="2019-20/cl.txt")
    clubs = {r[0] for r in store.execute("SELECT DISTINCT home_club_id FROM soccer_hist_matches UNION "
                                         "SELECT DISTINCT away_club_id FROM soccer_hist_matches")}
    assert clubs == {"ENG:Manchester City FC", "ENG:Tottenham Hotspur FC", "POR:FC Porto", "FRA:AS Monaco FC"}
    names = {(r["competition_id"], r["source_name"]): r["club_id"] for r in store.execute("SELECT * FROM soccer_club_names")}
    assert names[("uefa.champions", "AS Monaco FC (MCO)")] == "FRA:AS Monaco FC"
    assert names[("eng.1", "Manchester City")] == names[("uefa.champions", "Manchester City FC (ENG)")]


def test_club_map_has_no_chains_and_flags_possible_duplicates():
    targets = set(soccer_clubs.ALIASES.values())
    assert not targets & set(soccer_clubs.ALIASES), "an alias target is itself aliased"
    assert ("ENG:Manchester City FC", "ENG:Manchester City Women") in [tuple(sorted(p)) for p in
                                                                         soccer_clubs.possible_duplicates(
                                                                             {"ENG:Manchester City FC", "ENG:Manchester City Women"})]
    assert soccer_clubs.possible_duplicates({"ITA:Parma FC", "ITA:Parma Calcio 1913"}) == []          # declared DISTINCT
    assert soccer_clubs.possible_duplicates({"ENG:Arsenal FC", "ENG:Chelsea FC"}) == []
    assert soccer_clubs.same_name_other_country({"FRA:AS Monaco FC", "MCO:AS Monaco FC", "ENG:Arsenal FC"}) == \
        [["FRA:AS Monaco FC", "MCO:AS Monaco FC"]]


def test_same_club_both_sides_and_duplicate_identity_are_rejected(store):
    ms = [m("Matchday 1", "2019-08-10", "Manchester City", "Manchester City FC", {"ft": [1, 0]}),
          m("Matchday 1", "2019-08-10", "Arsenal FC", "Burnley FC", {"ft": [1, 0]}),
          m("Matchday 1", "2019-08-11", "Arsenal FC", "Burnley FC", {"ft": [2, 0]})]
    fetch_league(store, SHA_A, league(ms), T1, path="2019-20/en.1.json")
    assert rows(store) == {}
    assert sorted(r[0] for r in store.execute("SELECT reason FROM soccer_hist_rejections")) == \
        ["duplicate_identity", "duplicate_identity", "same_club_both_sides"]


# --- Repeated ingestion, replay, corrections, ordering ---------------------------------------------------------------
def test_repeated_ingestion_of_identical_bytes_adds_no_row_or_revision(store):
    fetch_league(store, SHA_A, league(EPL), T1)
    before = h.digest(store)
    fetch_league(store, SHA_A, league(EPL), T2)
    assert store.execute("SELECT COUNT(*) FROM raw_responses WHERE source = 'openfootball' AND request_key LIKE '%en.1%'"
                         ).fetchone()[0] == 1                                  # same bytes reuse the raw row ...
    assert store.execute("SELECT COUNT(*) FROM soccer_source_files WHERE kind = 'results'").fetchone()[0] == 2  # ... 2 fetches
    after = h.digest(store)
    assert after["soccer_hist_revisions"] == before["soccer_hist_revisions"]
    assert store.execute("SELECT COUNT(*) FROM soccer_hist_matches").fetchone()[0] == 4
    assert {r["last_seen_at"] for r in rows(store).values()} == {T2}


def test_replay_into_the_same_store_changes_nothing_and_into_a_fresh_store_gives_identical_rows(store, tmp_path, monkeypatch):
    fetch_league(store, SHA_A, league(EPL), T1)
    changed = [dict(x) for x in EPL]
    changed[0]["score"] = {"ft": [3, 1], "ht": [2, 0]}
    fetch_league(store, SHA_B, league(changed), T2)
    fetch_uefa(store, SHA_A, CL.encode(), T3)
    store.commit()
    original = h.digest(store)
    again = h.replay(store)
    assert again["inserted"] == again["changed"] == 0 and h.digest(store) == original
    src = store.execute("PRAGMA database_list").fetchone()[2]
    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    (fresh_dir / "raw").symlink_to(tmp_path / "raw")
    monkeypatch.setenv("NBA_DATA_DIR", str(fresh_dir))
    c2 = db.connect(sport="soccer")
    db.migrate(c2)
    db.claim_store(c2, "soccer")
    c2.commit()
    h.copy_provenance(c2, src)
    h.replay(c2)
    assert h.digest(c2) == original


def test_a_result_correction_appends_a_revision_known_only_from_its_retrieval(store):
    fetch_league(store, SHA_A, league(EPL), T1)
    changed = [dict(x) for x in EPL]
    changed[0]["score"] = {"ft": [3, 1], "ht": [2, 0]}
    stats = fetch_league(store, SHA_B, league(changed), T2)
    assert stats["result_corrections"] == 1
    row = rows(store)[("Arsenal FC", "Nottingham Forest FC")]
    assert (row["ft_home"], row["result_available_at"], row["result_availability_basis"]) == (3, T2, "observed_correction")
    revs = store.execute("SELECT revision, ft_home, result_available_at, result_availability_basis, observed_at FROM "
                         "soccer_hist_revisions WHERE hist_match_id = ? ORDER BY revision", (row["hist_match_id"],)).fetchall()
    assert [tuple(r) for r in revs] == [(1, 2, "2023-08-13T06:00:00Z", "assumed_next_day_0600Z", T1),
                                        (2, 3, T2, "observed_correction", T2)]
    # unchanged matches keep revision 1 and their assumed availability
    other = rows(store)[("Burnley FC", "Manchester City FC")]
    assert other["result_available_at"] == "2023-08-13T06:00:00Z"


def test_a_first_result_seen_after_an_earlier_listing_without_it_is_known_only_from_that_retrieval(store):
    # review S2 MEDIUM-2: the earlier retrieval showed the source did not have the result yet: never backdated
    fetch_league(store, SHA_A, league(EPL), T1)
    played = [dict(x) for x in EPL]
    played[3]["score"] = {"ft": [1, 1]}
    stats = fetch_league(store, SHA_B, league(played), T2)
    assert stats["result_corrections"] == 0 and stats["changed"] == 1
    r = rows(store)[("Nottingham Forest FC", "Burnley FC")]
    assert (r["status"], r["result_available_at"], r["result_availability_basis"]) == ("final", T2, "observed_retrieval")
    rev = store.execute("SELECT result_available_at, result_availability_basis FROM soccer_hist_revisions WHERE "
                        "hist_match_id = ? ORDER BY revision DESC", (r["hist_match_id"],)).fetchone()
    assert tuple(rev) == (T2, "observed_retrieval")


def test_a_date_correction_of_a_final_moves_its_availability_never_before_the_new_date(store):
    # review S2 MEDIUM-1: same score, later date -> the old next-day availability would precede the match
    fetch_league(store, SHA_A, league(EPL), T1)
    moved = [dict(x) for x in EPL]
    moved[2]["date"] = "2023-10-07"
    fetch_league(store, SHA_B, league(moved), T2)
    r = rows(store)[("Manchester City FC", "Arsenal FC")]
    assert r["local_date"] == "2023-10-07"
    assert (r["result_available_at"], r["result_availability_basis"]) == (T2, "observed_correction")


def test_the_store_refuses_a_result_known_before_the_day_after_its_match(store):
    fetch_league(store, SHA_A, league(EPL), T1)
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE soccer_hist_matches SET result_available_at = '2023-08-12T22:00:00Z' "
                      "WHERE home_name = 'Arsenal FC'")


def test_a_club_map_change_is_refused_on_a_store_built_with_another_map(store, monkeypatch):
    # review S2 MEDIUM-3: a same-store replay used to keep the old club ids silently
    fetch_league(store, SHA_A, league(EPL), T1)
    monkeypatch.setattr(soccer_clubs, "MAP_VERSION", "2099-01-01.1")
    monkeypatch.setitem(soccer_clubs.ALIASES, ("ENG", "Burnley FC"), ("ENG", "Burnley"))
    with pytest.raises(h.MapVersionChanged, match="rebuild"):
        h.replay(store)
    assert rows(store)[("Burnley FC", "Manchester City FC")]["home_club_id"] == "ENG:Burnley FC"


def test_download_refuses_bytes_that_do_not_match_the_tree_blob(store):
    body = league(EPL)

    def fetch(url):
        if "/git/trees/" in url:
            return json.dumps({"truncated": False, "tree": [{"path": "2023-24/en.1.json", "type": "blob",
                                                             "sha": "0" * 40}]}).encode()
        return body
    with pytest.raises(h.CacheMismatch):
        h.download(store, pins={"football.json": SHA_A}, fetch=fetch, sleep=0, only={"football.json"})
    assert store.execute("SELECT COUNT(*) FROM soccer_source_files WHERE kind = 'results'").fetchone()[0] == 0


def test_a_b_a_appends_one_revision_per_change_and_replay_adds_none(store):
    a, b = league(EPL), [dict(x) for x in EPL]
    b[1]["score"] = {"ft": [1, 3]}
    fetch_league(store, SHA_A, a, T1)
    fetch_league(store, SHA_B, league(b), T2)
    fetch_league(store, SHA_A, a, T3)
    mid = rows(store)[("Burnley FC", "Manchester City FC")]["hist_match_id"]
    revs = [tuple(r) for r in store.execute("SELECT revision, ft_home, result_availability_basis FROM soccer_hist_revisions "
                                            "WHERE hist_match_id = ? ORDER BY revision", (mid,))]
    assert revs == [(1, 0, "assumed_next_day_0600Z"), (2, 1, "observed_correction"), (3, 0, "observed_correction")]
    h.replay(store)
    assert store.execute("SELECT COUNT(*) FROM soccer_hist_revisions WHERE hist_match_id = ?", (mid,)).fetchone()[0] == 3


def test_same_second_retrievals_are_ordered_by_fetch_id(store, tmp_path, monkeypatch):
    b = [dict(x) for x in EPL]
    b[1]["score"] = {"ft": [1, 3]}
    fetch_league(store, SHA_A, league(EPL), T1)
    fetch_league(store, SHA_B, league(b), T1)                       # same second, later fetch id: wins
    row = rows(store)[("Burnley FC", "Manchester City FC")]
    assert row["ft_home"] == 1 and row["result_availability_basis"] == "observed_correction"
    store.commit()
    original = h.digest(store)
    h.replay(store)
    assert h.digest(store) == original


def test_a_match_dropped_by_a_later_file_is_kept_and_marked_then_restored(store):
    fetch_league(store, SHA_A, league(EPL), T1)
    fetch_league(store, SHA_B, league(EPL[:3]), T2)
    gone = rows(store)[("Nottingham Forest FC", "Burnley FC")]
    assert gone["in_latest_file"] == 0
    assert store.execute("SELECT in_file FROM soccer_hist_revisions WHERE hist_match_id = ? ORDER BY revision DESC",
                         (gone["hist_match_id"],)).fetchone()[0] == 0
    fetch_league(store, SHA_A, league(EPL), T3)
    assert rows(store)[("Nottingham Forest FC", "Burnley FC")]["in_latest_file"] == 1


def test_an_older_retrieval_imported_late_does_not_overwrite_a_newer_one(store):
    b = [dict(x) for x in EPL]
    b[1]["score"] = {"ft": [1, 3]}
    fetch_league(store, SHA_B, league(b), T2)
    fetch_league(store, SHA_A, league(EPL), T1)                     # retrieved earlier, stored later
    assert rows(store)[("Burnley FC", "Manchester City FC")]["ft_home"] == 1


def test_history_tables_are_append_only(store):
    fetch_league(store, SHA_A, league(EPL), T1)
    for sql in ("UPDATE soccer_hist_revisions SET ft_home = 9", "DELETE FROM soccer_hist_revisions",
                "UPDATE soccer_source_files SET path = 'x'", "DELETE FROM soccer_source_files"):
        with pytest.raises(sqlite3.IntegrityError):
            store.execute(sql)


def test_grading_requires_a_final_regular_non_deciding_match(store):
    fetch_uefa(store, SHA_A, CL.encode(), T1)
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE soccer_hist_matches SET grading = 'gradable' WHERE deciding = 1")


def test_coverage_counts_and_checks(store):
    fetch_league(store, SHA_A, league(EPL), T1)
    fetch_uefa(store, SHA_A, CL.encode(), T2)
    cov = h.coverage(store)
    eng = next(s for s in cov["seasons"] if s["competition_id"] == "eng.1")
    assert (eng["n"], eng["final"], eng["result_missing"], eng["gradable"], eng["time_missing"]) == (4, 3, 1, 3, 1)
    assert eng["latest_file"]["commit_sha"] == SHA_A
    cl = next(s for s in cov["seasons"] if s["competition_id"] == "uefa.champions")
    assert (cl["extra_time_noted"], cl["deciding_unverified"], cl["gradable"]) == (1, 2, 3)
    assert cov["possible_duplicate_clubs"] == [] and cov["anomalies"] == [] and cov["same_name_other_country"] == []


def test_a_second_leg_appearing_makes_the_first_leg_usable_only_from_that_retrieval(store):
    # review S3 L8: a change of whether a result may be used is not backdated
    first = ("▪ Finals, Round of 16\n  Tue Feb 13 2024\n    21:00  FC Porto (POR) v Arsenal FC (ENG)  1-0 (0-0)\n")
    fetch_uefa(store, SHA_A, first.encode(), T1)
    leg1 = rows(store, "uefa.champions")[("FC Porto", "Arsenal FC")]
    assert (leg1["deciding"], leg1["grading"]) == (1, "deciding_match_unverified")
    both = first + "  Tue Mar 12 2024\n    21:00  Arsenal FC (ENG) v FC Porto (POR)  1-0 (1-0)\n"
    fetch_uefa(store, SHA_B, both.encode(), T2)
    leg1 = rows(store, "uefa.champions")[("FC Porto", "Arsenal FC")]
    assert (leg1["deciding"], leg1["grading"], leg1["result_available_at"], leg1["result_availability_basis"]) == \
        (0, "gradable", T2, "observed_retrieval")


def test_a_development_copy_leaves_out_later_seasons_entirely(store, tmp_path, monkeypatch):
    for season, at in ((2021, T1), (2023, T2)):
        body = league([m("Matchday 1", f"{season}-08-14", "Arsenal FC", "Burnley FC", {"ft": [1, 0]})])
        fetch_league(store, SHA_A, body, at, path=f"{season}-{(season + 1) % 100:02d}/en.1.json")
    store.commit()
    src = store.execute("PRAGMA database_list").fetchone()[2]
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "raw").symlink_to(tmp_path / "raw")
    monkeypatch.setenv("NBA_DATA_DIR", str(dev))
    c2 = db.connect(sport="soccer")
    db.migrate(c2)
    db.claim_store(c2, "soccer")
    c2.commit()
    h.copy_provenance(c2, src, max_season=2022)
    h.replay(c2)
    assert {r[0] for r in c2.execute("SELECT season FROM soccer_hist_matches")} == {2021}
    assert {r[0] for r in c2.execute("SELECT season FROM soccer_source_files WHERE kind = 'results'")} == {2021}


def test_a_match_first_seen_in_a_later_retrieval_of_its_file_is_not_backdated(store):
    # S2 re-check MEDIUM-2 rest: a renamed side gives a new identity in the same retrieval that adds the score
    ms = [m("Matchday 1", "2023-08-12", "Arsenal FC", "Burnley", None)]
    fetch_league(store, SHA_A, league(ms), T1)
    renamed = [m("Matchday 1", "2023-08-12", "Arsenal FC", "Burnley FC", {"ft": [0, 1]})]
    fetch_league(store, SHA_B, league(renamed), T2)
    r = rows(store)[("Arsenal FC", "Burnley FC")]
    assert (r["result_available_at"], r["result_availability_basis"]) == (T2, "observed_retrieval")
    assert rows(store)[("Arsenal FC", "Burnley")]["in_latest_file"] == 0


def test_a_dropped_match_is_unusable_only_from_the_retrieval_that_dropped_it(store):
    # S3 re-review L8 rest: the withdrawal revision is not backdated
    fetch_league(store, SHA_A, league(EPL), T1)
    fetch_league(store, SHA_B, league(EPL[1:]), T2)
    gone = rows(store)[("Arsenal FC", "Nottingham Forest FC")]
    rev = store.execute("SELECT in_file, result_available_at, result_availability_basis FROM soccer_hist_revisions WHERE "
                        "hist_match_id = ? ORDER BY revision DESC", (gone["hist_match_id"],)).fetchone()
    assert tuple(rev) == (0, T2, "observed_retrieval")
