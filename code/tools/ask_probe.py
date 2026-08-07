"""Probe /ask on the history branch: the REAL demo questions first, then adversarial.

Set A is what actually gets asked on camera -- SUGGESTED_QUESTIONS from ask.py
(the buttons on the /ask page) plus the questions demo_autopilot.py types during
a run. The first probe of this branch missed all but one of these, which is the
whole reason this file exists.

Set B is the adversarial set that found the original defects; it is kept so the
fixes can be shown to hold.

`check` is a substring that MUST appear for the answer to be right, or a
callable. `forbid` is text that must NOT appear.
"""
import json
import os
import sys
import time

sys.path.insert(0, "hub")
os.environ.setdefault("AI_ASK", "1")

import ask as ask_mod  # noqa: E402

# --- Set A: what the demo actually asks -----------------------------------
DEMO = [
    ("A/ask-page", "Why is my bill high?", None, None),
    ("A/ask-page", "What should I do first?", None, None),
    ("A/ask-page", "What if I shift the dryer to 9 PM?", None, None),
    ("A/ask-page", "Is anything unusual right now?", None, None),
    ("A/ask-page", "How does today compare to my usual month?", "157.12", None),
    ("A/autopilot", "Why did you refuse to turn off the air conditioner?", None, "above 27"),
    ("A/autopilot", "What is the combined cost of all the findings?", None, None),
]

# --- Set B: adversarial, targeting the three defects ----------------------
ADVERSARIAL = [
    ("B/hvac-alias", "How much electricity did my air conditioner use over the past month?",
     "157.12", "0.55"),
    ("B/hvac-alias", "How much did my AC use last month?", "157.12", "0.55"),
    ("B/timeframe", "What did my HVAC cost me historically?", "72.99", "per day"),
    ("B/tautology", "Am I charging my car at the right time of day?", None, "100"),
    ("B/tautology", "What am I already doing well?", None, "100.0%"),
    ("B/live-guard", "What is my usage right now?", "3500", "558"),
    ("B/live-guard", "How many watts am I drawing at this moment?", "3500", None),
    ("B/out-of-scope", "How much did my dishwasher cost me last month?", None, None),
]


def run(label, cases, state):
    rows = []
    print("\n" + "=" * 104)
    print(f"  {label}")
    print("=" * 104, flush=True)
    for cat, q, check, forbid in cases:
        t0 = time.time()
        try:
            out = ask_mod.ASKER.ask(q, state)
        except Exception as exc:
            out = {"answer": f"<EXCEPTION {exc}>", "provenance": "?", "unverified": []}
        dt = time.time() - t0
        ans = (out.get("answer") or "").replace("\n", " ")

        verdict = []
        if check and check not in ans:
            verdict.append(f"MISSING '{check}'")
        if forbid and forbid.lower() in ans.lower():
            verdict.append(f"CONTAINS '{forbid}'")
        status = "FAIL: " + "; ".join(verdict) if verdict else "ok"

        print("-" * 104)
        print(f"[{cat}] {q}")
        print(f"  -> {ans[:300]}")
        print(f"  {status}   provenance={out.get('provenance')} "
              f"unverified={out.get('unverified')}  {dt:.1f}s")
        sys.stdout.flush()
        rows.append({"cat": cat, "q": q, "answer": ans, "status": status,
                     "provenance": out.get("provenance"), "secs": round(dt, 1)})
    return rows


def main():
    state = ask_mod._demo_state()
    rows = run("SET A - THE ACTUAL DEMO QUESTIONS", DEMO, state)
    rows += run("SET B - ADVERSARIAL", ADVERSARIAL, state)

    fails = [r for r in rows if r["status"] != "ok"]
    print("\n" + "=" * 104)
    print(f"  {len(rows)} questions asked, {len(fails)} failed")
    for f in fails:
        print(f"    FAIL  [{f['cat']}] {f['q']}  -- {f['status']}")
    with open("ask_probe2_results.json", "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)
    print("PROBE-COMPLETE")


if __name__ == "__main__":
    main()
