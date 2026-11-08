"""Mechanical reading of the one-shot soccer test by READING_PLAN.md (incl. Amendment 1). Written before the run.
Usage: python3 -I read_test.py <test_report.json> <development_validation_report.json>"""
import json
import math
import sys

COMPETITIONS = ("eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions", "uefa.europa")

report = json.load(open(sys.argv[1]))["test"]
freqs = json.load(open(sys.argv[2]))["frequency"]["frequencies"]
overall, comps = report["overall"], report["by_competition"]


def ll_freq(c):
    obs, f = comps[c]["observed"], freqs[c]
    return -sum(obs[k] * math.log(f[k]) for k in range(3))


present = [c for c in COMPETITIONS if comps.get(c, {}).get("n", 0) > 0]
n_sum = sum(comps[c]["n"] for c in present)
out = {"overall_n": overall["n"], "sum_competition_n": n_sum}
if n_sum != overall["n"] or set(comps) - set(COMPETITIONS):
    out["verdict"] = "STOP: competition counts do not add up to overall n (F5); not interpreted"
    print(json.dumps(out, indent=1))
    sys.exit(2)
B = sum(comps[c]["n"] * ll_freq(c) for c in present) / n_sum
r1 = overall["log_loss"] < B and overall["log_loss_ci95"][1] < B
r2 = all(e <= 0.05 for e in overall["ece"])
r3 = {}
for c in COMPETITIONS:
    s = comps.get(c, {"n": 0})
    if not s.get("n"):
        r3[c] = {"n": 0, "pass": False}
        continue
    b = ll_freq(c)
    r3[c] = {"n": s["n"], "elo_ll": s["log_loss"], "elo_ci95": s["log_loss_ci95"], "freq_ll": b,
             "pass": s["log_loss"] < b and s["log_loss_ci95"][1] < b and s["n"] >= 300}
scope = [c for c in COMPETITIONS if r3[c]["pass"]] if (r1 and r2) else []
out.update({"elo_ll": overall["log_loss"], "elo_ci95": overall["log_loss_ci95"], "freq_ll_pooled": B, "R1": r1,
            "ece": overall["ece"], "R2": r2, "R3": r3, "launch_scope": scope,
            "R4_home_overprediction": overall["mean_predicted"][0] - overall["observed"][0],
            "R4_disclose": overall["mean_predicted"][0] - overall["observed"][0] > 0.03,
            "by_season": {s: {k: v[k] for k in ("n", "log_loss", "brier", "accuracy_most_likely")}
                          for s, v in report["by_season"].items()},
            "verdict": ("SUPPORTS LAUNCH (model quality only) for " + ", ".join(scope)) if scope else
                       ("DOES NOT SUPPORT LAUNCH" + ("" if (r1 and r2) else " (R1/R2 failed)") +
                        ("" if not (r1 and r2) else " (no competition passed R3)"))})
print(json.dumps(out, indent=1))
