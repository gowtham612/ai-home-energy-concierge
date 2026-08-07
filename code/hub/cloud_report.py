"""Cloud deep report — the fourth tier, on Qualcomm AI Cloud 100.

Deliberately OFF the critical demo path: it is a button, never a dependency. The
digest is computed in Python; only the interpretation is delegated to a larger
model. If the cloud is unreachable, a deterministic report is generated from the
same digest, so the button never shows an error.

Env: CLOUD_BASE_URL, CLOUD_MODEL, CLOUD_API_KEY
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List

try:
    import requests
except ImportError:
    requests = None

from energy_model import (CO2_KG_PER_KWH, OFF_PEAK_USD_PER_KWH,
                          ON_PEAK_USD_PER_KWH,
                          SUPER_OFF_PEAK_USD_PER_KWH, annualize,
                          tariff_provenance)

CLOUD_BASE_URL = os.environ.get("CLOUD_BASE_URL", "")
CLOUD_MODEL = os.environ.get("CLOUD_MODEL", "cloud-model")
CLOUD_API_KEY = os.environ.get("CLOUD_API_KEY", "")
CLOUD_TIMEOUT_S = 30

CACHE_PATH = Path(__file__).resolve().parent / ".last_report.json"
MAX_DIGEST_CHARS = 2000

SYSTEM_PROMPT = """You are an energy analyst. You are given a statistical digest of \
one home's energy usage, already computed. Interpret it.

CRITICAL: do not recalculate anything and do not invent figures. Use the numbers \
in the digest as given.

