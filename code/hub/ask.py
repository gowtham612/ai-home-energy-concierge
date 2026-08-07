"""Tier 3: natural-language Q&A over the current home state, on the NPU.

WHY THIS EXISTS
    A plan list is something a judge reads. A question they typed themselves,
    answered on-device in a couple of seconds, is something they remember — and
    during the gallery walk it is the clearest available proof that a real model
    is running locally rather than a template pretending to be one.

THE HONESTY MECHANISM
    The model is given ONLY a digest computed in Python (cloud_report.build_digest)
    and may cite only numbers from it. Every answer then goes through the P1-C
    provenance verifier, and the page shows a verified / unverified badge. That
    badge is the demo moment: it is a mechanical check, not a promise.

LATENCY
    Measured here: first token 0.15 s, full answer ~2.5 s, because latency tracks
    OUTPUT length and the prompt demands brevity. Streaming is therefore worth
    real UX: the answer starts appearing essentially immediately.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Dict, Generator, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

import llm as llm_mod
from cloud_report import build_digest
from history_digest import build_history_digest

AI_ASK = os.environ.get("AI_ASK", "0") == "1"

ASK_MAX_TOKENS = int(os.environ.get("ASK_MAX_TOKENS", "180"))
ASK_TIMEOUT_S = int(os.environ.get("ASK_TIMEOUT_S", "30"))

SUGGESTED_QUESTIONS = [
    "Why is my bill high?",
    "What should I do first?",
    "What if I shift the dryer to 9 PM?",
    "Is anything unusual right now?",     # exercises the tier-1 anomaly signal
    "How does today compare to my usual month?",
]

SYSTEM_PROMPT = """You answer questions about a home's energy use, over two
windows: what is happening RIGHT NOW, and a multi-day PAST BILLING PERIOD.
Both are in scope and both are in the digest.

You are given a DIGEST computed in Python. Every number you may use is in it.

RULES:
- Do NOT do arithmetic. Do NOT invent, restate differently, or round any number.
  Cite figures exactly as they appear in the digest, or omit them.
- The digest has a LIVE section (right now) and a separate HISTORY / BILL PERIOD
  section (a past multi-day window). Never answer a question about current/live
  usage with a HISTORY figure, or vice versa -- state which window a number is
  from.
- Questions about a BILL, a MONTH, "usually", or any comparison over time are
  PERIOD questions: answer them from the BILL PERIOD / HISTORY figures. A bill
  covers a period, so the instantaneous wattage is not the answer to it. The
  live figures are still there if you need to contrast the two.
- HISTORY hvac/car_charging/lights_or_fan figures are INFERRED labels, not
  measured per-circuit data. Say "inferred" or "estimated" when citing them.
