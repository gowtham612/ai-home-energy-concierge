"""Deterministic energy arithmetic for the AI Home Energy Concierge.

This module is the credibility of the entire project. Every number shown on the
dashboard, spoken by the LLM, or claimed on stage originates here.

DESIGN RULE: the LLM never computes. It receives numbers from this module and is
allowed only to phrase them. Each estimate carries a `formula` string that spells
out the calculation so a judge can audit any figure on screen by hand.

No MQTT, no LLM, no network. Pure functions, with ONE exception: the SDG&E
tariff table is read from data/sdge_tou_dr1.json at import, so the published
rates carry their source URL and effective date instead of being anonymous
constants. That read is wrapped and falls back to built-in rates, so this
module still imports on a machine where the file is missing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Dict, Tuple

# --------------------------------------------------------------------------
# Load power draws
# --------------------------------------------------------------------------
# Every entry carries a `source` so we can defend the figure if challenged.
# Figures are typical steady-state draw, not startup surge.

LOADS: Dict[str, Dict] = {
    "led_bulb_set":     {"watts": 36.0,   "label": "LED bulb set (4 x 9 W)",     "source": "US DOE typical LED, 9 W each"},
    "incandescent_set": {"watts": 240.0,  "label": "Incandescent set (4 x 60 W)", "source": "nameplate 60 W each"},
    "ceiling_fan":      {"watts": 70.0,   "label": "Ceiling fan (medium)",        "source": "US DOE typical range 50-80 W"},
    "window_ac":        {"watts": 1100.0, "label": "Window air conditioner",      "source": "Energy Star typical 10k BTU unit"},
    "central_ac":       {"watts": 3500.0, "label": "Central A/C compressor",      "source": "US DOE typical 3-ton residential"},
    "space_heater":     {"watts": 1500.0, "label": "Space heater",                "source": "nameplate, standard US 120 V unit"},
    "tv_65":            {"watts": 120.0,  "label": "65-inch LED TV",              "source": "Energy Star typical 2024 panel"},
    "desktop_pc":       {"watts": 200.0,  "label": "Desktop PC + monitor",        "source": "measured typical idle-to-load average"},
    "game_console":     {"watts": 160.0,  "label": "Game console (active)",       "source": "manufacturer active-play figure"},
    "clothes_dryer":    {"watts": 3000.0, "label": "Clothes dryer (electric)",    "source": "US DOE typical electric resistance dryer"},
    "dishwasher":       {"watts": 1200.0, "label": "Dishwasher (heated cycle)",   "source": "Energy Star typical with heated dry"},
    "fridge":           {"watts": 150.0,  "label": "Refrigerator (compressor on)", "source": "Energy Star typical mid-size"},
    "standby_phantom":  {"watts": 12.0,   "label": "Standby / phantom load",      "source": "LBNL standby power survey, per-device average"},
    # Added so "what if I run the dryer at 9 PM?" is answerable. These are
    # NAMEPLATE / typical figures for a representative appliance, not
    # measurements of anything in this house, and every answer that cites one
    # says "a typical X". typical_run_h is what makes a what-if computable at
    # all: a shift question is watts x hours x (rate_then - rate_now), and
    # without a duration there is no number to give.
    "microwave":        {"watts": 1200.0, "label": "Microwave oven",               "source": "nameplate, standard US 120 V unit", "typical_run_h": 0.1},
    "electric_range":   {"watts": 2400.0, "label": "Electric stove (one element)", "source": "US DOE typical 8-inch element",     "typical_run_h": 0.5},
    "electric_oven":    {"watts": 2400.0, "label": "Electric oven (baking)",       "source": "US DOE typical wall oven",          "typical_run_h": 1.0},
    "table_fan":        {"watts": 35.0,   "label": "Table / desk fan",             "source": "nameplate, typical 12-inch fan",    "typical_run_h": 4.0},
    "patio_lights":     {"watts": 60.0,   "label": "Patio string lights (LED)",    "source": "typical 48-ft LED string",          "typical_run_h": 5.0},
    "washing_machine":  {"watts": 500.0,  "label": "Washing machine (warm wash)",  "source": "Energy Star typical front loader",  "typical_run_h": 1.0},
    "water_heater":     {"watts": 4500.0, "label": "Electric water heater",        "source": "nameplate, standard resistance tank", "typical_run_h": 3.0},
    "ev_charger":       {"watts": 7200.0, "label": "EV charger (Level 2)",         "source": "typical 240 V 30 A home charger",   "typical_run_h": 4.0},
}

# Cycle appliances get a duration too, so any of them can answer a shift
# question. The ones above carry their own; these are the pre-existing entries.
for _k, _h in (("clothes_dryer", 1.0), ("dishwasher", 1.5), ("fridge", 24.0),
               ("ceiling_fan", 6.0), ("window_ac", 6.0), ("central_ac", 6.0),
               ("space_heater", 4.0), ("led_bulb_set", 5.0),
               ("incandescent_set", 5.0), ("tv_65", 4.0), ("desktop_pc", 8.0),
               ("game_console", 3.0), ("standby_phantom", 24.0)):
    LOADS[_k].setdefault("typical_run_h", _h)

# --------------------------------------------------------------------------
# Tariff — SDG&E Schedule TOU-DR1, from the published rate table
# --------------------------------------------------------------------------
# The rates live in data/sdge_tou_dr1.json with their source URL and effective
# date, NOT as constants here. They were previously two hardcoded numbers
# labelled "approximate": $0.32 off-peak and $0.58 on-peak. The real published
# figures are about 40% higher, and there is a THIRD tier those constants could
# not express at all.
#
# The third tier is not a detail — it is a different recommendation. With two
# tiers the only advice available is "move it out of peak". With super-off-peak
# the system can say "run the dryer after midnight", which is worth roughly
# twice as much per kWh shifted.
#
# Loaded at import, with the previous constants kept as a fallback so a missing
# or malformed file degrades to the old behaviour instead of taking the hub
# down. A tariff file is not worth a dead demo.

TARIFF_PATH = Path(__file__).resolve().parent.parent / "data" / "sdge_tou_dr1.json"

_FALLBACK_TARIFF = {
    "schedule": "TOU-DR1 (built-in fallback)",
    "effective_date": "unknown",
    "source_url": "",
    "seasons": {
        "summer": {"months": [6, 7, 8, 9],
                   "on_peak": 0.58, "off_peak": 0.32, "super_off_peak": 0.32},
        "winter": {"months": [10, 11, 12, 1, 2, 3, 4, 5],
                   "on_peak": 0.58, "off_peak": 0.32, "super_off_peak": 0.32},
    },
}


def _load_tariff() -> Dict:
    try:
        with TARIFF_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        # Validate rather than trust: a truncated file that still parses would
        # otherwise produce silent zero-dollar savings.
        for season in ("summer", "winter"):
            for period in ("on_peak", "off_peak", "super_off_peak"):
                if not isinstance(data["seasons"][season][period], (int, float)):
                    raise ValueError(f"{season}.{period} is not a number")
        return data
    except Exception as exc:            # missing, malformed, or incomplete
        print(f"[tariff] using built-in fallback rates: {exc}")
        return dict(_FALLBACK_TARIFF)


TARIFF = _load_tariff()

# Season boundaries. The only value here NOT read off an SDG&E document — the
# rate table splits Summer/Winter without printing the dates. It selects which
# column applies; it never changes a rate.
SUMMER_MONTHS = set(TARIFF["seasons"]["summer"]["months"])

# On-peak is 4-9 PM every day, in both seasons. Only the price changes.
ON_PEAK_START = time(16, 0)
ON_PEAK_END = time(21, 0)

# Super off-peak: midnight-6 AM and 10 AM-2 PM on weekdays; midnight-2 PM on
# weekends and holidays. The 10-14 weekday block used to be March/April only.
# Public holidays are treated as weekdays — the calendar is not modelled, which
# makes the estimate CONSERVATIVE (it charges the higher off-peak rate).
SUPER_OFF_PEAK_WEEKDAY = ((time(0, 0), time(6, 0)), (time(10, 0), time(14, 0)))
SUPER_OFF_PEAK_WEEKEND = ((time(0, 0), time(14, 0)),)

# Back-compatible names. Several modules import these directly; they resolve to
# the SUMMER figures, which is the worst case and the season the demo runs in.
ON_PEAK_USD_PER_KWH = TARIFF["seasons"]["summer"]["on_peak"]
OFF_PEAK_USD_PER_KWH = TARIFF["seasons"]["summer"]["off_peak"]
SUPER_OFF_PEAK_USD_PER_KWH = TARIFF["seasons"]["summer"]["super_off_peak"]

# California grid average carbon intensity.
# Source: CA Energy Commission / EPA eGRID CAMX region, ~0.25 kg CO2 per kWh.
CO2_KG_PER_KWH = 0.25


def season_of(dt: datetime) -> str:
    return "summer" if dt.month in SUMMER_MONTHS else "winter"


def _in_any(t: time, windows) -> bool:
    return any(start <= t < end for start, end in windows)


def rate_at(dt: datetime) -> Tuple[float, str]:
    """Return (usd_per_kwh, period_label) for a local datetime.

    Periods, in precedence order: on-peak (16:00-21:00 daily), then super
    off-peak, then off-peak as the remainder. Windows are inclusive of start
    and exclusive of end, so 21:00 is off-peak rather than on-peak.
    """
    rates = TARIFF["seasons"][season_of(dt)]
    clock = dt.time()

    if ON_PEAK_START <= clock < ON_PEAK_END:
        return rates["on_peak"], "on_peak"

    weekend = dt.weekday() >= 5
    windows = SUPER_OFF_PEAK_WEEKEND if weekend else SUPER_OFF_PEAK_WEEKDAY
    if _in_any(clock, windows):
        return rates["super_off_peak"], "super_off_peak"

    return rates["off_peak"], "off_peak"


def cheapest_rate(dt: datetime) -> Tuple[float, str]:
    """The best rate available on the same day — the target for shifting.

    A deferral recommendation is only honest if it names what the load would be
    shifted TO. Super off-peak exists every day, so that is the floor.
    """
    rates = TARIFF["seasons"][season_of(dt)]
    return rates["super_off_peak"], "super_off_peak"


def tariff_provenance() -> str:
    """One line naming the schedule and when it took effect."""
    return (f"SDG&E Schedule {TARIFF.get('schedule', '?')}, "
            f"effective {TARIFF.get('effective_date', '?')}")


# --------------------------------------------------------------------------
# Core arithmetic
# --------------------------------------------------------------------------


def kwh(watts: float, seconds: float) -> float:
    """Energy in kilowatt-hours.

    Formula: kWh = watts x seconds / 3_600_000
    """
    return (watts * seconds) / 3_600_000.0


def cost(energy_kwh: float, usd_per_kwh: float) -> float:
    """Cost in dollars.

    Formula: usd = kWh x rate
    """
    return energy_kwh * usd_per_kwh


def co2_kg(energy_kwh: float) -> float:
    """Carbon in kilograms.

    Formula: kg = kWh x CO2_KG_PER_KWH
    """
    return energy_kwh * CO2_KG_PER_KWH


@dataclass
class WasteEstimate:
    """One auditable waste figure.

    `formula` is a hard requirement: the dashboard renders it verbatim so any
    number on screen can be recomputed by hand.
    """

    kwh: float
    usd: float
    co2_kg: float
    rate_used: float
    period_label: str
    watts: float
    load_label: str
    source: str
    seconds: float
    formula: str = field(default="")


def waste_estimate(load_key: str, seconds_wasted: float, at_time: datetime) -> WasteEstimate:
    """Compute an auditable waste estimate for a load left running.

    Formula chain:
      kWh = watts x seconds / 3_600_000
      usd = kWh x rate(at_time)
      kg  = kWh x 0.25
    """
    if load_key not in LOADS:
        raise KeyError(f"unknown load key {load_key!r}; add it to LOADS with a source")

    spec = LOADS[load_key]
    watts = spec["watts"]
    rate, period = rate_at(at_time)

    e = kwh(watts, seconds_wasted)
    usd = cost(e, rate)
    kg = co2_kg(e)

    formula = (
        f"{watts:.0f} W x {seconds_wasted:.0f} s = {e:.4f} kWh; "
        f"{e:.4f} kWh x ${rate:.2f}/kWh ({period}) = ${usd:.3f}; "
        f"{e:.4f} kWh x {CO2_KG_PER_KWH} kg/kWh = {kg:.4f} kg CO2"
    )

    return WasteEstimate(
        kwh=e,
        usd=usd,
        co2_kg=kg,
        rate_used=rate,
        period_label=period,
        watts=watts,
        load_label=spec["label"],
        source=spec["source"],
        seconds=seconds_wasted,
        formula=formula,
    )


def annualize(usd_per_event: float, events_per_week: float) -> float:
    """Project a per-event cost to an annual figure.

    Formula: usd_year = usd_event x events_per_week x 52
    """
    return usd_per_event * events_per_week * 52.0


# --------------------------------------------------------------------------
# Self-test — verify the math by hand against this output
# --------------------------------------------------------------------------

if __name__ == "__main__":
    on_peak = datetime(2026, 8, 3, 18, 30)   # 6:30 PM -> on-peak
    off_peak = datetime(2026, 8, 3, 10, 0)   # 10:00 AM -> off-peak

    scenarios = [
        ("incandescent_set", 3600, on_peak,  "Lights left on 1 h, evening peak"),
        ("incandescent_set", 3600, off_peak, "Lights left on 1 h, mid-morning"),
        ("window_ac",        7200, on_peak,  "A/C running 2 h while away, peak"),
        ("clothes_dryer",    2700, on_peak,  "Dryer 45 min during peak window"),
        ("standby_phantom",  28800, off_peak, "Phantom load 8 h overnight"),
    ]

    print("\n" + "=" * 96)
    print("ENERGY MODEL SELF-TEST  —  every figure below is hand-checkable")
    print("=" * 96)

    for key, secs, when, note in scenarios:
        est = waste_estimate(key, secs, when)
        print(f"\n{note}")
        print(f"  load     : {est.load_label}  ({est.watts:.0f} W)")
        print(f"  source   : {est.source}")
        print(f"  window   : {when.strftime('%H:%M')}  -> {est.period_label} @ ${est.rate_used:.2f}/kWh")
        print(f"  energy   : {est.kwh:.4f} kWh")
        print(f"  cost     : ${est.usd:.3f}")
        print(f"  carbon   : {est.co2_kg:.4f} kg CO2")
        print(f"  formula  : {est.formula}")

    print("\n" + "-" * 96)
    daily = waste_estimate("window_ac", 7200, on_peak)
    print(f"Annualized: A/C wasted 2 h, 4 nights a week  ->  "
          f"${annualize(daily.usd, 4):.2f} per year")
    print(f"  formula  : ${daily.usd:.3f} x 4 events/week x 52 weeks")
    print("-" * 96 + "\n")
