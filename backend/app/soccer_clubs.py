"""Explicit, versioned club identity for the openfootball soccer history (app/soccer_history.py).

A club is '<country code>:<canonical name>'. A source name maps to a club in exactly one of two ways:
1. it is listed in ALIASES (written by hand after reading every name in the pinned files; the reason is the comment), or
2. it is used as written (exact string, no normalisation).
Nothing is matched by similarity. `possible_duplicates` uses a crude normalisation only to FLAG pairs that may be one club
under two names; each flagged pair must then be listed in ALIASES or DISTINCT, and the coverage report lists any that are
neither (tests/test_soccer_history.py checks the pinned dataset has none).

Canonical name = the most recent spelling in the source. Domestic league names carry no country: the league's country is
used. UEFA names carry a '(XXX)' suffix, which is the country used (Monaco's 'MCO' is mapped to its Ligue 1 identity).
Changing any entry requires a new MAP_VERSION; imported rows record the version that resolved them.
"""
from __future__ import annotations

import re
import unicodedata

MAP_VERSION = "2026-11-08.1"

LEAGUE_COUNTRY = {"eng.1": "ENG", "esp.1": "ESP", "ger.1": "GER", "ita.1": "ITA", "fra.1": "FRA"}

# (country, name as written) -> (country, canonical name). Same club, renamed in the source (mostly the 2020-21 switch
# to full official names in football.json, and the 2023-24+ UEFA files).
ALIASES: dict[tuple[str, str], tuple[str, str]] = {
    # England
    ("ENG", "Aston Villa"): ("ENG", "Aston Villa FC"),
    ("ENG", "Brighton & Hove Albion"): ("ENG", "Brighton & Hove Albion FC"),
    ("ENG", "Crystal Palace"): ("ENG", "Crystal Palace FC"),
    ("ENG", "Hull City"): ("ENG", "Hull City AFC"),
    ("ENG", "Leicester City"): ("ENG", "Leicester City FC"),
    ("ENG", "Manchester City"): ("ENG", "Manchester City FC"),
    ("ENG", "Manchester United"): ("ENG", "Manchester United FC"),
    ("ENG", "Newcastle United"): ("ENG", "Newcastle United FC"),
    ("ENG", "Norwich City"): ("ENG", "Norwich City FC"),
    ("ENG", "Sheffield United"): ("ENG", "Sheffield United FC"),
    ("ENG", "Tottenham Hotspur"): ("ENG", "Tottenham Hotspur FC"),
    ("ENG", "West Bromwich Albion"): ("ENG", "West Bromwich Albion FC"),
    ("ENG", "West Ham United"): ("ENG", "West Ham United FC"),
    ("ENG", "Wolverhampton Wanderers"): ("ENG", "Wolverhampton Wanderers FC"),
    # Spain
    ("ESP", "Atlético Madrid"): ("ESP", "Club Atlético de Madrid"),
    ("ESP", "CD Alavés"): ("ESP", "Deportivo Alavés"),
    ("ESP", "Deportivo La Coruña"): ("ESP", "RC Deportivo La Coruña"),
    ("ESP", "Espanyol Barcelona"): ("ESP", "RCD Espanyol de Barcelona"),
    ("ESP", "RC Celta"): ("ESP", "RC Celta de Vigo"),
    ("ESP", "Rayo Vallecano"): ("ESP", "Rayo Vallecano de Madrid"),
    ("ESP", "Real Betis"): ("ESP", "Real Betis Balompié"),
    ("ESP", "Real Madrid"): ("ESP", "Real Madrid CF"),
    ("ESP", "Real Sociedad"): ("ESP", "Real Sociedad de Fútbol"),
    ("ESP", "Real Valladolid"): ("ESP", "Real Valladolid CF"),
    # Germany
    ("GER", "1899 Hoffenheim"): ("GER", "TSG 1899 Hoffenheim"),
    ("GER", "Bayer Leverkusen"): ("GER", "Bayer 04 Leverkusen"),
    ("GER", "Bayern München"): ("GER", "FC Bayern München"),
    ("GER", "Bor. Mönchengladbach"): ("GER", "Borussia Mönchengladbach"),
    ("GER", "FC St. Pauli"): ("GER", "FC St. Pauli 1910"),
    ("GER", "SpVgg Greuther Fürth"): ("GER", "SpVgg Greuther Fürth 1903"),
    ("GER", "Werder Bremen"): ("GER", "SV Werder Bremen"),
    # Italy
    ("ITA", "Atalanta"): ("ITA", "Atalanta BC"),
    ("ITA", "Bologna FC"): ("ITA", "Bologna FC 1909"),
    ("ITA", "Hellas Verona"): ("ITA", "Hellas Verona FC"),
    ("ITA", "Inter"): ("ITA", "FC Internazionale Milano"),
    ("ITA", "Juventus"): ("ITA", "Juventus FC"),
    ("ITA", "Lazio Roma"): ("ITA", "SS Lazio"),
    ("ITA", "Sampdoria"): ("ITA", "UC Sampdoria"),
    ("ITA", "Sassuolo Calcio"): ("ITA", "US Sassuolo Calcio"),
    # France
    ("FRA", "AS Monaco"): ("FRA", "AS Monaco FC"),
    ("MCO", "AS Monaco FC"): ("FRA", "AS Monaco FC"),          # UEFA files 2024-25+ list Monaco under MCO
    ("FRA", "ESTAC Troyes"): ("FRA", "ES Troyes AC"),
    ("FRA", "Olympique Marseille"): ("FRA", "Olympique de Marseille"),
    ("FRA", "Paris Saint-Germain"): ("FRA", "Paris Saint-Germain FC"),
    ("FRA", "RC Lens"): ("FRA", "Racing Club de Lens"),
    ("FRA", "RC Strasbourg"): ("FRA", "RC Strasbourg Alsace"),
    ("FRA", "Stade Rennais"): ("FRA", "Stade Rennais FC 1901"),
    # Other countries (UEFA files only)
    ("AUT", "RB Salzburg"): ("AUT", "FC Red Bull Salzburg"),
    ("AUT", "Sturm Graz"): ("AUT", "SK Sturm Graz"),
    ("AZE", "Qarabağ FK"): ("AZE", "Qarabağ Ağdam FK"),
    ("BEL", "Union Saint-Gilloise"): ("BEL", "Royale Union Saint-Gilloise"),
    ("CRO", "Dinamo Zagreb"): ("CRO", "GNK Dinamo Zagreb"),
    ("CZE", "Slavia Praha"): ("CZE", "SK Slavia Praha"),
    ("GRE", "Olympiakos Piraeus"): ("GRE", "PAE Olympiakos SFP"),
    ("NED", "Feyenoord"): ("NED", "Feyenoord Rotterdam"),
    ("NED", "PSV Eindhoven"): ("NED", "PSV"),
    ("POR", "SL Benfica"): ("POR", "Sport Lisboa e Benfica"),
    ("POR", "Sporting Clube de Braga"): ("POR", "Sporting Braga"),
    ("POR", "Sporting CP"): ("POR", "Sporting Clube de Portugal"),
    ("ROU", "FC Steaua Bucureşti"): ("ROU", "FCSB"),             # the club that played as Steaua until 2017 (UEFA record)
    ("SRB", "Crvena Zvezda"): ("SRB", "FK Crvena Zvezda"),
    ("TUR", "Galatasaray"): ("TUR", "Galatasaray SK"),
    ("UKR", "Shakhtar Donetsk"): ("UKR", "FK Shakhtar Donetsk"),
}

