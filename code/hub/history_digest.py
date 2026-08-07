"""History digest — a Python-computed summary of the labeled 37-day utility history.

WHY THIS EXISTS
    hub/ask.py and hub/planner.py only ever reason from a live snapshot: whatever
    the sensors say right now. They have no sense of "is this normal for this
    home" because nothing feeds them the past. This module is that feed: it
    reads the assumption-labeled history CSV tools/history_disaggregate.py
    already produced and reduces it to the same kind of compact, LLM-safe digest
    cloud_report.build_digest() computes for the live state — same rule applies:
    the LLM only phrases these numbers, it never recomputes them.

WHAT IT DOES NOT DO
    It does not re-run the disaggregation. It does not import anything from
    tools/ — hub/ has no business depending on tools/, and the labeled CSV is
    the actual interface between the two. If that CSV is missing, this module
    returns {} and callers treat history as simply unavailable, the same way a
    missing tariff file falls back rather than crashing energy_model.

THE CAVEAT THAT MUST TRAVEL WITH THE NUMBERS
    Every hvac / car_charging / lights_or_fan figure below is INFERRED from
    interval size and time-of-day on a whole-home meter, not measured
    per-circuit (see tools/history_disaggregate.py). digest["caveat"] states
    this in one line so any consumer can put it in front of the model instead
    of letting an inferred label read as a measured fact.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from energy_model import cost, rate_at

HISTORY_CSV = Path(__file__).resolve().parent.parent / "data" / "Electric_15_Minute_history_labeled.csv"

_STATES = ("hvac", "car_charging", "lights_or_fan", "baseline_only")

_cache: Optional[Dict] = None


def _parse_dt(date_str: str, time_str: str) -> datetime:
    return datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %I:%M %p")


def _load_rows() -> list:
    with HISTORY_CSV.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "Date")
    return rows[header_idx + 1:]


def build_history_digest(force: bool = False) -> Dict:
    """Compact digest of the labeled history. {} if the CSV isn't there.

    Cached after the first call: the source CSV is static for the life of a demo
    process, so recomputing it per question would burn latency for nothing.
    """
    global _cache
    if _cache is not None and not force:
        return _cache

    if not HISTORY_CSV.exists():
        _cache = {}
        return _cache

    try:
        rows = _load_rows()
    except Exception:
        _cache = {}
        return _cache

    kwh = {s: 0.0 for s in _STATES}
    usd = {s: 0.0 for s in _STATES}
    events = {s: 0 for s in _STATES}
    dates = set()
    total_kwh = total_usd = 0.0

    hvac_on_peak_kwh = hvac_on_peak_usd = 0.0
    car_super_off_peak_kwh = 0.0

    for r in rows:
        if not r or not r[0]:
            continue
        date_str, time_str, kwh_str, state = r[0], r[1], r[2], r[6]
        e = float(kwh_str)
        dt = _parse_dt(date_str, time_str)
        rate, period = rate_at(dt)
        c = cost(e, rate)

        dates.add(date_str)
        total_kwh += e
        total_usd += c

        if state in kwh:
            kwh[state] += e
            usd[state] += c
            events[state] += 1

        if state == "hvac" and period == "on_peak":
            hvac_on_peak_kwh += e
            hvac_on_peak_usd += c
        if state == "car_charging" and period == "super_off_peak":
            car_super_off_peak_kwh += e

    days = len(dates) or 1
    hvac_hours_per_day = events["hvac"] / days * 0.25

    hvac_on_peak_pct = round(hvac_on_peak_kwh / kwh["hvac"] * 100, 1) if kwh["hvac"] else 0.0
    car_super_off_peak_pct = (round(car_super_off_peak_kwh / kwh["car_charging"] * 100, 1)
                               if kwh["car_charging"] else 0.0)

    cheap_rate, _ = rate_at(datetime(2026, 8, 1, 2, 0))  # any super-off-peak hour
    hvac_onpeak_shift_monthly_usd = round(
        (hvac_on_peak_usd - hvac_on_peak_kwh * cheap_rate) / days * 30, 2
    ) if hvac_on_peak_kwh else 0.0

    digest = {
        "window_days": days,
        "total_kwh": round(total_kwh, 2),
        "total_usd": round(total_usd, 2),
        # A typical DAY, not just the window total. "How does today compare to
        # my usual month?" is one of the suggested questions on the /ask page,
        # and it was unanswerable: the digest offered a 37-day total and today
        # is one day, so there was nothing to compare against. Probed, the model
        # said so correctly and unhelpfully — "no monthly data or usual pattern
        # is provided ... does not include a monthly baseline or average."
        # It was right. Compute the baseline rather than expect it to be
        # inferred from a total and a day count, which would also be arithmetic
        # the prompt forbids.
        "avg_kwh_per_day": round(total_kwh / days, 2) if days else None,
        "avg_usd_per_day": round(total_usd / days, 2) if days else None,
        "hvac_kwh": round(kwh["hvac"], 2),
        "hvac_usd": round(usd["hvac"], 2),
        "hvac_events": events["hvac"],
        "hvac_hours_per_day": round(hvac_hours_per_day, 1),
        "hvac_on_peak_kwh": round(hvac_on_peak_kwh, 2),
        "hvac_on_peak_usd": round(hvac_on_peak_usd, 2),
        "hvac_on_peak_pct_of_hvac": hvac_on_peak_pct,
        "hvac_onpeak_shift_monthly_usd": hvac_onpeak_shift_monthly_usd,
        "car_charging_kwh": round(kwh["car_charging"], 2),
        "car_charging_usd": round(usd["car_charging"], 2),
        "car_charging_events": events["car_charging"],
        "car_super_off_peak_kwh": round(car_super_off_peak_kwh, 2),
        "car_super_off_peak_pct": car_super_off_peak_pct,
        "lights_or_fan_kwh": round(kwh["lights_or_fan"], 2),
        "lights_or_fan_usd": round(usd["lights_or_fan"], 2),
        "lights_or_fan_events": events["lights_or_fan"],
        "baseline_only_kwh": round(kwh["baseline_only"], 2),
        "baseline_only_usd": round(usd["baseline_only"], 2),
        "caveat": ("History labels (hvac / car_charging / lights_or_fan) are INFERRED "
                   "from interval size and time-of-day on a whole-home meter with no "
                   "per-circuit data -- treat as a lead, not a measured fact."),
    }
    _cache = digest
    return digest


# --------------------------------------------------------------------------
# Self-test — no LLM, no network. Confirms the digest parses and the figures
# are sane. Run: python hub/history_digest.py
# --------------------------------------------------------------------------

if __name__ == "__main__":
    d = build_history_digest(force=True)
    print("=" * 78)
    print("HISTORY DIGEST - self-test")
    print(f"source: {HISTORY_CSV}")
    print("=" * 78)
    if not d:
        print("no history CSV found -- digest is empty, callers must treat "
              "history as unavailable (this is the expected fallback, not a bug)")
    else:
        for k, v in d.items():
            print(f"  {k:32s} {v}")
    print("=" * 78)