- If the digest does not contain what was asked, say so plainly. Do not guess.
- Answer in at most 3 short sentences. Be direct and practical.
- Plain prose. No markdown, no JSON, no preamble."""


def _period_question(question: Optional[str]) -> bool:
    """True if the question is about a billing PERIOD rather than this instant.

    "Why is my bill high?" is the question this whole history feature exists to
    answer, and it was being answered purely from live state -- $0.319 of
    current waste, with the 37-day $254.25 never mentioned. Nothing was wrong
    with the digest's contents; the live figures simply came first and the model
    answered from the top of what it saw. A bill spans a period by definition,
    so when the question is period-shaped the period totals lead.
    """
    q = (question or "").lower()
    return any(w in q for w in
               ("bill", "month", "monthly", "usual", "compare", "history",
                "historic", "past", "last 37", "37 day", "period", "so far"))


def _digest_lines(state: Dict,
                  question: Optional[str] = None) -> Tuple[str, Dict[str, str]]:
    """(prompt text, allowed-number map for the provenance verifier).

    The allowed map is built from the SAME figures put in the prompt, so the
    verifier and the model see exactly one source of truth. Building them apart
    would let the two drift and make the badge meaningless.

    `question` only ever changes the ORDER of what is included, never the
    contents: a period-shaped question gets the history totals first. No figure
    is added or withheld based on what was asked.
    """
    d = build_digest(state)
    allowed: Dict[str, str] = {}
    lines: List[str] = []

    # A period-shaped question gets the billing-period totals FIRST, before any
    # live figure. Same numbers either way -- the full HISTORY line still
    # appears below -- but the answer to "why is my bill high?" is a period
    # total, and whatever leads is what gets answered from.
    if _period_question(question):
        _hd = build_history_digest()
        if _hd:
            lines.append(
                f"BILL PERIOD ({_hd['window_days']} days -- ANSWER BILL AND "
                f"MONTHLY QUESTIONS FROM THESE, not from the live figures "
                f"further down): total {_hd['total_kwh']} kWh costing "
                f"${_hd['total_usd']} over {_hd['window_days']} days. Largest "
                f"contributor: the air conditioner / heating and cooling at "
                f"${_hd['hvac_usd']} over {_hd['window_days']} days, of which "
                f"${_hd['hvac_on_peak_usd']} was at the expensive on-peak rate; "
                f"moving that on-peak slice to super-off-peak would save about "
                f"${_hd['hvac_onpeak_shift_monthly_usd']} a month. Live "
                f"instantaneous figures below describe THIS MOMENT and are not "
                f"the bill."
            )

    # Keys are build_digest's own: total_wasted_usd / _kwh / _co2_kg, etc.
    for key, label, fmt in (("total_wasted_usd", "avoidable $", "{:.3f}"),
                            ("total_wasted_kwh", "kWh", "{:.4f}"),
                            ("total_wasted_co2_kg", "kg CO2", "{:.4f}")):
        if d.get(key) is not None:
            allowed[key] = f"{d[key]}"
    lines.append(f"TOTALS: avoidable ${d.get('total_wasted_usd', 0):.3f}, "
                 f"{d.get('total_wasted_kwh', 0):.4f} kWh, "
                 f"{d.get('total_wasted_co2_kg', 0):.4f} kg CO2")

    tariff = state.get("tariff") or {}
    tn = d.get("tariff_now") or {}
    rate = tn.get("rate", tariff.get("rate"))
    if rate is not None:
        allowed["tariff.rate"] = f"{rate}"
        lines.append(f"TARIFF NOW: ${rate}/kWh "
                     f"({tn.get('period', tariff.get('period',''))}) "
                     f"at {tariff.get('clock','')}")
    # Both rates, so "what if I move it to 9 PM?" can be answered from the digest
    # instead of the model reasoning that the off-peak figure "is not specified".
    for k in ("on_peak_rate", "off_peak_rate", "super_off_peak_rate",
              "co2_kg_per_kwh"):
        if d.get(k) is not None:
            allowed[k] = f"{d[k]}"
            lines.append(f"{k.upper()}: {d[k]}")

    # The appliance catalogue, so a what-if answer's numbers are IN the digest
    # and verify. Without this the computed answer was correct and flagged
    # "PROVENANCE FAIL: 3000, 1.16" -- the badge saying unverified next to a
    # figure the hub itself calculated from a cited DOE nameplate.
    #
    # These are TYPICAL ratings for a representative appliance, not
    # measurements of anything in this house, and the line says so.
    try:
        import energy_model as _em
        picks = ("clothes_dryer", "dishwasher", "fridge", "microwave",
                 "electric_range", "electric_oven", "ceiling_fan", "table_fan",
                 "patio_lights", "washing_machine", "water_heater",
                 "ev_charger", "space_heater", "tv_65")
        bits = []
        for _k in picks:
            _spec = _em.LOADS.get(_k)
            if not _spec:
                continue
            allowed[f"appliance.{_k}.watts"] = f"{_spec['watts']}"
            allowed[f"appliance.{_k}.watts_int"] = f"{int(_spec['watts'])}"
            _h = _spec.get("typical_run_h")
            if _h is not None:
                allowed[f"appliance.{_k}.hours"] = f"{_h}"
                _kwh = _spec["watts"] / 1000.0 * _h
                allowed[f"appliance.{_k}.kwh"] = f"{round(_kwh, 2)}"
                _rates = [(_rn, d.get(_r)) for _r, _rn in
                          (("on_peak_rate", "on"), ("off_peak_rate", "off"),
                           ("super_off_peak_rate", "sop")) if d.get(_r)]
                for _rn, _rv in _rates:
                    allowed[f"appliance.{_k}.usd_{_rn}"] = f"{round(_kwh * _rv, 2)}"
                # The DIFFERENCE between two periods is the actual answer to a
                # shift question, and it is not any single figure above. Without
                # it the arithmetic verified and the conclusion did not.
                for _i, (_an, _av) in enumerate(_rates):
                    for _bn, _bv in _rates[_i + 1:]:
                        allowed[f"appliance.{_k}.delta_{_an}_{_bn}"] =                             f"{round(abs(_kwh * _av - _kwh * _bv), 2)}"
            bits.append(f"{_spec['label']} {int(_spec['watts'])} W"
                        + (f" for ~{_h} h" if _h else ""))
        if bits:
            lines.append("TYPICAL APPLIANCE RATINGS (nameplate figures for a "
                         "representative appliance, NOT measurements of this "
                         "home): " + "; ".join(bits) + ".")
    except Exception:
        pass

    tw = d.get("current_watts", state.get("total_watts"))
    if tw is not None:
        allowed["total_watts"] = f"{tw}"
        # Say that the total IS the total. Asked "how many watts am I drawing at
        # this moment?", the model answered 1100 W — the A/C alone — citing the
        # per-load line. Both numbers are real and live, so the provenance check
        # passes either way; nothing in the digest said which one answers a
        # whole-home question, and the per-load lines are more specific-looking.
        lines.append(f"DRAWING NOW (whole home, ALL loads combined): {tw} W. "
                     f"This is the answer to 'how much am I drawing/using right "
                     f"now'. The per-load LOAD lines below are components of "
                     f"this total, not the total.")

    # 37 days of real utility data, disaggregated into inferred buckets by
    # tools/history_disaggregate.py. Labeled HISTORY and NOT LIVE in the prompt
    # itself, not just in this comment -- probed live, a model asked "what's my
    # current usage?" will reach for the biggest number in the digest unless the
    # window each figure covers is unambiguous at the point of citation.
    hd = build_history_digest()
    if hd:
        for k in ("window_days", "total_kwh", "total_usd",
                  "avg_kwh_per_day", "avg_usd_per_day", "hvac_kwh", "hvac_usd",
                  "hvac_hours_per_day", "hvac_on_peak_kwh", "hvac_on_peak_usd",
                  "hvac_on_peak_pct_of_hvac", "hvac_onpeak_shift_monthly_usd",
                  # car_super_off_peak_pct is deliberately absent: it is 100% by
                  # construction (car_charging is DEFINED as midnight-6AM load,
                  # the same block as super-off-peak), so it measures the
                  # labelling rule, not the household. Offering it as a
                  # verifiable figure let the model report "already in the
                  # cheapest window, no adjustment needed" WITH a verified
                  # badge. A meaningless number is deleted, not shipped with a
                  # warning label telling the model to ignore it.
                  "car_charging_kwh", "car_charging_usd",
                  "lights_or_fan_kwh", "lights_or_fan_usd",
                  "baseline_only_kwh", "baseline_only_usd"):
            if hd.get(k) is not None:
                allowed[f"history.{k}"] = f"{hd[k]}"
        d = hd["window_days"]
        # Every history figure carries its window INLINE. Probed live, "what did
        # my HVAC cost historically?" returned "$14.84 per day" — the 37-day
        # on-peak subtotal with an invented daily rate. A number whose timeframe
        # is only stated once, in a header, gets re-timeframed by the model.
        #
        # "hvac" is aliased to the words people actually use. Asked "how much did
        # my AIR CONDITIONER use over the past month?", the model returned the
        # 0.55 kWh LIVE finding and captioned it "as reported in the HISTORY
        # section" — 285x wrong — because the only tokens matching "air
        # conditioner" were in the live A/C finding. Asking the same thing as
        # "heating and cooling over the last 37 days" answered correctly.
        lines.append(
            f"HISTORY (a {d}-day past window, NOT live -- every number in this "
            f"line covers {d} days unless it says per day): "
            f"total {hd['total_kwh']} kWh over {d} days (${hd['total_usd']} over {d} days). "
            f"A TYPICAL DAY in that window was {hd['avg_kwh_per_day']} kWh "
            f"(${hd['avg_usd_per_day']}) -- use this as the 'usual' baseline when "
            f"asked how today compares. "
            f"hvac -- this is the AIR CONDITIONER / AC / A/C / heating and cooling "
            f"bucket (inferred) -- {hd['hvac_kwh']} kWh over {d} days "
            f"(${hd['hvac_usd']} over {d} days), averaging ~{hd['hvac_hours_per_day']} h/day, "
            f"{hd['hvac_on_peak_pct_of_hvac']}% of it on-peak "
            f"(${hd['hvac_on_peak_usd']} over {d} days); shifting that on-peak slice to "
            f"super-off-peak would save ~${hd['hvac_onpeak_shift_monthly_usd']}/month. "
            f"car_charging (inferred) {hd['car_charging_kwh']} kWh over {d} days "
            f"(${hd['car_charging_usd']} over {d} days). "
            f"lights_or_fan (inferred) {hd['lights_or_fan_kwh']} kWh over {d} days "
            f"(${hd['lights_or_fan_usd']} over {d} days). "
            f"baseline/standby {hd['baseline_only_kwh']} kWh over {d} days "
            f"(${hd['baseline_only_usd']} over {d} days)."
        )
        lines.append(f"HISTORY CAVEAT: {hd['caveat']}")

    for name, room in (state.get("rooms") or {}).items():
        bits = []
        for k, label in (("temp_c", "C"), ("humidity", "% RH"), ("lux", " lux")):
            if room.get(k) is not None:
                allowed[f"{name}.{k}"] = f"{room[k]}"
                bits.append(f"{room[k]}{label}")
        bits.append("occupied" if room.get("occupancy") else "empty")
        if room.get("temp_src"):
            bits.append(f"temp source={room['temp_src']}")
        lines.append(f"ROOM {name}: " + ", ".join(bits))

    for key, load in (state.get("loads") or {}).items():
        w = load.get("watts")
        if w is not None:
            allowed[f"{key}.watts"] = f"{w}"
        lines.append(f"LOAD {key}: {load.get('state')} at {w} W"
                     + (" (real metered device)" if load.get("metered")
                        else " (simulated)"))

    for r in (state.get("recos") or [])[:6]:
        rid = r.get("id", "?")
        if r.get("usd") is not None:
            allowed[f"{rid}.usd"] = f"{r['usd']}"
        if r.get("kwh") is not None:
            allowed[f"{rid}.kwh"] = f"{r['kwh']}"
        det = r.get("detector", "rule")
        if r.get("anomaly_score") is not None:
            allowed[f"{rid}.score"] = f"{r['anomaly_score']}"
        lines.append(f"FINDING {rid} [{r.get('severity','')}, detector={det}]: "
                     f"{r.get('title','')} — ${r.get('usd',0)}, {r.get('kwh',0)} kWh"
                     + (f", anomaly score {r['anomaly_score']}"
                        if r.get("anomaly_score") is not None else ""))

    # R7 comfort guardrail. WITHOUT THIS the model cannot answer "why did you
    # refuse?" — it has no idea a guardrail exists. Asked cold it confabulated
    # ("no intervention occurred"), and worse, when told the room was 29.5 C it
    # RECOMMENDED switching the A/C off — the exact action R7 blocks. An
    # assistant contradicting its own safety gate on stage is worse than one
    # that says nothing, so the thresholds and the current verdict go in the
    # digest as facts the model can cite.
    try:
        import rules as _rules
        cmax, cmin = _rules.COMFORT_MAX_C, _rules.COMFORT_MIN_C
    except Exception:
        cmax, cmin = 27.0, 16.0
    allowed["comfort.max_c"] = f"{cmax}"
    allowed["comfort.min_c"] = f"{cmin}"
    lines.append(f"COMFORT GUARDRAIL (rule R7): the system REFUSES to switch off "
                 f"cooling above {cmax} C, or heating below {cmin} C, even when "
                 f"asked. Human comfort outranks the saving.")

    for _rname, _room in (state.get("rooms") or {}).items():
        t = _room.get("temp_c")
        if t is None:
            continue
        if t > cmax:
            lines.append(f"GUARDRAIL ACTIVE in {_rname}: {t} C is ABOVE the {cmax} C "
                         f"limit, so switching the A/C off is currently REFUSED "
                         f"(HTTP 409). Do not advise turning it off.")
        elif t < cmin:
            lines.append(f"GUARDRAIL ACTIVE in {_rname}: {t} C is BELOW the {cmin} C "
                         f"limit, so switching the heater off is currently REFUSED "
                         f"(HTTP 409). Do not advise turning it off.")
        else:
            # The evaluated verdict, not just the rule. Previously this branch
            # emitted NOTHING when the room was inside the comfort band: the
            # digest stated "the system REFUSES above 27.0 C" and then went
            # silent on whether that applied right now. Asked "why did you
            # refuse to turn off the air conditioner?" at 23.5 C, the model
            # filled the gap by asserting the room was "actively maintaining a
            # temperature above 27.0 C" — inventing the precondition to justify
            # a refusal that never happened.
            #
            # Absence of evidence was being read as evidence. State both
            # verdicts explicitly so there is nothing to infer.
            lines.append(f"GUARDRAIL NOT ACTIVE in {_rname}: {t} C is INSIDE the "
                         f"{cmin}-{cmax} C comfort band, so R7 is NOT refusing "
                         f"anything right now. No comfort refusal is in force. "
                         f"If asked why something was refused, the answer is that "
                         f"R7 did NOT refuse at this temperature.")

    # A refusal that actually happened, if the hub recorded one.
    ref = state.get("last_refusal") or {}
    if ref.get("reason"):
        lines.append(f"MOST RECENT REFUSAL: {ref['reason']}")

    plan = state.get("plan") or {}
    if plan.get("situation"):
        lines.append(f"PLAN SITUATION: {plan['situation']}")

    user = state.get("user") or {}
    if user.get("presence"):
        lines.append(f"USER: presence {user['presence']}")

    if not lines:
        lines.append("No findings and no live device state.")

    return "\n".join(lines), allowed


# Questions whose answers the hub has already COMPUTED. For these the model can
# only degrade a known-correct result, so they never reach it.
#
# The split is by whether the system already knows the answer, not by
# difficulty: R7's verdict is the output of a rule that just ran, the combined
# cost is addition, and the anomaly score came from the edge detector. Probing
# showed the model getting all three wrong while the provenance badge read
# "verified" -- it confabulated a temperature above 27 C to justify a refusal
# that never happened, and answered "No, nothing unusual is happening" in the
# same breath as describing an anomaly scoring 0.81.
#
# Everything genuinely interpretive -- why the bill is high, what to do first,
# what-if questions, comparisons -- still goes to the model. This narrows where
# it can be wrong; it does not take the AI out of the demo.
# Keyword routing is itself a weak form of enumerating phrasings, and it was
# caught doing exactly that: a held-out probe asked "add up everything that's
# being wasted", which matches none of the arithmetic keywords below as they
# first stood, so it went to the model and came back as prose with no total.
# Widened once with the natural synonyms. This will still miss phrasings nobody
# thought of — the honest ceiling of matching on words. An intent classifier
# would generalise, but it is not worth a model call in front of an audience.
_COMPUTED_INTENTS = (
    ("refus", "guardrail", "why won't you", "why not turn",
     "stop anything", "being blocked", "blocked right now"),   # R7 verdict
    ("combined", "altogether", "all the finding", "add up",
     "total cost", "total waste", "in total", "sum of"),       # arithmetic
    ("unusual", "anomal", "strange"),                          # edge detector
    ("what if", "instead of", "cost to run", "shift the",
     "move the", "run the"),                                   # appliance what-if
)


def _is_computed(question: str) -> bool:
    q = (question or "").lower()
    return any(any(k in q for k in group) for group in _COMPUTED_INTENTS)


# Appliance lookup for what-if questions. Matched on the words people use, not
# the dict key: nobody types "electric_range".
_APPLIANCE_WORDS = {
    "dryer": "clothes_dryer", "clothes dryer": "clothes_dryer",
    "dishwasher": "dishwasher", "fridge": "fridge", "refrigerator": "fridge",
    "microwave": "microwave", "stove": "electric_range", "range": "electric_range",
    "hob": "electric_range", "cooktop": "electric_range",
    "oven": "electric_oven", "ceiling fan": "ceiling_fan",
    "table fan": "table_fan", "desk fan": "table_fan",
    "patio": "patio_lights", "string lights": "patio_lights",
    "washing machine": "washing_machine", "washer": "washing_machine",
    "water heater": "water_heater", "ev": "ev_charger", "car charger": "ev_charger",
    "tv": "tv_65", "television": "tv_65", "console": "game_console",
    "pc": "desktop_pc", "computer": "desktop_pc",
    "space heater": "space_heater", "air conditioner": "window_ac",
    "a/c": "window_ac", "ac": "window_ac",
}


def _appliance_in(q: str):
    """Longest match wins, so "table fan" never resolves as "ac" inside it."""
    try:
        import energy_model as _em
    except Exception:
        return None
    best = None
    for word, key in _APPLIANCE_WORDS.items():
        if word in q and key in _em.LOADS:
            if best is None or len(word) > len(best[0]):
                best = (word, key)
    return (best[1], _em.LOADS[best[1]]) if best else None


def _hour_in(q: str):
    """Pull an hour out of '9 PM', '21:00', 'at 3am'. None if absent."""
    m = re.search(r"\b(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)\b", q)
    if m:
        h = int(m.group(1)) % 12
        return h + 12 if m.group(3) == "pm" else h
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", q)
    if m and 0 <= int(m.group(1)) <= 23:
        return int(m.group(1))
    return None


def deterministic_answer(question: str, state: Dict) -> str:
    """Answer from the digest with no model at all.

    The fallback must be genuinely useful, not an apology: with GenieX dead the
    demo still has to answer the question, just less fluently.
    """
    d = build_digest(state)
    now_dt = datetime.fromtimestamp(state.get("now") or time.time())
    total_usd = d.get("total_wasted_usd", 0.0)
    total_kwh = d.get("total_wasted_kwh", 0.0)
    recos = state.get("recos") or []
    q = (question or "").lower()

    if (("usual" in q and "unusual" not in q) or "compare" in q
            or "history" in q or "past month" in q):
        hd = build_history_digest()
        if hd:
            # No car_super_off_peak_pct here either: it is 100% by construction
            # (see the note in _digest_lines), so quoting it as "already
            # off-peak" would be praising a labelling artifact.
            return (f"Over the last {hd['window_days']} days: an estimated "
                    f"{hd['hvac_kwh']} kWh on heating/cooling — the A/C — "
                    f"(${hd['hvac_usd']}), "
                    f"{hd['car_charging_kwh']} kWh on car charging "
                    f"(${hd['car_charging_usd']}), "
                    f"{hd['lights_or_fan_kwh']} kWh on lights/fan. "
                    f"All figures cover the whole {hd['window_days']} days, not one day. "
                    f"These are inferred from whole-home data, not measured per-circuit.")
        return "No historical usage data is available right now."

    # Why was something refused. The hub EVALUATED R7 -- it knows the answer
    # exactly -- so asking a 4B model to reconstruct it from prose is choosing
    # to be wrong some of the time. Probed at 23.5 C it asserted the room was
    # "actively maintaining a temperature above 27.0 C", inventing the
    # precondition for a refusal that never happened, and the provenance badge
    # still read "verified" because 27.0 is a real number in the digest.
    if "refus" in q or "guardrail" in q or "why won't you" in q or "why not turn" in q:
        try:
            import rules as _r
            cmax, cmin = _r.COMFORT_MAX_C, _r.COMFORT_MIN_C
        except Exception:
            cmax, cmin = 27.0, 16.0
        ref = state.get("last_refusal") or {}
        if ref.get("reason"):
            return f"It was refused because {ref['reason']}"
        hot = [(n, rm.get("temp_c")) for n, rm in (state.get("rooms") or {}).items()
               if rm.get("temp_c") is not None and rm["temp_c"] > cmax]
        if hot:
            n, t = hot[0]
            return (f"The comfort guardrail (R7) refused it: {n} is {t} C, above "
                    f"the {cmax} C limit, so cutting cooling is blocked even when "
                    f"asked. Human comfort outranks the saving.")
        temps = [(n, rm.get("temp_c")) for n, rm in (state.get("rooms") or {}).items()
                 if rm.get("temp_c") is not None]
        if temps:
            n, t = temps[0]
            return (f"Nothing was refused. The comfort guardrail (R7) only blocks "
                    f"switching cooling off above {cmax} C, and {n} is {t} C — "
                    f"inside the {cmin}-{cmax} C band — so R7 is not in force "
                    f"right now.")
        return (f"Nothing was refused. R7 only blocks cutting cooling above "
                f"{cmax} C, and no room temperature is being reported.")

    # "What if I run the dryer at 9 PM?" — a shift question is
    # watts x hours x (rate_then - rate_now), which is arithmetic over a
    # nameplate figure and the tariff. The model was told not to do arithmetic
    # and had no appliance data anyway, so it correctly answered "the digest
    # does not contain information about the dryer" — on a button the /ask page
    # itself offers.
    if any(w in q for w in ("what if", "instead of", "cost to run",
                            "shift the", "move the", "run the")):
        hit = _appliance_in(q)
        if hit:
            key, spec = hit
            when = _hour_in(q)
            watts = float(spec["watts"])
            hours = float(spec.get("typical_run_h", 1.0))
            kwh = watts / 1000.0 * hours
            try:
                import energy_model as _em
                now_rate, now_period = _em.rate_at(now_dt)
                if when is None:
                    return (f"A typical {spec['label'].lower()} draws {watts:.0f} W "
                            f"for about {hours} h, so {kwh:.2f} kWh. Right now that "
                            f"is {now_period.replace('_',' ')} at ${now_rate}/kWh, "
                            f"costing ${kwh * now_rate:.2f}. Tell me a time and I "
                            f"will compare it.")
                then_dt = now_dt.replace(hour=when, minute=0, second=0, microsecond=0)
                then_rate, then_period = _em.rate_at(then_dt)
                now_cost, then_cost = kwh * now_rate, kwh * then_rate
                delta = now_cost - then_cost
                verb = "saves" if delta > 0 else "costs an extra"
                return (f"A typical {spec['label'].lower()} draws {watts:.0f} W for "
                        f"about {hours} h — {kwh:.2f} kWh. Running it now "
                        f"({now_period.replace('_',' ')}, ${now_rate}/kWh) costs "
                        f"${now_cost:.2f}; at {when:02d}:00 "
                        f"({then_period.replace('_',' ')}, ${then_rate}/kWh) it costs "
                        f"${then_cost:.2f}. Shifting it {verb} ${abs(delta):.2f} per "
                        f"run. This is a typical nameplate figure, not a measurement "
                        f"of your appliance.")
            except Exception:
                pass

    # Arithmetic over the findings. There is no reason to let a model add up
    # numbers the hub already holds.
    if ("combined" in q or "total" in q or "altogether" in q or "all the finding" in q
            or "sum" in q):
        if recos:
            parts = ", ".join(f"{r.get('title','finding')} ${float(r.get('usd',0) or 0):.3f}"
                              for r in recos)
            return (f"${total_usd:.3f} in total across {len(recos)} finding(s): "
                    f"{parts}. That is {total_kwh:.4f} kWh of avoidable waste.")
        return "There are no active findings, so the combined cost is $0.000."

    if "unusual" in q or "anomal" in q or "strange" in q:
        learned = [r for r in recos if r.get("detector") == "learned"]
        if learned:
            r = learned[0]
            return (f"Yes — the learned detector flagged {r.get('title','a load')} "
                    f"(score {r.get('anomaly_score')}). It is a learned score on "
                    f"SIMULATED training data, not a measurement.")
        return "Nothing is scoring anomalous right now."

    if "first" in q or "should i do" in q:
        if recos:
            r = max(recos, key=lambda x: x.get("usd", 0))
            # Format explicitly: a raw float renders as $0.0004174503347608778,
            # which reads as a bug to anyone looking at the screen.
            return (f"Start with: {r.get('title','the largest finding')} "
                    f"— ${float(r.get('usd', 0) or 0):.3f} avoidable.")
        return "Nothing needs action right now."

    if "bill" in q or "high" in q or "why" in q:
        if recos:
            return (f"There is ${total_usd:.3f} of avoidable waste right now "
                    f"across {len(recos)} finding(s), {total_kwh:.4f} kWh.")
        return "No avoidable waste is detected right now."

    if recos:
        return (f"{len(recos)} finding(s) are active, "
                f"${total_usd:.3f} avoidable in total.")
    return "Nothing wasteful is detected right now."


class Asker:
    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.base_url = (base_url or llm_mod.LLM_BASE_URL).rstrip("/")
        self.model = model or llm_mod.LLM_MODEL
        self.violations = 0

    def _payload(self, question: str, digest_text: str, stream: bool) -> Dict:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"DIGEST:\n{digest_text}\n\n"
                                            f"QUESTION: {question}"},
            ],
            "temperature": 0.3,
            "max_tokens": ASK_MAX_TOKENS,
            "stream": stream,
        }

    def _headers(self) -> Dict:
        h = {"Content-Type": "application/json"}
        if llm_mod.LLM_API_KEY:
            h["Authorization"] = f"Bearer {llm_mod.LLM_API_KEY}"
        return h

    def ask(self, question: str, state: Dict) -> Dict:
        """Non-streaming answer + provenance verdict."""
        digest_text, allowed = _digest_lines(state, question)
        t0 = time.time()

        # Computed answers do not go to the model at all — see _COMPUTED_INTENTS.
        if _is_computed(question) or not llm_mod.LLM_ENABLED or requests is None:
            return self._wrap(deterministic_answer(question, state),
                              self._with_question_numbers(allowed, question),
                              "computed" if _is_computed(question) else "template",
                              time.time() - t0)
        try:
            r = requests.post(f"{self.base_url}/chat/completions",
                              headers=self._headers(),
                              json=self._payload(question, digest_text, False),
                              timeout=ASK_TIMEOUT_S)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            return self._wrap(text, self._with_question_numbers(allowed, question),
                              "llm", time.time() - t0)
        except Exception as exc:
            print(f"[ask] falling back to deterministic ({type(exc).__name__}: {exc})")
            return self._wrap(deterministic_answer(question, state),
                              self._with_question_numbers(allowed, question),
                              "template", time.time() - t0)

    def stream(self, question: str, state: Dict) -> Generator[Dict, None, None]:
        """Yield {'delta': str} chunks then a final {'done': True, ...} record.

        First token arrives in ~0.15 s here, so a ~2.5 s answer reads as instant.
        """
        digest_text, allowed = _digest_lines(state, question)
        t0 = time.time()

        # Must match ask() exactly. The /ask PAGE streams, so routing only in
        # ask() would fix the tests and leave the on-camera path untouched —
        # which is the version of this fix that fails in front of an audience.
        if _is_computed(question) or not llm_mod.LLM_ENABLED or requests is None:
            text = deterministic_answer(question, state)
            yield {"delta": text}
            yield self._wrap(text, allowed,
                             "computed" if _is_computed(question) else "template",
                             time.time() - t0, done=True)
            return

        acc = ""
        try:
            with requests.post(f"{self.base_url}/chat/completions",
                               headers=self._headers(),
                               json=self._payload(question, digest_text, True),
                               stream=True, timeout=ASK_TIMEOUT_S) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    raw = line.decode("utf-8", "ignore")
                    if raw.startswith("data:"):
                        raw = raw[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        d = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    delta = (d.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        acc += delta
                        yield {"delta": delta}
            if not acc.strip():
                raise ValueError("empty stream")
            yield self._wrap(acc.strip(), allowed, "llm", time.time() - t0, done=True)
        except Exception as exc:
            print(f"[ask] stream failed, falling back ({type(exc).__name__}: {exc})")
            text = deterministic_answer(question, state)
            yield {"delta": ("\n" if acc else "") + text}
            yield self._wrap(text, allowed, "template", time.time() - t0, done=True)

    @staticmethod
    def _with_question_numbers(allowed: Dict, question: str) -> Dict:
        """Numbers the USER put in the question are not the model's invention.

        Probed live: asked "the room is set to 65 degrees, is that reasonable?"
        the model correctly answered that the room is 18.3 C, not 65 — and the
        badge went amber because it had echoed "65". Flagging a model for
        repeating the question back is a false positive, and a verifier that
        cries wolf on a correct answer trains everyone to ignore the badge.
        """
        out = dict(allowed)
        try:
            import provenance
            for i, n in enumerate(provenance.extract_numbers(question or "")):
                out[f"question.{i}"] = n
        except Exception:
            pass
        return out

    def _wrap(self, text: str, allowed: Dict, answered_by: str,
              latency: float, done: bool = False) -> Dict:
        prov, unverified = "unchecked", []
        try:
            import provenance
            ok, unverified = provenance.verify(text, allowed)
            prov = "verified" if ok else "unverified"
            if not ok:
                self.violations += 1
                print(f"[ask] PROVENANCE FAIL — not in digest: {unverified}")
        except ImportError:
            pass
        out = {"answer": text, "answered_by": answered_by,
               "provenance": prov, "unverified": unverified,
               "latency_s": round(latency, 3), "figures_offered": len(allowed)}
        if done:
            out["done"] = True
        return out


ASKER = Asker()


# --------------------------------------------------------------------------
# Self-test:  AI_ASK=1 python hub/ask.py
#             LLM_ENABLED=0 python hub/ask.py     (fallback path)
# --------------------------------------------------------------------------

def _demo_state() -> Dict:
    return {
        "rooms": {"living": {"occupancy": False, "lux": 110, "temp_c": 23.5,
                             "humidity": 47, "temp_src": "knob_sim"}},
        "loads": {"living/ac": {"state": "on", "watts": 1100, "metered": True},
                  "living/dryer": {"state": "on", "watts": 2400, "metered": False}},
        "user": {"presence": "away"},
        "total_watts": 3500,
        "tariff": {"rate": 0.58, "period": "on_peak", "clock": "18:30"},
        "recos": [
            {"id": "r2-living-ac", "title": "A/C cooling an empty home",
             "severity": "critical", "usd": 0.319, "kwh": 0.55, "detector": "rule"},
            {"id": "learned-living-dryer", "title": "Unusual pattern for this home",
             "severity": "serious", "usd": 0.39, "kwh": 0.67,
             "detector": "learned", "anomaly_score": 0.81},
        ],
        "plan": {"situation": "A/C is cooling an empty home during peak hours."},
    }


if __name__ == "__main__":
    state = _demo_state()
    print("=" * 78)
    print("NATURAL-LANGUAGE Q&A — self-test")
    print(f"LLM_ENABLED={llm_mod.LLM_ENABLED}  endpoint={llm_mod.LLM_BASE_URL}")
    print("=" * 78)

    asker = Asker()
    for q in SUGGESTED_QUESTIONS:
        res = asker.ask(q, state)
        print(f"\nQ: {q}")
        print(f"   [{res['answered_by']}] {res['latency_s']}s  "
              f"provenance={res['provenance']}"
              + (f"  UNVERIFIED={res['unverified']}" if res["unverified"] else ""))
        print(f"   {res['answer']}")

    print(f"\nprovenance violations this run: {asker.violations}")
    print("=" * 78)