# Pairs the duplicate check flags that are different clubs (kept apart on purpose).
DISTINCT: frozenset[frozenset[str]] = frozenset({
    frozenset({"ITA:Parma FC", "ITA:Parma Calcio 1913"}),          # Parma FC went bankrupt in 2015; refounded club
    frozenset({"FRA:AC Ajaccio", "FRA:Gazélec FC Ajaccio"}),        # two Ajaccio clubs
    frozenset({"ESP:Real Madrid CF", "ESP:Club Atlético de Madrid"}),
    frozenset({"ESP:Real Madrid CF", "ESP:Rayo Vallecano de Madrid"}),
    frozenset({"ESP:Club Atlético de Madrid", "ESP:Rayo Vallecano de Madrid"}),
    frozenset({"ESP:FC Barcelona", "ESP:RCD Espanyol de Barcelona"}),
    frozenset({"ENG:Manchester City FC", "ENG:Manchester United FC"}),
    frozenset({"ITA:AC Milan", "ITA:FC Internazionale Milano"}),
    frozenset({"ITA:AS Roma", "ITA:SS Lazio"}),
    frozenset({"FRA:Paris Saint-Germain FC", "FRA:Paris FC"}),
    frozenset({"CZE:AC Sparta Praha", "CZE:SK Slavia Praha"}),
    frozenset({"RUS:CSKA Moskva", "RUS:Spartak Moskva"}), frozenset({"RUS:CSKA Moskva", "RUS:Lokomotiv Moskva"}),
    frozenset({"RUS:Lokomotiv Moskva", "RUS:Spartak Moskva"}),
    frozenset({"BUL:CSKA Sofia", "RUS:CSKA Moskva"}),
    frozenset({"AUT:Austria Wien", "AUT:Rapid Wien"}),
    frozenset({"POR:Sporting Braga", "POR:Sporting Clube de Portugal"}),
    frozenset({"ISR:Maccabi Haifa", "ISR:Maccabi Tel Aviv"}),
    frozenset({"CYP:Omonia Nikosia", "CYP:APOEL Nikosia"}),
})

