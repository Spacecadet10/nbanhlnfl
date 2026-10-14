"""Registry of explicit exact rules `exact-rules-v1`: a chosen model subset with ONE threshold PER MEMBER, tracked
prospectively through tracking-v2 (agreement_tracking: kind 'exact'). Research only.

A rule is chosen in the threshold explorer (combination_thresholds, saved out-of-sample tables) and registered here by the
CLI (`exact-rules-register`); registration is CLI-only in this milestone. The registry file is sealed (sha256) and versioned:
registering adds a rule and a new file version, never changes or removes an earlier rule (every rule carries its own
`rule_sha256`, verified on load, and that digest is the `registry` an observation stores, so a rule's record survives later
file versions). A rule's `effective_from` is its registration time, never earlier; observations exist only for games that
start after it. Nothing here selects, recommends, promotes, trains or changes a primary forecast or a selector.

Identity: a rule IS its member -> threshold mapping; the order the members were typed in is kept in `id` and `members` but
two rules with the same mapping are the same rule (refused at registration, a registry error on load). Writes hold an
advisory lock (`<registry>.lock`, as db.store_lock) across load, validation and publication, publish by atomic replacement
and refuse when the file changed under them, so concurrent registrations never lose a rule.

Member mapping (explorer name -> pregame recording path), checked at registration, never substituted:
- core cohort members and shadow members: the research cohort records them under the same names (shadows in the paired
  expanded cohort);
- candidates: `candidate_forecasts.candidate` uses the explorer's name (no prefix), recorded live only for registry
  candidates whose optional dependency group is tracked and installed (agreement_tracking.candidates_for) ON THE REGISTERING
  MACHINE; the group a candidate needs is recorded in the rule's provenance, but availability on the release checkout is
  NOT verified here (unresolved: a candidate the live checkout cannot forecast stays `incomplete`, visibly, never substituted);
- adaptive-source members (`...@adaptive`, `cand:` prefix) have no pregame equivalent in the tracking inputs: refused;
- goalie candidates (`_goalie`): the explorer's historical evidence used the actual starter (postgame input): refused;
- a single-member rule is a tracking-v2 `single` group already: refused here.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import sports

STUDY = "exact-rules-v1"
REGISTRY_PATH = Path(__file__).parent / "models" / "exact_rules.json"
KIND = "exact"
EPS = 1e-9
LOCK_WAIT_SECONDS = 30.0
STATISTIC = ("minimum member confidence for the agreed side, each member's probability of that side among the market's "
             "allowed sides (as the explorer defines confidence); descriptive only, not an ensemble win probability")


class RegistryError(ValueError):
    pass


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def canonical(ts) -> bool:
    """Exactly YYYY-MM-DDTHH:MM:SSZ. strptime alone also accepts unpadded fields ('2026-10-1T9:00:00Z'), which compare
    wrongly as strings everywhere times are compared (registration, effective_from, game starts)."""
    try:
        return isinstance(ts, str) and _fmt(_parse(ts)) == ts
    except ValueError:
        return False


def utc_now() -> str:
    return _fmt(datetime.now(timezone.utc))


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def rule_identity(rule: dict) -> dict:
    """The immutable part of a rule: what an observation is recorded against."""
    return {"study": STUDY, "sport": rule["sport"], "market": rule["market"], "members": list(rule["members"]),
            "thresholds": [float(t) for t in rule["thresholds"]], "cohort": rule["cohort"],
            "effective_from": rule["effective_from"]}


def rule_sha256(rule: dict) -> str:
    return _digest(rule_identity(rule))


def rule_id(sport: str, market: str, members: list, thresholds: list) -> str:
    return f"{KIND}|{sport}:{market}|" + "+".join(f"{m}@{t:g}" for m, t in zip(members, thresholds))


def mapping(members: list, thresholds: list) -> dict:
    """member -> threshold: the rule's identity, independent of the order the members were given in."""
    return {m: float(t) for m, t in zip(members, thresholds)}


def same_rule(members_a: list, thresholds_a: list, members_b: list, thresholds_b: list) -> bool:
    a, b = mapping(members_a, thresholds_a), mapping(members_b, thresholds_b)
    return set(a) == set(b) and all(abs(a[m] - b[m]) <= EPS for m in a)


