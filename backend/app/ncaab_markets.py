"""NCAAB market specs, registered into `markets.SPEC` at import (docs/NCAAB_PLAN.md "Store and identity").

`markets.py` is hashed into the NFL activation, so it is never edited; this module only adds NCAAB entries to its
`SPEC` dict and its `LIMITATIONS` dict. Settlement is `markets.settle`, unchanged: overtime counts for all five markets
(the full-game score), half-point lines win or lose, whole-number lines can push, a tied final score voids the winner
market, and postponed or canceled games void.
"""
from . import markets

OVERTIME = "overtime counts; winner ties void"

SPECS = (
    markets.MarketSpec("ncaab", "winner", "Winner", OVERTIME, None),
    markets.MarketSpec("ncaab", "spread", "Point spread", OVERTIME, 0.5),
    markets.MarketSpec("ncaab", "total", "Game total points", OVERTIME, 0.5),
    markets.MarketSpec("ncaab", "team_total_home", "Home team points", OVERTIME, 0.5),
    markets.MarketSpec("ncaab", "team_total_away", "Away team points", OVERTIME, 0.5),
)

LIMITATION = "No injury, line-up or travel information is used."
RESEARCH_NOTE = ("Research only: the NCAAB model has no live record yet, so no pick rule or threshold is claimed for it.")

markets.SPEC.update({(s.sport, s.market): s for s in SPECS})
markets.LIMITATIONS.setdefault("ncaab", [LIMITATION])
