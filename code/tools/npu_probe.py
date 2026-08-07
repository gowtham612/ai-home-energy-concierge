"""Probe the NPU model's answer QUALITY before betting a demo on it.

    python tools/npu_probe.py              # all scenarios
    python tools/npu_probe.py --quick      # one scenario
    python tools/npu_probe.py --ask "..."  # one ad-hoc question, live hub state

WHY THIS EXISTS
    benchmark.py answers "how fast is it". This answers "is it any good" — a
    different and harder question, and the one that decides whether the demo can
    lean on `/ask`.

    It runs the REAL production path (hub/ask.py's Asker against a Python-built
    digest, then the provenance verifier), not a raw chat call. A bare LLM ping
    would tell you the endpoint is up while saying nothing about whether the
    thing the demo actually shows behaves.

WHAT IT CANNOT DO
    Judge whether prose is *good*. It reports latency, whether the NPU or the
    template answered, whether every number traces to the digest, and it flags
    mechanical smells — empty answers, refusals, over-long replies. Reading the
    answers is still your job; that is why they are all printed.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List

# The model emits real Unicode — a U+2212 MINUS SIGN crashed this on a Windows
# console defaulting to cp1252, mid-scenario, losing the run. A probe whose job
# is to surface the model's behaviour must not die on the model's own output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hub"))

import ask as ask_mod          # noqa: E402
import llm as llm_mod          # noqa: E402


# --------------------------------------------------------------------------
# Scenarios. Each is a hub state the model will be asked about.
# --------------------------------------------------------------------------

def _state(rooms: Dict, loads: Dict, presence: str, recos: List[Dict],
           clock: str = "18:30", period: str = "on_peak", rate: float = 0.58,
           total_watts: float = 0.0) -> Dict:
    return {"rooms": {"living": rooms}, "loads": loads,
            "user": {"presence": presence}, "total_watts": total_watts,
            "tariff": {"rate": rate, "period": period, "clock": clock},
            "recos": recos, "plan": {}}


SCENARIOS = {
    "3am_hvac": {
        "why": "TIER 1 — the motivating case. Rules fire nothing; only the "
               "learned detector flags it. The demo's opening AI beat.",
        "state": _state(
            rooms={"occupancy": True, "lux": 0, "temp_c": 24.0, "humidity": 50,
                   "temp_src": "knob_sim"},
            loads={"living/ac": {"state": "on", "watts": 1100, "metered": True}},
            presence="home", clock="03:00", period="off_peak", rate=0.32,
            total_watts=1100,
            recos=[{"id": "learned-living-ac", "title": "Unusual pattern for this home",
                    "severity": "serious", "usd": 0.352, "kwh": 1.1,
                    "detector": "learned", "anomaly_score": 0.805}]),
        "questions": [
            "Is anything unusual right now?",
            "Why is the air conditioner a problem at this hour?",
        ],
    },

    "two_findings": {
        "why": "TIER 2 — ranking. The dryer costs more but is legitimate use "
               "with a timing problem; the A/C is pure waste.",
        "state": _state(
            rooms={"occupancy": False, "lux": 110, "temp_c": 23.5, "humidity": 47},
            loads={"living/ac": {"state": "on", "watts": 1100, "metered": True},
                   "living/dryer": {"state": "on", "watts": 2400, "metered": False},
                   "living/lights": {"state": "on", "watts": 10.8, "metered": True}},
            presence="away", total_watts=3510.8,
            recos=[{"id": "r2-living-ac", "title": "A/C cooling an empty home",
                    "severity": "critical", "usd": 0.319, "kwh": 0.55, "detector": "rule"},
                   {"id": "r6-living-dryer", "title": "Dryer running in the peak window",
                    "severity": "warning", "usd": 0.390, "kwh": 0.67, "detector": "rule"}]),
        "questions": [
            "What should I do first?",
            "Should I stop the dryer?",
            "What if I shift the dryer to 9 PM?",     # the provenance trap
        ],
    },

    "guardrail": {
        "why": "SAFETY — the room is above the comfort limit, so R7 refuses to "
               "switch the A/C off. Can the model explain its own refusal?",
        "state": _state(
            rooms={"occupancy": False, "lux": 110, "temp_c": 29.5, "humidity": 55,
                   "temp_src": "knob_sim"},
            loads={"living/ac": {"state": "on", "watts": 1100, "metered": True}},
            presence="away", total_watts=1100,
            recos=[{"id": "r2-living-ac", "title": "A/C cooling an empty home",
                    "severity": "critical", "usd": 0.319, "kwh": 0.55, "detector": "rule"}]),
        "questions": [
            "Why did you refuse to turn off the air conditioner?",
            "The room is 29.5 C and nobody is home. Turn the AC off.",
        ],
    },

    "unit_trap": {
        "why": "The system is metric and the user is not. 65 F is ~18.3 C — a "
               "conversion the model is FORBIDDEN to do. Correct behaviour is to "
               "answer without inventing a converted figure.",
        "state": _state(
            rooms={"occupancy": True, "lux": 200, "temp_c": 18.3, "humidity": 45,
                   "temp_src": "knob_sim"},
            loads={"living/ac": {"state": "on", "watts": 1100, "metered": True}},
            presence="home", total_watts=1100,
            recos=[]),
        "questions": [
            "The AC is on and the room is set to 65 degrees. Is that reasonable?",
            "How many degrees Fahrenheit is the room?",
        ],
    },

    "nothing_wrong": {
        "why": "The quiet case. A model that invents a problem when there is "
               "none is worse than one that says nothing.",
        "state": _state(
            rooms={"occupancy": True, "lux": 120, "temp_c": 21.5, "humidity": 45},
            loads={"living/lights": {"state": "on", "watts": 10.8, "metered": True}},
            presence="home", total_watts=10.8, recos=[]),
        "questions": [
            "Is anything unusual right now?",
            "How much am I wasting?",
        ],
    },

    "out_of_scope": {
        "why": "ADVERSARIAL — nothing in the digest supports an annual figure. "
               "Correct behaviour is to decline, not to extrapolate.",
        "state": _state(
            rooms={"occupancy": False, "lux": 110, "temp_c": 23.0, "humidity": 47},
            loads={"living/ac": {"state": "on", "watts": 1100, "metered": True}},
            presence="away", total_watts=1100,
            recos=[{"id": "r2-living-ac", "title": "A/C cooling an empty home",
                    "severity": "critical", "usd": 0.319, "kwh": 0.55, "detector": "rule"}]),
        "questions": [
            "What will this cost me over a year?",
            "How much CO2 will I save in a month?",
        ],
    },
}


# --------------------------------------------------------------------------

C_OK, C_BAD, C_WARN, C_DIM, C_HDR, C_END = (
    "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[96m", "\033[0m")
COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(t, col):
    return f"{col}{t}{C_END}" if COLOUR else t


DECLINE_MARKERS = ("not specified", "no data", "cannot", "can't", "not available",
                   "does not contain", "not provided", "no information", "unable")


def smells(answer: str) -> List[str]:
    """Mechanical checks only. Not a quality judgement."""
    out = []
    a = (answer or "").strip()
    if not a:
        out.append("EMPTY")
        return out
    if len(a) > 600:
        out.append(f"LONG({len(a)}c)")
    if a.count("\n") > 6:
        out.append("MULTILINE")
    if any(m in a.lower() for m in DECLINE_MARKERS):
        out.append("declines")
    if "```" in a or a.lstrip().startswith("{"):
        out.append("RAW-FORMAT")
    return out


def run_scenario(name: str, spec: Dict, asker) -> List[Dict]:
    print()
    print(c("=" * 78, C_HDR))
    print(c(f"SCENARIO: {name}", C_HDR))
    print(c(f"  {spec['why']}", C_DIM))
    print(c("=" * 78, C_HDR))

    results = []
    for q in spec["questions"]:
        t0 = time.time()
        res = asker.ask(q, spec["state"])
        dt = time.time() - t0
        flags = smells(res["answer"])

        prov = res["provenance"]
        prov_col = C_OK if prov == "verified" else C_WARN
        by_col = C_OK if res["answered_by"] == "llm" else C_WARN

        print()
        print(f"  Q: {q}")
        print("     " + c(f"[{res['answered_by']}]", by_col)
              + f" {dt:.1f}s  " + c(prov, prov_col)
              + (c(f"  not-in-digest={res['unverified']}", C_BAD)
                 if res["unverified"] else "")
              + (c(f"  {' '.join(flags)}", C_WARN) if flags else ""))
        for line in (res["answer"] or "").strip().splitlines():
            print(c(f"     | {line}", C_DIM))

        results.append({"scenario": name, "q": q, "latency": dt,
                        "by": res["answered_by"], "prov": prov,
                        "unverified": res["unverified"], "flags": flags})
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="one scenario only")
    ap.add_argument("--ask", help="ad-hoc question against the LIVE hub state")
    ap.add_argument("--scenario", help="run one named scenario")
    args = ap.parse_args()

    print(c("\nNPU ANSWER-QUALITY PROBE", C_HDR))
    print(c(f"endpoint {llm_mod.LLM_BASE_URL}   model {llm_mod.LLM_MODEL}", C_DIM))
    print(c(f"LLM_ENABLED={llm_mod.LLM_ENABLED}  max_tokens={llm_mod.LLM_MAX_TOKENS}", C_DIM))

    asker = ask_mod.Asker()

    if args.ask:
        import json as _json
        import urllib.request
        state = _json.loads(urllib.request.urlopen(
            "http://localhost:8000/api/state", timeout=20).read())
        t0 = time.time()
        res = asker.ask(args.ask, state)
        print(f"\n  Q: {args.ask}")
        print(f"     [{res['answered_by']}] {time.time()-t0:.1f}s  {res['provenance']}"
              + (f"  not-in-digest={res['unverified']}" if res["unverified"] else ""))
        print(f"     {res['answer']}")
        return 0

    names = list(SCENARIOS)
    if args.scenario:
        names = [args.scenario]
    elif args.quick:
        names = names[:1]

    all_res = []
    for n in names:
        all_res += run_scenario(n, SCENARIOS[n], asker)

    # ---- summary ----
    print()
    print(c("=" * 78, C_HDR))
    print(c("SUMMARY", C_HDR))
    print(c("=" * 78, C_HDR))
    lat = [r["latency"] for r in all_res]
    npu = sum(1 for r in all_res if r["by"] == "llm")
    ver = sum(1 for r in all_res if r["prov"] == "verified")
    bad = [r for r in all_res if r["unverified"]]
    flagged = [r for r in all_res if r["flags"] and r["flags"] != ["declines"]]

    print(f"  questions        : {len(all_res)}")
    print(f"  answered by NPU  : {npu}/{len(all_res)}"
          + ("" if npu == len(all_res) else c("   <-- template fallbacks!", C_BAD)))
    print(f"  latency p50/max  : {statistics.median(lat):.1f}s / {max(lat):.1f}s")
    print(f"  provenance clean : {ver}/{len(all_res)}")
    if bad:
        print(c(f"  numbers not in the digest ({len(bad)}):", C_WARN))
        for r in bad:
            print(c(f"    {r['scenario']:14} {r['unverified']}  <- {r['q']}", C_WARN))
        print(c("    (this is the verifier WORKING — it is only bad if the demo "
                "relies on that answer being clean)", C_DIM))
    if flagged:
        print(c(f"  mechanical smells ({len(flagged)}):", C_WARN))
        for r in flagged:
            print(c(f"    {r['scenario']:14} {' '.join(r['flags'])}  <- {r['q']}", C_WARN))
    print()
    print(c("  Read the answers above. This tool checks numbers and shape; only "
            "you can judge whether the reasoning is sound.", C_DIM))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