def seal(reg: dict) -> dict:
    body = {k: v for k, v in reg.items() if k != "sha256"}
    return {**body, "sha256": _digest(body)}


def verify(reg: dict) -> dict:
    """The sealed file and every rule's own digest must match; a changed, removed or repeated rule is a registry error."""
    try:
        if seal(reg)["sha256"] != reg.get("sha256"):
            raise RegistryError("exact-rule registry does not match its seal")
        if reg.get("study") != STUDY:
            raise RegistryError(f"not an {STUDY} registry")
        ids = [r["id"] for r in reg["rules"]]
        if len(ids) != len(set(ids)):
            raise RegistryError("exact-rule registry holds a duplicated rule id")
        for i, r in enumerate(reg["rules"]):
            if rule_sha256(r) != r.get("rule_sha256"):
                raise RegistryError(f"rule {r.get('id')} does not match its own digest")
            if not canonical(r["registered_at"]) or not canonical(r["effective_from"]) \
                    or _parse(r["effective_from"]) < _parse(r["registered_at"]):
                raise RegistryError(f"rule {r.get('id')} has a non-canonical or backdated registration/effective time")
            if r["id"] != rule_id(r["sport"], r["market"], r["members"], r["thresholds"]):
                raise RegistryError(f"rule {r.get('id')} id does not match its members and thresholds")
            for q in reg["rules"][:i]:
                if (q["sport"], q["market"]) == (r["sport"], r["market"]) and \
                        same_rule(q["members"], q["thresholds"], r["members"], r["thresholds"]):
                    raise RegistryError(f"rules {q['id']} and {r['id']} have the same member thresholds: one rule, not two")
    except (KeyError, TypeError, AttributeError) as exc:
        raise RegistryError(f"exact-rule registry is malformed ({type(exc).__name__}: {exc})") from None
    return reg


def load_registry(path: Optional[Path] = None) -> Optional[dict]:
    """The verified registry, or None when no rule was ever registered (the file does not exist). Any unreadable, invalid
    or tampered file is a RegistryError naming the file: never 'no rules registered'."""
    p = path or REGISTRY_PATH
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        raise RegistryError(f"{p.name} cannot be read as JSON ({type(exc).__name__}: {exc})") from None
    if not isinstance(raw, dict):
        raise RegistryError(f"{p.name} is not an {STUDY} registry")
    try:
        return verify(raw)
    except RegistryError as exc:
        raise RegistryError(f"{p.name}: {exc}") from None


def rules_for(reg: Optional[dict], sport: str, market: str) -> list:
    if not reg:
        return []
    return [r for r in reg["rules"] if r["sport"] == sport and r["market"] == market]


# --- Mapping to the pregame recording path -----------------------------------------------------------------------------
def pregame_sources(sport: str, market: str) -> dict:
    """member -> where the two-hour cycle records it (the only names an exact rule may use)."""
    from . import agreement_tracking as at
    core, shadow, _ = at.cohort_members(sport, market)
    out = {m: "core cohort member" for m in core}
    out.update({m: "shadow member (paired expanded cohort)" for m in shadow})
    out.update({m: "candidate forecast (candidate_forecasts, same cohort and exact line)"
                for m in at.candidates_for(sport, market, live_ok=True)})
    return out


def candidate_requirements(sport: str, members: list) -> dict:
    """member -> optional dependency group a candidate needs (None when none): recorded, not verified for the release
    environment (the live checkout may not install it; such a member is then `incomplete` in every observation)."""
    from .candidates import registry
    specs = {s.name: s for s in registry.SPECS if s.sport == sport}
    return {m: specs[m].extra or None for m in members if m in specs}


def grid(market: str) -> tuple:
    from . import combination_thresholds as ct
    return ct._grid(market)