Reply with a single JSON object, nothing else:
{"summary": "<3-4 sentences>",
 "patterns": ["<observation>", ...],
 "weekly_plan": [{"action": "<what to do>", "est_monthly_usd": <number from digest>, "effort": "low"|"med"|"high"}],
 "top_retrofit": {"action": "<one purchase>", "cost_usd": <number>, "payback_months": <number>}}"""


def build_digest(state: Dict) -> Dict:
    """Compute the statistical digest IN PYTHON. Never send raw logs to a model."""
    recos = state.get("recos", [])
    total_usd = sum(r.get("usd", 0.0) for r in recos)
    total_kwh = sum(r.get("kwh", 0.0) for r in recos)
    total_co2 = sum(r.get("co2_kg", 0.0) for r in recos)

    by_rule: Dict[str, Dict] = {}
    for r in recos:
        k = r.get("rule_name", "unknown")
        e = by_rule.setdefault(k, {"count": 0, "usd": 0.0, "kwh": 0.0})
        e["count"] += 1
        e["usd"] += r.get("usd", 0.0)
        e["kwh"] += r.get("kwh", 0.0)

    ranked = sorted(by_rule.items(), key=lambda kv: kv[1]["usd"], reverse=True)
    tariff = state.get("tariff", {})

    return {
        "window": "observed session",
        "total_wasted_usd": round(total_usd, 3),
        "total_wasted_kwh": round(total_kwh, 4),
        "total_wasted_co2_kg": round(total_co2, 4),
        "current_watts": round(state.get("total_watts", 0.0), 1),
        "tariff_now": {"rate": tariff.get("rate"), "period": tariff.get("period")},
        "on_peak_rate": ON_PEAK_USD_PER_KWH,
        "off_peak_rate": OFF_PEAK_USD_PER_KWH,
        # The cheapest tier has to be in the digest, not just the
        # other two. Without it the model cannot answer "when is the
        # cheapest time to run this?" — and if it named the right
        # figure anyway, the provenance verifier would flag a CORRECT
        # answer as invented, because the number was never given.
        "super_off_peak_rate": SUPER_OFF_PEAK_USD_PER_KWH,
        "tariff_source": tariff_provenance(),
        "co2_kg_per_kwh": CO2_KG_PER_KWH,
        "by_rule": [
            {"rule": k, "events": v["count"], "usd": round(v["usd"], 3),
             "kwh": round(v["kwh"], 4),
             "projected_monthly_usd": round(annualize(v["usd"] / max(v["count"], 1),
                                                      v["count"] * 7) / 12.0, 2)}
            for k, v in ranked
        ],
        "loads_seen": sorted({k for k in state.get("loads", {})}),
    }


def _deterministic_report(digest: Dict) -> Dict:
    """Python-generated report — the path that always works."""
    by_rule = digest.get("by_rule", [])
    top = by_rule[0] if by_rule else None

    patterns: List[str] = []
    for r in by_rule:
        nice = r["rule"].replace("_", " ")
        patterns.append(
            f"{nice}: {r['events']} event(s), ${r['usd']:.2f} observed, "
            f"about ${r['projected_monthly_usd']:.2f} per month if the pattern holds")
    if digest["tariff_now"].get("period") == "on_peak":
        patterns.append(
            f"Observed during the on-peak window at ${digest['on_peak_rate']:.2f}/kWh — "
            f"{((digest['on_peak_rate'] / digest['off_peak_rate']) - 1) * 100:.0f}% "
            f"above the off-peak rate")

    plan = []
    for r in by_rule[:3]:
        rule = r["rule"]
        if rule == "away_with_hvac_on":
            plan.append({"action": "Link the A/C to your phone's geofence so it stops when you leave",
                         "est_monthly_usd": r["projected_monthly_usd"], "effort": "med"})
        elif rule == "unoccupied_lights_on":
            plan.append({"action": "Fit motion-timer switches in the rooms used least",
                         "est_monthly_usd": r["projected_monthly_usd"], "effort": "low"})
        elif rule == "daylight_waste":
            plan.append({"action": "Reposition seating toward the window and leave the lights off by day",
                         "est_monthly_usd": r["projected_monthly_usd"], "effort": "low"})
        elif rule == "peak_hour_heavy_load":
            plan.append({"action": "Use delay-start on the dryer and dishwasher so cycles begin after 9 PM",
                         "est_monthly_usd": r["projected_monthly_usd"], "effort": "low"})
        elif rule == "phantom_standby":
            plan.append({"action": "Move idle electronics onto switched power strips",
                         "est_monthly_usd": r["projected_monthly_usd"], "effort": "low"})
        elif rule == "hvac_with_window_open":
            plan.append({"action": "Check window and door seals before running the A/C",
                         "est_monthly_usd": r["projected_monthly_usd"], "effort": "low"})

    monthly = sum(p["est_monthly_usd"] for p in plan)
    summary = (
        f"Across the observed session the system identified ${digest['total_wasted_usd']:.2f} "
        f"of avoidable cost, {digest['total_wasted_kwh']:.2f} kWh, and "
        f"{digest['total_wasted_co2_kg']:.2f} kg of CO2. "
        + (f"The largest single contributor was {top['rule'].replace('_', ' ')}, "
           f"at ${top['usd']:.2f}. " if top else "")
        + f"Acting on the plan below would save roughly ${monthly:.2f} per month. "
        f"All figures are computed from measured load power and the SDG&E time-of-use tariff."
    )

    retrofit = {"action": "Smart thermostat with geofencing (e.g. a learning thermostat)",
                "cost_usd": 130.0,
                "payback_months": round(130.0 / monthly, 1) if monthly > 0 else 0.0}

    return {"summary": summary, "patterns": patterns, "weekly_plan": plan,
            "top_retrofit": retrofit, "generated_by": "deterministic",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "digest": digest}


def _cloud_report(digest: Dict) -> Dict:
    if requests is None:
        raise RuntimeError("requests not installed")
    if not CLOUD_BASE_URL:
        raise RuntimeError("CLOUD_BASE_URL not set")

    digest_json = json.dumps(digest, indent=None)[:MAX_DIGEST_CHARS]
    headers = {"Content-Type": "application/json"}
    if CLOUD_API_KEY:
        headers["Authorization"] = f"Bearer {CLOUD_API_KEY}"

    payload = {
        "model": CLOUD_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"DIGEST:\n{digest_json}\n\nReply with the JSON object now."},
        ],
        "temperature": 0.3,
        "max_tokens": 900,
    }
    resp = requests.post(f"{CLOUD_BASE_URL.rstrip('/')}/chat/completions",
                         headers=headers, json=payload, timeout=CLOUD_TIMEOUT_S)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    parsed = json.loads(raw[start:end + 1])

    for key in ("summary", "patterns", "weekly_plan"):
        if key not in parsed:
            raise ValueError(f"cloud response missing {key!r}")

    parsed["generated_by"] = f"cloud:{CLOUD_MODEL}"
    parsed["generated_at"] = datetime.now().isoformat(timespec="seconds")
    parsed["digest"] = digest
    return parsed


def generate_report(state: Dict) -> Dict:
    """Generate a deep report. Never raises — always returns something showable."""
    digest = build_digest(state)

    try:
        report = _cloud_report(digest)
        print(f"[cloud] report generated by {report['generated_by']}")
    except Exception as exc:
        print(f"[cloud] falling back to deterministic report ({type(exc).__name__}: {exc})")
        report = _deterministic_report(digest)

    try:
        CACHE_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except Exception:
        pass
    return report


def cached_report() -> Dict:
    """Last successful report — lets us show something fully offline."""
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


if __name__ == "__main__":
    synthetic = {
        "total_watts": 1460.0,
        "tariff": {"rate": ON_PEAK_USD_PER_KWH, "period": "on_peak", "clock": "18:30"},
        "loads": {"living/lights": {}, "living/ac": {}, "living/dryer": {}, "living/standby": {}},
        "recos": [
            {"rule_name": "away_with_hvac_on", "usd": 1.28, "kwh": 2.20, "co2_kg": 0.55},
            {"rule_name": "unoccupied_lights_on", "usd": 0.14, "kwh": 0.24, "co2_kg": 0.06},
            {"rule_name": "peak_hour_heavy_load", "usd": 0.39, "kwh": 1.50, "co2_kg": 0.38},
            {"rule_name": "daylight_waste", "usd": 0.02, "kwh": 0.06, "co2_kg": 0.02},
        ],
    }
    rep = generate_report(synthetic)
    print("\n" + "=" * 88)
    print("DEEP REPORT  —  generated_by:", rep["generated_by"])
    print("=" * 88)
    print("\nSUMMARY\n  " + rep["summary"].replace(". ", ".\n  "))
    print("\nPATTERNS")
    for p in rep["patterns"]:
        print("  - " + p)
    print("\nWEEKLY PLAN")
    for p in rep["weekly_plan"]:
        print(f"  - {p['action']}\n      ${p['est_monthly_usd']:.2f}/month, effort {p['effort']}")
    tr = rep.get("top_retrofit", {})
    if tr:
        print(f"\nTOP RETROFIT\n  {tr['action']}\n      "
              f"${tr['cost_usd']:.0f}, payback {tr['payback_months']} months")
    print("\n" + "=" * 88 + "\n")