UEFA_NAME = re.compile(r"^(?P<name>.+?) \((?P<country>[A-Z]{3})\)$")


def club(country: str, name: str) -> tuple[str, str]:
    """(country, canonical name) for a name as written; exact lookup, then the name itself."""
    return ALIASES.get((country, name), (country, name))


def club_id(country: str, name: str) -> str:
    c, n = club(country, name)
    return f"{c}:{n}"


_STOP = {"fc", "afc", "cf", "sc", "ac", "as", "us", "ss", "ssc", "sk", "fk", "cd", "rc", "rcd", "ud", "sd", "vfl", "vfb",
         "sv", "tsg", "bsc", "kv", "kaa", "krc", "rsc", "pfc", "gnk", "nk", "hnk", "if", "bk", "pae", "sfp", "de", "club",
         "calcio", "cfc", "bc", "hsc", "sco", "fco", "ogc", "osc"}


def _key(name: str) -> str:
    """Crude comparison form used ONLY to flag possible duplicates for a human; never to match."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", " ", s)
    words = [w for w in s.split() if w not in _STOP and len(w) > 2]
    return " ".join(sorted(words))


def possible_duplicates(club_ids: set[str]) -> list[tuple[str, str]]:
    """Pairs of distinct clubs in one country whose names share a comparison word set or contain one another, and are
    not declared DISTINCT. Each returned pair needs a decision (ALIASES or DISTINCT). Grouped strictly by country code:
    a club listed under a second code (Monaco as MCO) is caught by `same_name_other_country` instead."""
    by_country: dict[str, list[tuple[str, str]]] = {}
    for cid in sorted(club_ids):
        country, name = cid.split(":", 1)
        by_country.setdefault(country, []).append((cid, _key(name)))
    out = []
    for items in by_country.values():
        for i, (a, ka) in enumerate(items):
            for b, kb in items[i + 1:]:
                if not ka or not kb or frozenset({a, b}) in DISTINCT:
                    continue
                wa, wb = set(ka.split()), set(kb.split())
                if wa == wb or wa <= wb or wb <= wa:
                    out.append((a, b))
    return out


def same_name_other_country(club_ids: set[str]) -> list[list[str]]:
    """Clubs whose canonical name appears under more than one country code (e.g. an unaliased 'MCO:AS Monaco FC' next
    to 'FRA:AS Monaco FC'). Each needs an explicit ALIASES entry or a documented reason."""
    by_name: dict[str, list[str]] = {}
    for cid in sorted(club_ids):
        by_name.setdefault(cid.split(":", 1)[1], []).append(cid)
    return [ids for ids in by_name.values() if len(ids) > 1]