def validate(sport: str, market: str, members: list, thresholds: list, source: str = "pool",
             existing: Optional[dict] = None) -> dict:
    """Refuses, with the exact reason, every member or threshold that has no faithful pregame equivalent. Returns the
    mapping and the cohort the rule reads from."""
    if sport not in ("nba", "nhl"):
        raise RegistryError(f"unknown sport {sport!r}")
    if market not in sports.get(sport).markets:
        raise RegistryError(f"{market!r} is not a {sport.upper()} market")
    if source != "pool":
        raise RegistryError(f"source {source!r} is not supported: only the candidate-pool tables (source 'pool') have "
                            "pregame equivalents; adaptive-definition members are recorded by adaptive-v1, not by the "
                            "tracking input path")
    if len(members) < 2:
        raise RegistryError("an exact rule needs at least two members; a single model is already a tracking-v2 group")
    if len(set(members)) != len(members):
        raise RegistryError("rule members must be distinct")
    if len(thresholds) != len(members):
        raise RegistryError("one threshold per member")
    known = pregame_sources(sport, market)
    for m in members:
        if "@adaptive" in m or m.startswith("cand:") or m.startswith("adaptive"):
            raise RegistryError(f"{m}: adaptive-source members have no pregame equivalent in the tracking inputs")
        if "_goalie" in m:
            raise RegistryError(f"{m}: the explorer's historical evidence for goalie candidates used the actual starting "
                                "goalie (a postgame input); not registrable")
        if m not in known:
            raise RegistryError(f"{m}: not recorded in the pregame path for {sport}:{market} (not a cohort member, shadow or "
                                "live-recorded candidate on this machine); no substitution is made")
    g = grid(market)
    for m, t in zip(members, thresholds):
        if not any(abs(float(t) - x) <= EPS for x in g):
            raise RegistryError(f"{m}@{t}: threshold is not on the explorer grid {[round(x, 4) for x in g]}")
    for r in rules_for(existing, sport, market):
        if same_rule(r["members"], r["thresholds"], members, thresholds):
            raise RegistryError(f"rule already registered as {r['id']} (version {r['registered_version']}); the same member "
                                "thresholds in another order are the same rule")
    from . import agreement_tracking as at
    _, shadow, _ = at.cohort_members(sport, market)
    return {"sources": {m: known[m] for m in members}, "cohort": "expanded" if set(members) & set(shadow) else "core",
            "candidate_requirements": candidate_requirements(sport, members)}


def explorer_provenance(path: Path, members: list, thresholds: list, sport: str, market: str) -> dict:
    """The rule's row in a saved `subsets.json` (frozen lists) of the explorer: which list, rank and both period records.
    Matched by member -> threshold mapping (member order is immaterial). A rule absent from the file, or a file saved for
    another sport, market or forecast source than the registration (source 'pool'), is refused rather than registered
    with invented provenance."""
    raw = path.read_bytes()
    d = json.loads(raw)
    saved = (d.get("sport"), d.get("market"), d.get("source"))
    if saved != (sport, market, "pool"):
        raise RegistryError(f"evidence file {path} was saved for sport/market/source {saved}, not ({sport!r}, {market!r}, "
                            "'pool'); it is not evidence for this rule")
    for key, name in (("frozen", "reliable"), ("frozen_lift", "lift")):
        for i, f in enumerate(d.get(key) or [], 1):
            if same_rule(f["members"], f["thresholds"], members, thresholds):
                return {"file": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "list": name, "rank": i,
                        "sport": d.get("sport"), "market": d.get("market"), "source": d.get("source"),
                        "periods": d.get("periods"), "sources": d.get("sources"),
                        "earlier": f["earlier"]["record"], "later": f["later"]["record"],
                        "later_pick_set": f.get("later_pick_set")}
    raise RegistryError(f"rule {rule_id('?', '?', members, thresholds)} is not in a frozen list of {path}")


# --- Writing: one writer at a time, atomic publication, no lost update ---------------------------------------------
class registry_lock:
    """Advisory lock on `<registry>.lock` (flock, as db.store_lock) held across load, validation and publication."""

    def __init__(self, path: Path, wait_seconds: float = LOCK_WAIT_SECONDS, poll_seconds: float = 0.1):
        self.path, self.wait, self.poll, self.fd = path.with_name(path.name + ".lock"), wait_seconds, poll_seconds, None

    def __enter__(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fd = fd
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise RegistryError(f"another registration holds the exact-rule registry lock ({self.path.name}); "
                                        "nothing was written, try again") from None
                time.sleep(self.poll)

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        return False


def _file_sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text()).get("sha256")
    except (OSError, ValueError, AttributeError):
        return "unreadable"


