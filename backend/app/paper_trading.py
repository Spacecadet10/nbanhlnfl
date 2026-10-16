"""Paper trading (read-only report; no money, no new rows): would a model's recorded forecasts have beaten the bookmaker?

For every recorded forecast (canonical `game_forecasts`, each adaptive definition in `adaptive_forecasts`, and each registered
single member's vector in `agreement_observations`, as `single:<member>` timed at its recording) of a two-way
quoted market (winner, spread, total), the bookmaker quote is the latest `current` `market_quotes` row
for the same game, market and line retrieved at or before the forecast was generated (ESPN repeats the opening row in
every fetch: it was not on offer once the line moved): the price a bettor could have taken then. A paper bet is the first
such forecast per (source, game, market) whose probability exceeds the quote's de-vigged probability by at least the
threshold, on that side, staked 1 unit at the quoted American price. Close = the last quote at the same line retrieved at
or before the start; closing-line value (CLV) = the de-vigged close probability of the bet side minus the bet-time one
(positive: the market moved toward the bet). Settled with `markets.settle`; a moved start voids the bet.
Limits: one bookmaker (DraftKings via ESPN); a quote's own time is unknown (retrieval time is used); a line that moved
leaves the bet without a same-line close (counted, CLV not computed).
A single member is recorded every tracking cycle (and per cohort definition), so it gets more looks per game than the
canonical forecast; its first qualifying row is the bet, as a bettor watching live would take it."""
from collections import defaultdict
from typing import Optional

from . import markets

THRESHOLDS = (0.02, 0.04, 0.06)
TWO_WAY = ("winner", "spread", "total")


def _decimal(american) -> Optional[float]:
    if american is None:
        return None
    a = float(american)
    return 1 + (a / 100 if a > 0 else 100 / -a) if abs(a) >= 100 else None


def _forecasts(conn, since: Optional[str]):
    # Rows of one cycle share their time: source, line and storage order make "the first qualifying forecast" fixed.
    q = ("SELECT 'canonical' AS source, game_id, market, line, p_a, p_b, generated_at, tipoff_at, rowid AS rid "
         "FROM game_forecasts WHERE status = 'forecast' AND market IN ('winner','spread','total') AND generated_at >= ? "
         "UNION ALL SELECT 'adaptive:' || definition, game_id, market, line, p_a, p_b, generated_at, tipoff_at, rowid "
         "FROM adaptive_forecasts WHERE status = 'forecast' AND market IN ('winner','spread','total') AND generated_at >= ? "
         "UNION ALL SELECT 'single:' || substr(group_id, 8), game_id, market, line, json_extract(vector, '$[0]'), "
         "json_extract(vector, '$[2]'), recorded_at, tipoff_at, observation_id FROM agreement_observations "
         "WHERE kind = 'single' AND group_id LIKE 'single|%' AND json_valid(vector) AND vector != 'null' "
         "AND market IN ('winner','spread','total') AND recorded_at >= ? "
         "ORDER BY generated_at, source, line, rid")
    return [r[:8] for r in conn.execute(q, (since or "", since or "", since or ""))]


def _quote(conn, game_id, market, line, at):
    return conn.execute("SELECT price_a, price_b, p_a_devig, p_b_devig, retrieved_at FROM market_quotes WHERE game_id = ? "
                        "AND market = ? AND line IS ? AND retrieved_at <= ? AND p_a_devig IS NOT NULL "
                        "AND price_label = 'current' "
                        "ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1", (game_id, market, line, at)).fetchone()


def bets(conn, *, thresholds=THRESHOLDS, since: Optional[str] = None) -> list[dict]:
    games = {r[0]: r for r in conn.execute("SELECT game_id, status, home_score, away_score, tipoff_at FROM games")}
    taken: set = set()
    out = []
    for source, gid, market, line, pa, pb, generated, tipoff in _forecasts(conn, since):
        if pa is None or pb is None or pa + pb <= 0 or generated >= tipoff:
            continue
        q = _quote(conn, gid, market, line, generated)
        if q is None:
            continue
        price_a, price_b, qa, qb, retrieved = q
        two = pa + pb
        edge = {"a": pa / two - qa, "b": pb / two - qb}
        for t in thresholds:
            key = (source, gid, market, t)
            side = max(edge, key=edge.get)
            if key in taken or edge[side] < t:
                continue
            taken.add(key)
            dec = _decimal(price_a if side == "a" else price_b)
            if dec is None:
                continue
            close = _quote(conn, gid, market, line, tipoff)
            g = games.get(gid)
            realised = markets.settle(market, line, status=g[1], home=g[2], away=g[3], tipoff_at=g[4],
                                      forecast_tipoff_at=tipoff) if g else "pending"
            if realised in ("a", "b", "mid"):
                profit = 0.0 if realised == "mid" else (dec - 1 if realised == side else -1.0)
            else:
                profit = None
            clv = None
            if close is not None and close[4] > retrieved:
                clv = (close[2] - qa) if side == "a" else (close[3] - qb)
            out.append({"source": source, "game_id": gid, "market": market, "line": line, "threshold": t, "side": side,
                        "edge": edge[side], "price": dec, "quote_at": retrieved, "forecast_at": generated,
                        "result": realised, "profit": profit, "clv": clv})
    return out


def report(conn, **kw) -> dict:
    """Per source, market and threshold: bets, W-L-P, voids, pending, ROI per settled bet, mean CLV and the share > 0."""
    groups: dict = defaultdict(list)
    for b in bets(conn, **kw):
        groups[(b["source"], b["market"], b["threshold"])].append(b)
        groups[(b["source"], "all", b["threshold"])].append(b)
    out: dict = {}
    for (source, market, t), rows in sorted(groups.items()):
        settled = [r for r in rows if r["profit"] is not None]
        clv = [r["clv"] for r in rows if r["clv"] is not None]
        out.setdefault(source, {}).setdefault(market, {})[f"{int(round(t * 100))}pt"] = {
            "bets": len(rows), "wins": sum(1 for r in settled if r["result"] == r["side"]),
            "losses": sum(1 for r in settled if r["result"] not in (r["side"], "mid")),
            "pushes": sum(1 for r in settled if r["result"] == "mid"),
            "voids": sum(1 for r in rows if r["result"] == "void"), "pending": sum(1 for r in rows if r["profit"] is None
                                                                                   and r["result"] != "void"),
            "roi": (sum(r["profit"] for r in settled) / len(settled)) if settled else None,
            "clv_mean": (sum(clv) / len(clv)) if clv else None, "clv_positive": (sum(c > 0 for c in clv) / len(clv)) if clv else None,
            "clv_n": len(clv)}
    return out