def _write_atomic(path: Path, reg: dict, expected_previous: Optional[str]) -> None:
    """Publishes `reg` by writing a temporary file beside the registry and replacing it in one step. The file must still
    hold the version the new one was built on; otherwise nothing is replaced (a writer that skipped the lock)."""
    current = _file_sha256(path)
    if current != expected_previous:
        raise RegistryError(f"{path.name} changed during registration (found {current}, built on {expected_previous}); "
                            "nothing was written, register again")
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w") as f:
            f.write(json.dumps(reg, indent=1, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def register(sport: str, market: str, spec: str, *, evidence: Optional[Path] = None, note: str = "",
             effective_from: Optional[str] = None, now: Optional[str] = None, path: Optional[Path] = None,
             write: bool = True, wait_seconds: float = LOCK_WAIT_SECONDS) -> dict:
    """Validates and appends one rule as a new sealed file version under the registry lock. `effective_from` defaults to
    now and may never be earlier than now (no backdating); `now` is only for tests."""
    from . import combination_thresholds as ct
    p = path or REGISTRY_PATH
    with registry_lock(p, wait_seconds):
        existing = load_registry(p)
        at_ = now or utc_now()
        eff = effective_from or at_
        if not canonical(eff) or not canonical(at_):
            raise RegistryError("effective_from must be an ISO UTC time exactly like 2026-10-11T18:00:00Z (zero-padded)")
        if _parse(eff) < _parse(at_):
            raise RegistryError(f"effective_from {eff} is before the registration time {at_}: a rule is never backdated")
        try:
            members, thresholds = ct._parse_rule(spec, list(pregame_sources(sport, market)) + _unknown_names(spec))
        except ValueError as exc:
            raise RegistryError(str(exc)) from None
        # A threshold typed as printed (rule ids and error messages round, e.g. 1/3 -> 0.333333 or 0.3333) is the grid
        # value it names; anything farther than half the printed precision is still refused by validate().
        thresholds = [next((x for x in grid(market) if abs(float(t) - x) <= 5e-5), t) for t in thresholds]
        mapping_ = validate(sport, market, members, thresholds, "pool", existing)
        if evidence is None and not note.strip():
            raise RegistryError("provenance needed: --evidence <subsets.json of the explorer> and/or --note")
        prov = {"source": "pool", "explorer": (explorer_provenance(Path(evidence), members, thresholds, sport, market)
                                               if evidence else None),
                "note": note.strip() or None, "members": mapping_["sources"],
                "candidate_requirements": mapping_["candidate_requirements"],
                "environment": "members checked against the pregame path and the optional groups installed on the "
                               "registering machine only; the release checkout is not verified"}
        version = (existing["version"] + 1) if existing else 1
        rule = {"id": rule_id(sport, market, members, thresholds), "study": STUDY, "sport": sport, "market": market,
                "members": members, "thresholds": [float(t) for t in thresholds], "cohort": mapping_["cohort"],
                "registered_at": at_, "registered_version": version, "effective_from": eff, "provenance": prov,
                "statistic": STATISTIC}
        rule["rule_sha256"] = rule_sha256(rule)
        history = list(existing["history"]) if existing else []
        history.append({"version": version, "registered_at": at_, "added": [rule["id"]],
                        "previous_sha256": existing["sha256"] if existing else None})
        reg = seal({"study": STUDY, "version": version, "updated_at": at_, "kind": KIND, "statistic": STATISTIC,
                    "rules": [*(existing["rules"] if existing else []), rule], "history": history})
        verify(reg)
        if write:
            _write_atomic(p, reg, existing["sha256"] if existing else None)
    return {"path": str(p), "version": version, "sha256": reg["sha256"], "rule": rule, "written": write}


def _unknown_names(spec: str) -> list:
    """Names in the spec that are not pregame sources, so `_parse_rule` reports distinct/number errors first and `validate`
    then names the unsupported member with its reason."""
    return [part.rpartition("@")[0] for part in spec.split(",") if part]


def summary(reg: Optional[dict]) -> dict:
    if not reg:
        return {"study": STUDY, "version": 0, "rules": [], "note": f"no exact rule registered ({REGISTRY_PATH.name} absent)"}
    return {"study": STUDY, "version": reg["version"], "sha256": reg["sha256"], "updated_at": reg["updated_at"],
            "rules": [{k: r[k] for k in ("id", "sport", "market", "members", "thresholds", "cohort", "registered_at",
                                        "registered_version", "effective_from", "rule_sha256")} for r in reg["rules"]],
            "history": reg["history"]}
