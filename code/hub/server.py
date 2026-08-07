"""Hub orchestrator — the Copilot+ PC side.

Subscribes to MQTT, fuses device state into a snapshot, runs the deterministic
rules, narrates findings via the local LLM, and pushes everything to the dashboard
and phone over WebSocket.

Degrades gracefully by design: a dead broker, a bad payload, or a dead LLM must
never take the dashboard down. Every handler is wrapped.

Run:  python hub/server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import rules
from llm import LLMClient, Recommendation, LLM_BASE_URL, template_narrate

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8000"))
# Room a board-side cue applies to. Single-room demo; named, not inlined.
ROOM_DEFAULT = os.environ.get("ROOM", "living")

# Env-overridable so a live demo is not waiting on a 5 s tick after every
# button press. Deployment default unchanged.
EVAL_INTERVAL_S = float(os.environ.get("EVAL_INTERVAL_S", "5"))
RECO_COOLDOWN_S = 600      # do not re-narrate the same finding for 10 min — anti-spam
POWER_HISTORY_LEN = 60     # 5 min of 5 s samples for the sparkline

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = ROOT / "dashboard"
PHONE_DIR = ROOT / "phone"
SIMULATOR_DIR = ROOT / "simulator"
ASK_DIR = ROOT / "ask"


# --------------------------------------------------------------------------
# Prop scaling — declared stand-ins for appliances we do not physically have
# --------------------------------------------------------------------------
#
# The demo props are a 10.8 W bulb and a 33 W desk fan on the plug labelled
# "ac". The 37-day history is a real household: 15.09 kWh a day, HVAC averaging
# 1930 W while running. Showing 33 W beside that reads as broken, and any saving
# computed from it rounds to nothing — the findings were coming out at $0.00.
#
# So the props stand in for appliances, the way a scale model stands in for a
# building. Three rules make that honest rather than a lie:
#
#   1. OFF by default. Nothing scales unless DEMO_SCALE_LOADS=1 is set.
#   2. The measured value is never destroyed. It stays on the record as
#      watts_measured, next to the factor that was applied.
#   3. It is labelled at every point it is used — watts_src="scaled_prop" in the
#      state, and a visible marker in the UI. A scaled figure must never be
#      readable as a meter reading.
#
# The targets are not invented. They are the mean power each bucket actually
# drew in the history file, so the live view and the billing period describe the
# same house instead of two different ones.
SCALE_LOADS = os.environ.get("DEMO_SCALE_LOADS", "0") == "1"

# load suffix -> (target watts while ON, where the target came from)
PROP_TARGETS = {
    "ac": (float(os.environ.get("DEMO_SCALE_AC_W", "1930")),
           "mean HVAC power in the 37-day history (157.12 kWh over 81.4 h)"),
    "lights": (float(os.environ.get("DEMO_SCALE_LIGHTS_W", "526")),
               "mean lights/fan power in the 37-day history (196.1 kWh over 372.5 h)"),
}
# Nominal draw of the actual prop, used to derive the factor. Measured, not
# guessed: the fan reads ~32.6 W and the KL120 ~10.8 W at its demo brightness.
PROP_NOMINAL_W = {
    "ac": float(os.environ.get("DEMO_PROP_AC_W", "32.6")),
    "lights": float(os.environ.get("DEMO_PROP_LIGHTS_W", "10.8")),
}


def _scale_prop(key: str, payload: Dict) -> Dict:
    """Scale a prop's measured watts to the appliance it stands in for.

    Multiplies rather than substitutes, so the prop's real variation still shows
    through — a fan that spins down is still visible as a dip, it just lands in
    the right order of magnitude.
    """
    if not SCALE_LOADS or "watts" not in payload:
        return payload
    name = key.rsplit("/", 1)[-1]
    target, basis = PROP_TARGETS.get(name, (None, None))
    nominal = PROP_NOMINAL_W.get(name)
    if not target or not nominal:
        return payload
    try:
        measured = float(payload.get("watts") or 0.0)
    except (TypeError, ValueError):
        return payload
    factor = target / nominal
    out = dict(payload)
    out["watts_measured"] = round(measured, 2)
    out["watts"] = round(measured * factor, 1)
    out["watts_scale"] = round(factor, 1)
    out["watts_src"] = "scaled_prop"
    out["watts_basis"] = basis
    return out


class StateStore:
    """In-memory fused state. Thread-safe enough for our single writer."""

    def __init__(self):
        self.rooms: Dict[str, Dict] = {}
        self.loads: Dict[str, Dict] = {}
        self.user: Dict = {"presence": "home", "distance_m": 0, "battery": 100, "ts": time.time()}
        self.power_history: deque = deque(maxlen=POWER_HISTORY_LEN)
        self.recos: List[Recommendation] = []
        self.last_narrated: Dict[str, float] = {}
        self.mqtt_connected = False
        self.sim_clock_offset = 0.0    # simulator can drive a virtual clock
        # Actuation bookkeeping: what the user approved and what physically happened.
        self.realized: List[Dict] = []
        self.applied_ids: set = set()
        self.actuations: List[Dict] = []
        # Tier-2 plan, when AI_PLAN=1. Empty dict = no plan this cycle,
        # which the UI renders as the plain ranked list it always had.
        self.plan: Dict = {}
        # Last guardrail refusal, and whether a person overrode it. Read by
        # /ask so "why did you refuse?" answers from what actually happened.
        self.last_refusal: Dict = {}
        # Autonomous-demo cue. The autopilot posts a control change here and the
        # simulator page applies it to its OWN widget, so a scripted run drives the
        # visible UI rather than quietly POSTing behind it. Monotonic id: the page
        # acts only on a cue it has not seen, so a re-render never replays one.
        self.demo_cue: Dict = {}          # most recent, kept for the /ask page
        # A QUEUE, not a slot. Button C fires presence+occupancy+lux back to
        # back; with one slot the page renders once, sees only the newest id and
        # silently drops the rest — then posts its own stale values over them.
        # Bounded because a page that has been closed for an hour should not
        # replay an hour of scenario when it reopens.
        self.demo_cues: deque = deque(maxlen=20)
        self._demo_cue_seq = 0
        self.lock = threading.Lock()

    # -- actuation ---------------------------------------------------------

    def book_realized(self, rec) -> None:
        """Record an approved action's saving as REALIZED rather than avoidable.

        The distinction matters on stage: "you could save $X" becomes "you saved $X".
        """
        with self.lock:
            if rec.id in self.applied_ids:
                return
            self.applied_ids.add(rec.id)
            self.realized.append({
                "reco_id": rec.id, "rule_name": rec.rule_name, "room": rec.room,
                "usd": rec.usd, "kwh": rec.kwh, "co2_kg": rec.co2_kg,
                "title": rec.title, "ts": time.time(),
            })

    def record_actuation(self, load_key: str, payload: Dict) -> None:
        """Store a physical-action confirmation from the UNO Q.

        If the confirmation says the action FAILED, un-book the saving. api_apply
        books optimistically the moment the command is published, which is what
        keeps the dashboard responsive — but a command that was published and then
        refused by the hardware has saved nothing. Leaving it booked would show
        "you saved $0.02" for a lamp still burning, which is precisely the kind of
        claim this project refuses to make elsewhere.

        `ok=False` means a real device was addressed and did not comply
        (source `kasa_error`). A declared simulation (`source="simulated"`,
        `ok=True`) is a legitimate labelled path and stays booked.
        """
        with self.lock:
            self.actuations.append({"load_key": load_key, **payload})
            self.actuations = self.actuations[-40:]

            if payload.get("ok") is not False:
                return
            reco_id = payload.get("reco_id")
            if not reco_id or reco_id not in self.applied_ids:
                return
            before = len(self.realized)
            self.realized = [r for r in self.realized if r["reco_id"] != reco_id]
            self.applied_ids.discard(reco_id)
            if before != len(self.realized):
                print(f"[actuation] UN-BOOKED {reco_id}: {load_key} reported "
                      f"ok=false via {payload.get('source')} — saving not realized")

    def realized_totals(self) -> Dict:
        with self.lock:
            return {
                "usd": sum(r["usd"] for r in self.realized),
                "kwh": sum(r["kwh"] for r in self.realized),
                "co2_kg": sum(r["co2_kg"] for r in self.realized),
                "count": len(self.realized),
            }

    # -- ingest ------------------------------------------------------------

    def update_room(self, room: str, payload: Dict) -> None:
        with self.lock:
            prev = self.rooms.get(room, {})
            now = payload.get("ts", time.time())

            # Track when the room was last occupied — R1 needs this.
            last_occ = prev.get("last_occupied_ts", now)
            if payload.get("occupancy"):
                last_occ = now

            # Track how long lux has been above the daylight threshold — R3.
            # A backwards or large forwards clock jump (the simulator moves the
            # virtual clock between beats) resets the window rather than billing
            # the gap as waste.
            lux_high_since = prev.get("lux_high_since", now)
            prev_ts = prev.get("ts", now)
            if payload.get("lux", 0) <= rules.DAYLIGHT_LUX_THRESHOLD or abs(now - prev_ts) > 900:
                lux_high_since = now

            # Track temperature movement for the R4 open-window heuristic.
            temp_drop = prev.get("temp_drop_c", 0.0)
            if "temp_c" in payload and "temp_c" in prev:
                temp_drop = prev["temp_c"] - payload["temp_c"]

            self.rooms[room] = {**prev, **payload,
                                "last_occupied_ts": last_occ,
                                "lux_high_since": lux_high_since,
                                "temp_drop_c": temp_drop}

    def update_load(self, key: str, payload: Dict) -> None:
        with self.lock:
            payload = _scale_prop(key, payload)
            prev = self.loads.get(key, {})
            now = payload.get("ts", time.time())
            on_since = prev.get("on_since", now)
            if prev.get("state") != "on" and payload.get("state") == "on":
                on_since = now       # rising edge
            # Re-anchor across a clock discontinuity so runtime stays plausible.
            if on_since > now or (now - on_since) > 12 * 3600:
                on_since = now
            self.loads[key] = {**prev, **payload, "on_since": on_since}

    def update_user(self, payload: Dict) -> None:
        with self.lock:
            prev = self.user
            # Only advance ts on an actual presence transition, so "how long away"
            # measures the transition, not the last heartbeat.
            ts = prev.get("ts", time.time())
            if payload.get("presence") != prev.get("presence"):
                ts = payload.get("ts", time.time())
            self.user = {**prev, **payload, "ts": ts}

    # -- derive ------------------------------------------------------------

    def total_watts(self) -> float:
        return sum(l.get("watts", 0.0) for l in self.loads.values() if l.get("state") == "on")

    def sample_power(self) -> None:
        with self.lock:
            self.power_history.append({"ts": time.time(), "watts": self.total_watts()})

    def snapshot(self) -> Dict:
        with self.lock:
            return {
                "rooms": json.loads(json.dumps(self.rooms)),
                "loads": json.loads(json.dumps(self.loads)),
                "user": dict(self.user),
                "now": time.time() + self.sim_clock_offset,
            }

    def latest_recos(self, limit: int = 12) -> List["Recommendation"]:
        """Newest-first, one card per finding id.

        STORE.recos is an append-only history: RECO_COOLDOWN_S throttles how often
        a finding is re-narrated, but once it lapses the SAME id is appended again.
        Rendering the raw tail therefore showed the same recommendation as three
        separate cards. Keep the history intact for the audit trail; collapse it
        here so each distinct finding surfaces once, with its most recent wording.
        """
        with self.lock:
            seen, out = set(), []
            for rec in reversed(self.recos):
                if rec.id in seen:
                    continue
                seen.add(rec.id)
                out.append(rec)
                if len(out) >= limit:
                    break
            return out

    def public_state(self) -> Dict:
        snap = self.snapshot()
        now_dt = datetime.fromtimestamp(snap["now"])
        rate, period = _rate(now_dt)
        return {
            **snap,
            "last_refusal": self.last_refusal,
            "total_watts": self.total_watts(),
            "power_history": list(self.power_history),
            "tariff": {"rate": rate, "period": period,
                       "clock": now_dt.strftime("%H:%M")},
            "mqtt_connected": self.mqtt_connected,
            "recos": [r.to_dict() for r in self.latest_recos(12)],
            "plan": self.plan,
            "demo_cue": self.demo_cue,
            "demo_cues": list(self.demo_cues),
            "applied_ids": sorted(self.applied_ids),
            "realized": self.realized_totals(),
            "actuations": self.actuations[-8:][::-1],
        }


def _rate(dt: datetime):
    from energy_model import rate_at
    return rate_at(dt)


STORE = StateStore()
# load_key -> when we last auto-acted on it. A COOLDOWN, not a permanent set.
# It was a set, which meant the hub would auto-switch a given load exactly once
# in its whole lifetime: after the first demo run, pressing the reset button and
# going away again did nothing at all, with the finding sitting right there on
# the dashboard. A demo has to be repeatable without restarting the server.
AUTO_ACTED: Dict[str, float] = {}
AUTO_COOLDOWN_S = float(os.environ.get("AUTO_COOLDOWN_S", "30"))
# Button C sets the scene in several steps - clear findings, presence home,
# occupancy true, lux, then switch both devices back on. With a short grace and
# a fast eval tick the rules can fire in the gap BEFORE occupancy has
# propagated, so the lights get auto-killed a second after the reset turned
# them on. Hold auto-actuation off while the scene settles.
RESET_SETTLE_S = float(os.environ.get("RESET_SETTLE_S", "12"))
LAST_RESET_AT = 0.0
LLM = LLMClient()
app = FastAPI(title="AI Home Energy Concierge")
CLIENTS: List[WebSocket] = []
LOOP: Optional[asyncio.AbstractEventLoop] = None


# --------------------------------------------------------------------------
# MQTT
# --------------------------------------------------------------------------

def on_connect(client, userdata, flags, rc, properties=None):
    STORE.mqtt_connected = (rc == 0)
    print(f"[mqtt] connected rc={rc}")
    for topic in ("home/sensors/#", "home/loads/#", "home/context/#",
              "home/actuator/#", "home/demo/cue"):
        client.subscribe(topic)
        print(f"[mqtt] subscribed {topic}")


def on_disconnect(client, userdata, rc, properties=None, reason=None):
    STORE.mqtt_connected = False
    print(f"[mqtt] disconnected rc={rc}")


def on_message(client, userdata, msg):
    """Never crash on a bad payload — log and continue."""
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        parts = msg.topic.split("/")

        if msg.topic.startswith("home/sensors/") and len(parts) >= 3:
            STORE.update_room(parts[2], payload)
        elif msg.topic.startswith("home/loads/") and len(parts) >= 4:
            STORE.update_load(f"{parts[2]}/{parts[3]}", payload)
        elif msg.topic.startswith("home/actuator/") and len(parts) >= 4:
            # Physical-action confirmation from the UNO Q.
            STORE.record_actuation(f"{parts[2]}/{parts[3]}", payload)
            print(f"[mqtt] actuator confirmed {parts[2]}/{parts[3]} -> "
                  f"{payload.get('state')} via {payload.get('source')} "
                  f"ok={payload.get('ok')}")
        elif msg.topic == "home/demo/cue":
            # A Modulino button press, relayed by the publisher. Goes through the
            # same cue path the autopilot uses, so a physical press and a scripted
            # one move the simulator's controls identically.
            control = str(payload.get("control", ""))
            if control == "reset":
                # Findings are append-only history, so after a reset the
                # dashboard still listed "lights on in an empty room" with the
                # room occupied and the lights on. Stale on camera.
                with STORE.lock:
                    STORE.recos.clear(); STORE.last_narrated.clear()
                    STORE.applied_ids.clear(); STORE.realized.clear()
                    STORE.actuations.clear(); STORE.plan = {}
                AUTO_ACTED.clear()
                global LAST_RESET_AT
                LAST_RESET_AT = time.time()
                print("[demo] reset: findings cleared")
            elif control in ("presence", "occupancy", "lux", "humidity", "temp_c", "ask"):
                value = payload.get("value")
                with STORE.lock:
                    STORE._demo_cue_seq += 1
                    STORE.demo_cue = {"id": STORE._demo_cue_seq, "control": control,
                                      "value": value,
                                      "note": str(payload.get("note", ""))[:120],
                                      "ts": time.time()}
                    STORE.demo_cues.append(STORE.demo_cue)
                # A cue from the BOARD is applied here as well as broadcast.
                # Cues are normally applied BY the simulator page, which is what
                # makes its switches move — but that means a physical button
                # press does nothing at all when no browser is open. A hardware
                # button has to work whether or not someone is looking at a
                # screen. The page still moves its control on the broadcast, so
                # both end up agreeing; the values are identical, so applying
                # twice is idempotent.
                #
                # Deliberately NOT done for the HTTP /api/demo/cue path: there,
                # "did the state change?" is the proof that the PAGE acted, and
                # applying it here would make that check meaningless.
                try:
                    if control == "presence":
                        STORE.update_user({"presence": str(value),
                                           "distance_m": 2400 if value == "away" else 0,
                                           "ts": time.time()})
                    elif control in ("occupancy", "lux", "humidity", "temp_c"):
                        STORE.update_room(ROOM_DEFAULT, {control: value, "ts": time.time()})
                except Exception as exc:
                    print(f"[demo] could not apply board cue: {exc}")
                print(f"[demo] cue from board: {control} -> {value}")
        elif msg.topic == "home/context/user":
            STORE.update_user(payload)
        elif msg.topic == "home/context/clock":
            # pin_hhmm is resolved HERE, not by the sender. The board's clock is
            # UTC and the hub's is local, so an offset computed on the board
            # landed the demo seven hours out — asked for 18:30 it produced
            # 11:30, which silently swapped the on-peak rate for super-off-peak
            # and changed every dollar on screen. Whoever renders the clock has
            # to own the conversion. offset_s is still accepted for anything
            # that genuinely means "shift by N seconds".
            pin = payload.get("pin_hhmm")
            if pin:
                try:
                    hh, mm = (int(x) for x in str(pin).split(":", 1))
                    now = time.time()
                    lt = time.localtime(now)
                    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                          hh, mm, 0, 0, 0, lt.tm_isdst))
                    STORE.sim_clock_offset = target - now
                    print(f"[clock] pinned to {pin} local "
                          f"(offset {STORE.sim_clock_offset:+.0f}s)")
                except Exception as exc:
                    print(f"[clock] bad pin_hhmm {pin!r}: {exc}")
            else:
                STORE.sim_clock_offset = payload.get("offset_s", 0.0)
    except Exception as exc:
        print(f"[mqtt] bad payload on {msg.topic}: {exc}")


def start_mqtt() -> Optional["mqtt.Client"]:
    if mqtt is None:
        print("[mqtt] paho-mqtt not installed — running dashboard-only")
        return None
    try:
        client = mqtt.Client(client_id="hub-orchestrator", clean_session=True)
    except Exception:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hub-orchestrator")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    try:
        client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    except Exception as exc:
        print(f"[mqtt] connect failed ({exc}) — will retry in background")
    client.loop_start()
    return client


MQTT_CLIENT = None


# --------------------------------------------------------------------------
# Evaluation loop
# --------------------------------------------------------------------------

def evaluation_tick() -> List[Recommendation]:
    """Run rules, narrate genuinely new findings, publish. Returns new recos."""
    STORE.sample_power()
    snap = STORE.snapshot()
    now_dt = datetime.fromtimestamp(snap["now"])

    try:
        findings = rules.evaluate(snap, now_dt)
    except Exception as exc:
        print(f"[eval] rules failed: {exc}")
        return []

    # --- AUTO-ACT on low-risk loads (AI_AUTO_LIGHTS=1) -----------------------
    # ORDER IS LOAD-BEARING: this runs BEFORE plan synthesis and narration.
    #
    # Both of those make model calls measured in seconds. The planner caches on
    # the frozenset of finding ids, so it only calls out when the finding set
    # CHANGES — which is precisely the tick where R1 first fires. Running the
    # actuation after it meant the one tick that mattered paid the full model
    # cost before it was allowed to switch a bulb: measured ~9.6 s from "room is
    # empty" to the command, against a Kasa switch that takes 0.6 s.
    #
    # The decision does not depend on the plan. Acting first is also the honest
    # shape of the three-tier story — the deterministic rule fires in
    # microseconds and the model explains afterwards, rather than the
    # explanation gating the action.
    # Beat 2 of the demo: the UNO Q detects an empty room and the lights go off
    # by themselves, while the A/C still waits for a human. That split is the
    # point — autonomy is granted per-load by RISK, not granted wholesale.
    #
    # Scoped hard, because this is the one place the system acts without being
    # asked:
    #   * lights ONLY. Never HVAC, never anything comfort-affecting.
    #   * still goes through _guardrail_allows, so R7 governs it exactly as it
    #     governs a human approval.
    #   * booked as approved_by="auto" so the audit trail never implies a person
    #     pressed something.
    # If you enable this, say so on stage. "A human approves every action" stops
    # being true the moment it is on.
    if (os.environ.get("AI_AUTO_LIGHTS", "0") == "1"
            and time.time() - LAST_RESET_AT > RESET_SETTLE_S):
        for f in findings:
            # DETERMINISTIC rules only. The learned tier detects, it does not act.
            #
            # The edge classifier scores the whole home state, and its two
            # heaviest positive weights are hvac_on (+3.48) and lights_on
            # (+2.36) against occupancy at only -3.78. Past ~22:00 the clock
            # terms tip the sum over ANOMALY_THRESHOLD for the ordinary beat-1
            # steady state -- home, occupied, lights + A/C on -- which scores
            # 0.83 at 23:00 and 0.97 at 03:00. It then attached to whichever
            # load was heaviest (_biggest_live_load) and, when that happened to
            # be the bulb, switched off the scene button C had just set up,
            # seconds after the reset settled and with nobody having pressed A.
            #
            # That is the model behaving as trained -- this IS its motivating
            # case (anomaly.py:240) -- so the threshold is not the thing to
            # change. What is wrong is granting a LEARNED score the authority to
            # actuate. Autonomy here is granted per-load by risk (see below);
            # it should equally be granted per-DETECTOR by confidence. A rule
            # that fired is a fact, and R1 is the one beat 2 demonstrates. A
            # score of 0.83 is an opinion, and an opinion belongs on the
            # dashboard where a human can weigh it.
            #
            # The learned finding is unaffected: it still surfaces, still
            # carries its score and provenance, still gets narrated. It just
            # cannot move a switch by itself.
            if f.rule_name != "unoccupied_lights_on":
                continue
            if f.load_key.rsplit("/", 1)[-1] != "lights":
                continue
            # Dedupe by LOAD, not by finding id. R1 (unoccupied) and R3
            # (daylight) both target living/lights, so keying on the finding
            # sent two switch-off commands for one bulb — harmless but noisy,
            # and it would read as a stutter on camera.
            if f.id in STORE.applied_ids:
                continue
            # Short cooldown only, to stop a re-fire in the seconds between
            # publishing the command and the load state coming back "off".
            # The state check below is what really prevents repeats.
            if time.time() - AUTO_ACTED.get(f.load_key, 0) < AUTO_COOLDOWN_S:
                continue
            # Nothing to do if it is already off. This is also what stops the
            # loop re-firing every 5 s once the bulb has actually gone out.
            if (STORE.loads.get(f.load_key) or {}).get("state") != "on":
                continue
            allowed, reason = _guardrail_allows(snap, f, f.load_key, "off", now_dt)
            if not allowed:
                print(f"[auto] refused {f.load_key}: {reason}")
                continue
            AUTO_ACTED[f.load_key] = time.time()
            cmd = {"action": "off", "reco_id": f.id, "approved_by": "auto",
                   "ts": time.time()}
            if MQTT_CLIENT is not None:
                try:
                    MQTT_CLIENT.publish(f"home/command/{f.load_key}", json.dumps(cmd))
                    print(f"[auto] {f.load_key} -> off  (rule {f.rule_name}, "
                          f"no human in the loop by design)")
                except Exception as exc:
                    print(f"[auto] publish failed: {exc}")

    # TIER 2: one plan-synthesis call per CHANGE of the finding set (the planner
    # caches on frozenset of ids), not per cycle and not per finding. Strictly
    # cheaper than the per-finding narration below, which is left completely
    # intact underneath as the fallback. Runs after the actuation above — see
    # the note there.
    if os.environ.get("AI_PLAN", "0") == "1":
        try:
            import planner
            STORE.plan = planner.PLANNER.plan(findings).to_dict()
        except Exception as exc:
            print(f"[eval] planner unavailable: {exc}")

    fresh: List[Recommendation] = []
    now = time.time()

    # Severity first, so the critical finding gets its card before the merely
    # serious one. Narration is a model call per finding taking seconds, and
    # dict order meant the A/C — the finding beat 5 is built on — queued behind
    # the lights. Approve was pressed into that gap and got 404 unknown reco_id.
    _RANK = {"critical": 0, "serious": 1, "warning": 2, "info": 3}
    findings = sorted(findings, key=lambda f: _RANK.get(f.severity, 9))

    # At most one LLM narration per tick. The others get their deterministic
    # card IMMEDIATELY and are upgraded on a later tick.
    #
    # The card is what /api/apply resolves and what a person clicks, so a
    # finding that exists without one is a live action with no way to take it —
    # which on camera is an Approve button that 404s. A template card is worth
    # far more than a missing one, and the wording improves a second later.
    llm_budget = 1
    for f in findings:
        # Already acted on — do not re-narrate a finding the user has resolved.
        if f.id in STORE.applied_ids:
            continue
        last = STORE.last_narrated.get(f.id, 0)
        already_carded = any(r.id == f.id for r in STORE.recos)
        if already_carded and now - last < RECO_COOLDOWN_S:
            continue          # cooldown — this is what stops the demo flooding
        STORE.last_narrated[f.id] = now

        try:
            if llm_budget > 0:
                llm_budget -= 1
                rec = LLM.narrate(f)
            else:
                rec = template_narrate(f)
        except Exception as exc:
            print(f"[eval] narration failed hard: {exc}")
            continue

        STORE.recos.append(rec)
        fresh.append(rec)
        print(f"[reco] ({rec.narrated_by}) [{rec.severity}] {rec.title}  ${rec.usd:.2f}")

        if MQTT_CLIENT is not None:
            try:
                MQTT_CLIENT.publish("home/reco", json.dumps(rec.to_dict()))
            except Exception as exc:
                print(f"[mqtt] publish failed: {exc}")

    return fresh


async def broadcast(message: Dict) -> None:
    dead = []
    for ws in list(CLIENTS):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in CLIENTS:
            CLIENTS.remove(ws)


async def eval_loop() -> None:
    while True:
        try:
            fresh = await asyncio.to_thread(evaluation_tick)
            await broadcast({"type": "state", "data": STORE.public_state()})
            for rec in fresh:
                await broadcast({"type": "reco", "data": rec.to_dict()})
        except Exception as exc:
            print(f"[loop] {exc}")
        await asyncio.sleep(EVAL_INTERVAL_S)


# --------------------------------------------------------------------------
# HTTP / WS
# --------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    global MQTT_CLIENT, LOOP
    LOOP = asyncio.get_running_loop()
    MQTT_CLIENT = start_mqtt()
    asyncio.create_task(eval_loop())
    print(_banner())


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _mdns_host() -> str:
    """The .local name, which does NOT change when the network does.

    An IP printed at startup is stale the moment you move between work Wi-Fi and
    home Wi-Fi — same SSID or not — and every bookmark, QR code and note made
    from it breaks. The mDNS name is resolved on the local link, so it follows
    the machine across networks. Verified resolving from the UNO Q.
    """
    name = socket.gethostname().split(".")[0]
    return f"{name}.local"


def _lan_ips() -> List[str]:
    """Every usable IPv4 address, best guess first.

    Printing only one is what makes this confusing on a box with five adapters:
    the address with the default route is not necessarily the one a phone on the
    LAN can reach. 169.254.* is link-local autoconfig — an adapter that failed to
    get a lease — and is never the right answer, so it is filtered out.
    """
    primary = _lan_ip()
    out = [primary] if not primary.startswith(("127.", "169.254.")) else []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith(("127.", "169.254.")):
                out.append(ip)
    except Exception:
        pass
    return out or ["127.0.0.1"]


def _banner() -> str:
    host = _mdns_host()
    ips = _lan_ips()
    base = f"http://{host}:{HTTP_PORT}"
    # "Phone PWA" was wrong: code/phone/index.html has no manifest and no service
    # worker, so it does not install and does not work offline. It is a plain
    # mobile page. Calling it a PWA in the one place a judge is most likely to
    # read is exactly the kind of claim this project does not make.
    pages = [("Dashboard", "/"), ("Ask (NPU Q&A)", "/ask"),
             ("Sensors / Approve", "/simulator"), ("Phone", "/phone")]
    rows = "\n".join(f"  {label:<15}{base}{path}" for label, path in pages)
    alt = "\n".join(f"                 http://{ip}:{HTTP_PORT}/" for ip in ips)
    return f"""
{'=' * 72}
  AI HOME ENERGY CONCIERGE — hub running
{'=' * 72}
  These URLs do NOT change when you move between networks — bookmark them:

{rows}

  ANDROID cannot resolve .local — it has no system mDNS resolver. For a phone,
  open this on the PC and scan; nothing to type, and it follows the network:

     http://{host}:{HTTP_PORT}/qr        <- scan from the phone

  Or use an address directly (these DO change with the network):
{alt}

  Broker    : {MQTT_HOST}:{MQTT_PORT}
  LLM       : {LLM_BASE_URL}
              (LLM_ENABLED={os.environ.get('LLM_ENABLED', '1')})
{'=' * 72}
"""


@app.get("/")
async def dashboard():
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/phone")
async def phone():
    return FileResponse(PHONE_DIR / "index.html")


# --------------------------------------------------------------------------
# TIER 3: natural-language Q&A (AI_ASK=1). Additive — its own page and its own
# endpoints, so nothing here touches the simulator or the approve flow.
# --------------------------------------------------------------------------

@app.get("/ask")
async def ask_page():
    if os.environ.get("AI_ASK", "0") != "1":
        return JSONResponse({"error": "Q&A disabled; start the hub with AI_ASK=1"},
                            status_code=404)
    return FileResponse(ASK_DIR / "index.html")


@app.post("/api/demo/cue")
async def api_demo_cue(request: Request):
    """Ask the simulator page to move one of its own controls.

    Used by tools/demo_autopilot.py. The point is that a scripted demo should be
    visible: driving the hub directly would change the numbers while the
    simulator's switches sat still, which looks like the UI is broken rather
    than like the demo is running.
    """
    body = await request.json()
    control = str(body.get("control", "")).strip()
    if control not in ("presence", "occupancy", "lux", "humidity", "temp_c", "ask"):
        return JSONResponse({"error": f"unknown control {control!r}"}, status_code=400)
    with STORE.lock:
        STORE._demo_cue_seq += 1
        STORE.demo_cue = {"id": STORE._demo_cue_seq, "control": control,
                          "value": body.get("value"),
                          "note": str(body.get("note", ""))[:120],
                          "ts": time.time()}
        STORE.demo_cues.append(STORE.demo_cue)
    await broadcast({"type": "state", "data": STORE.public_state()})
    return JSONResponse({"ok": True, "cue": STORE.demo_cue})


@app.get("/api/ask/suggestions")
async def ask_suggestions():
    try:
        import ask as ask_mod
        return JSONResponse(ask_mod.SUGGESTED_QUESTIONS)
    except Exception:
        return JSONResponse([])


@app.post("/api/ask")
async def api_ask(request: Request):
    """Stream an answer as newline-delimited JSON.

    Streaming is not decoration: first token lands in ~0.15 s while the full
    answer takes ~2.5 s, so the reply reads as immediate. The final line carries
    the provenance verdict the page renders as a badge.
    """
    if os.environ.get("AI_ASK", "0") != "1":
        return JSONResponse({"error": "Q&A disabled"}, status_code=404)
    try:
        import ask as ask_mod
    except Exception as exc:
        return JSONResponse({"error": f"ask unavailable: {exc}"}, status_code=503)

    body = await request.json()
    question = str(body.get("question", "")).strip()
    if not question:
        return JSONResponse({"error": "question is required"}, status_code=400)

    state = STORE.public_state()

    def gen():
        for chunk in ask_mod.ASKER.stream(question, state):
            yield json.dumps(chunk) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/simulator")
async def simulator():
    """Sensor simulator — stands in for hardware we do not have.

    Served from the hub on purpose: same-origin means no CORS, and the page
    falls back to location.origin, so it needs no configuration at all when
    opened at http://<hub>:8000/simulator from a phone or laptop.

    It drives the SAME endpoints a real sensor would (/api/sensor, /api/load,
    /api/presence) rather than a private back door, so the rules engine cannot
    tell the difference — which is exactly why the values it sends must stay
    labelled as simulated wherever they surface.
    """
    return FileResponse(SIMULATOR_DIR / "index.html")


@app.get("/api/state")
async def api_state():
    return JSONResponse(STORE.public_state())


@app.get("/qr")
async def qr_page():
    """Scannable links to every page, using the CURRENT LAN address.

    The .local name this hub prints does not work on Android: unlike iOS, macOS,
    Windows and avahi, Android has no system mDNS resolver behind the normal DNS
    path, so Chrome cannot look it up. There is therefore no hostname that can be
    typed on an Android phone, and the IP is the only thing that works — but the
    IP changes with the network, which is what made it painful in the first
    place.

    A QR sidesteps the whole problem: the address can change every day and
    nobody has to read it, type it, or notice that it changed. Open this page on
    the PC and scan from the phone.

    Rendered server-side with segno (pure Python, no JS, no CDN) so it works on
    a network with no internet — which is the situation this is for.
    """
    ips = _lan_ips()
    ip = ips[0]
    pages = [("Phone", "/phone", "the approval feed — scan this one"),
             ("Simulator", "/simulator", "sensors + approve"),
             ("Dashboard", "/", "live power and findings"),
             ("Ask", "/ask", "natural-language Q&A")]
    try:
        import segno
    except ImportError:
        return HTMLResponse(
            "<h1>QR unavailable</h1><p><code>pip install segno</code> "
            f"(pure Python, no deps).</p><p>Meanwhile, type: "
            + "".join(f"<br><code>http://{ip}:{HTTP_PORT}{p}</code>" for _, p, _ in pages)
            + "</p>", status_code=200)

    cards = []
    for label, path, note in pages:
        url = f"http://{ip}:{HTTP_PORT}{path}"
        svg = segno.make(url, error="m").svg_inline(scale=5, dark="#111")
        cards.append(
            f'<div class="c"><h2>{label}</h2><div class="n">{note}</div>'
            f'{svg}<div class="u">{url}</div></div>')

    others = "".join(f"<li><code>http://{o}:{HTTP_PORT}/phone</code></li>"
                     for o in ips[1:]) or "<li>none</li>"
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8">
<title>Scan to open on your phone</title>
<style>
 body{{font:15px system-ui,sans-serif;background:#0f1115;color:#e8e8ea;margin:0;padding:28px}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#9aa0aa;margin-bottom:22px}}
 .g{{display:flex;flex-wrap:wrap;gap:20px}}
 .c{{background:#fff;color:#111;border-radius:12px;padding:16px;text-align:center;width:230px}}
 .c h2{{font-size:15px;margin:0 0 2px}} .n{{font-size:11px;color:#666;margin-bottom:8px}}
 .u{{font-size:10px;color:#444;margin-top:8px;word-break:break-all}}
 .warn{{margin-top:26px;padding:12px 14px;background:#241c10;border-left:3px solid #b8860b;
        color:#e0d3b8;font-size:13px;max-width:760px;line-height:1.5}}
 code{{background:#1b1e24;padding:1px 5px;border-radius:4px;color:#cfd4dc}}
</style>
<h1>Scan to open on your phone</h1>
<div class="sub">This PC is <code>{ip}</code> right now. Scan and the address is
whatever it currently is — nothing to type, nothing to remember.</div>
<div class="g">{''.join(cards)}</div>
<div class="warn">
<b>Do not bookmark these on the phone.</b> The IP is assigned by whichever
network you are on, so a bookmark made at the office will not open at home.
Re-open <code>/qr</code> on the PC and scan again after switching networks.<br><br>
<b>The <code>.local</code> name will not work on Android</b> — it has no system
mDNS resolver. It does work on iPhone, Mac, Windows and the UNO Q.<br><br>
Other addresses this PC has: <ul>{others}</ul>
Phone and PC must be on the <b>same network</b>, and guest Wi-Fi usually blocks
device-to-device traffic entirely.
</div>""")


@app.get("/api/pacing")
async def api_pacing():
    """The demo-pacing values this hub is ACTUALLY running with.

    These are process-lifetime environment overrides, so they are invisible from
    outside — a hub started without them looks identical to one started with
    them until you sit and watch a beat take ten minutes instead of five
    seconds. `run_demo.ps1` used to infer "started correctly" from /ask
    answering, which only proves AI_ASK was set and says nothing about pacing.
    Report the numbers so the launcher can verify rather than assume.
    """
    return JSONResponse({
        "DEMO_GRACE_S": rules.UNOCCUPIED_GRACE_S,
        "DEMO_AWAY_GRACE_S": rules.AWAY_HVAC_GRACE_S,
        "AUTO_COOLDOWN_S": AUTO_COOLDOWN_S,
        "EVAL_INTERVAL_S": EVAL_INTERVAL_S,
        "RESET_SETTLE_S": RESET_SETTLE_S,
        "AI_AUTO_LIGHTS": os.environ.get("AI_AUTO_LIGHTS", "0") == "1",
    })


@app.get("/api/recos")
async def api_recos():
    return JSONResponse([r.to_dict() for r in STORE.latest_recos(25)])


@app.post("/api/presence")
async def api_presence(body: Dict):
    """Manual presence override — the demo-safe path when geolocation is blocked."""
    presence = body.get("presence")
    if presence not in ("home", "away"):
        return JSONResponse({"error": "presence must be 'home' or 'away'"}, status_code=400)
    STORE.update_user({"presence": presence,
                       "distance_m": int(body.get("distance_m", 0 if presence == "home" else 2400)),
                       "battery": int(body.get("battery", STORE.user.get("battery", 100))),
                       "ts": time.time()})
    print(f"[api] presence -> {presence}")
    return JSONResponse({"ok": True, "user": STORE.user})


@app.post("/api/load")
async def api_load(body: Dict):
    """Toggle a load by hand — lets us drive the demo with no hardware."""
    key = body.get("key")
    state = body.get("state")
    if not key or state not in ("on", "off"):
        return JSONResponse({"error": "need key and state"}, status_code=400)
    watts = float(body.get("watts", STORE.loads.get(key, {}).get("watts", 100)))
    STORE.update_load(key, {"state": state, "watts": watts, "ts": time.time()})
    return JSONResponse({"ok": True, "loads": STORE.loads})


@app.post("/api/sensor")
async def api_sensor(body: Dict):
    """Inject a sensor reading over REST.

    Lets the whole pipeline be driven with no broker and no hardware at all —
    the last-resort demo fallback, and how the smoke test exercises the rules.
    """
    room = body.get("room", "living")
    payload = {k: v for k, v in body.items() if k != "room"}
    payload.setdefault("ts", time.time())
    STORE.update_room(room, payload)
    return JSONResponse({"ok": True, "rooms": STORE.rooms})


@app.post("/api/apply")
async def api_apply(body: Dict):
    """Approve a recommendation and command the physical actuator.

    This is the closing half of the loop (Archetype E): sense -> reason -> recommend
    -> USER APPROVES -> physically act -> confirm -> book the saving as realized.

    The comfort guardrail (R7) is enforced here as a PRE-FLIGHT GATE, not merely as
    advice. A command the guardrail would suppress is refused outright, so the system
    will not execute its own recommendation when doing so would make the home
    uncomfortable.
    """
    reco_id = body.get("reco_id")
    if not reco_id:
        return JSONResponse({"error": "reco_id required"}, status_code=400)

    rec = next((r for r in reversed(STORE.recos) if r.id == reco_id), None)
    if rec is None:
        return JSONResponse({"error": f"unknown reco_id {reco_id!r}"}, status_code=404)

    load_name = _load_from_rule(rec)
    if not load_name:
        # Cannot determine which device this concerns. Refuse rather than act on
        # a guess — see the note in _load_from_rule().
        return JSONResponse(
            {"error": "cannot determine which load this recommendation concerns",
             "reco_id": reco_id, "rule_name": rec.rule_name,
             "hint": "the Finding must carry load_key, or rule_name must be mapped"},
            status_code=422)
    load_key = f"{rec.room}/{load_name}"
    action = body.get("action", "off")
    if action not in ("on", "off"):
        return JSONResponse({"error": "action must be 'on' or 'off'"}, status_code=400)

    # --- R7 pre-flight safety gate -------------------------------------------
    snap = STORE.snapshot()
    now_dt = datetime.fromtimestamp(snap["now"])
    allowed, reason = _guardrail_allows(snap, rec, load_key, action, now_dt)
    override = bool(body.get("override"))
    if not allowed and not override:
        print(f"[apply] REFUSED {load_key} -> {action}: {reason}")
        return JSONResponse({"ok": False, "refused": True, "reason": reason,
                             "gate": "comfort_guardrail",
                             # Tell the caller the door exists. The UI renders
                             # this as a second, deliberately unfriendly button.
                             "override_available": True,
                             "override_hint": "resend with override:true to act anyway"},
                            status_code=409)
    if not allowed and override:
        # A person overruled the guardrail. This is allowed — R7 protects the
        # occupant's comfort, and the occupant is entitled to decide they would
        # rather have the saving. What is NOT allowed is doing it quietly: the
        # refusal that was overridden is recorded verbatim, the action is booked
        # as approved_by="human_override" rather than plain approval, and it is
        # never available to the autonomous path, which has no one to be
        # accountable for it.
        print(f"[apply] OVERRIDE {load_key} -> {action}: user overruled — {reason}")
        STORE.last_refusal = {"load_key": load_key, "reason": reason,
                              "overridden": True, "ts": time.time()}

    # --- publish the command --------------------------------------------------
    # "human_override" is a distinct actor from "user". A saving taken by
    # overruling the comfort guardrail is not the same event as one the system
    # was happy to make, and the audit trail must not flatten the two.
    approver = body.get("approved_by", "user")
    if not allowed and override:
        approver = "human_override"
    command = {"action": action, "reco_id": reco_id,
               "approved_by": approver, "ts": time.time()}
    published = False
    if MQTT_CLIENT is not None:
        try:
            MQTT_CLIENT.publish(f"home/command/{load_key}", json.dumps(command))
            published = True
        except Exception as exc:
            print(f"[apply] publish failed: {exc}")

    # Reflect intent locally so the UI responds even with no board attached.
    watts = STORE.loads.get(load_key, {}).get("watts", 0.0)
    STORE.update_load(load_key, {"state": action,
                                 "watts": watts if action == "on" else 0.0,
                                 "ts": time.time()})

    STORE.book_realized(rec)
    print(f"[apply] {load_key} -> {action} (reco {reco_id}), "
          f"realized ${rec.usd:.3f}, published={published}")

    await broadcast({"type": "state", "data": STORE.public_state()})
    return JSONResponse({"ok": True, "command": command, "load_key": load_key,
                         "published": published,
                         "realized_usd": round(rec.usd, 4)})


def _load_from_rule(rec) -> str:
    """Map a recommendation back to the load name it concerns."""
    by_rule = {
        "unoccupied_lights_on": "lights",
        "daylight_waste": "lights",
        "away_with_hvac_on": "ac",
        "hvac_with_window_open": "ac",
        "phantom_standby": "standby",
        "peak_hour_heavy_load": "dryer",
    }
    mapped = by_rule.get(rec.rule_name)
    if mapped:
        return mapped
    # Unknown rule_name. The old default was "lights", which was silently WRONG
    # for any detector not in the table above: a learned finding on the A/C
    # resolved to living/lights, so approving it would switch the wrong device
    # AND skip the comfort guardrail, which keys off the load name. The Finding
    # has always known its own load; prefer that over guessing from the rule.
    # Behaviour for the six mapped rules is unchanged.
    lk = getattr(rec, "load_key", "") or ""
    if lk:
        return lk.split("/")[-1]

    # Nothing left to go on. Return "" and let the caller REFUSE, rather than
    # guessing a device.
    #
    # This used to `return "lights"`. That line is what made the learned-A/C
    # incident possible, and deleting only the specific trigger would have left
    # the mechanism in place for whatever touches Recommendation construction
    # next — Recommendation.load_key defaults to "", so a new construction site,
    # a deserialised object, or a third-party rule inherits the silent guess.
    # An actuator that picks a plausible-looking device when it does not know
    # which device is meant is the single most dangerous shape of code in this
    # project. Fail loudly instead; a 422 is recoverable, switching the wrong
    # appliance in someone's home is not.
    print(f"[apply] REFUSING: cannot determine the load for reco {rec.id!r} "
          f"(rule_name={rec.rule_name!r}, load_key empty)")
    return ""


def _guardrail_allows(snap: Dict, rec, load_key: str, action: str, now_dt):
    """Return (allowed, reason). Refuses actions R7 would have suppressed."""
    if action == "on":
        return True, ""          # turning something on never harms comfort

    load_name = load_key.split("/")[-1]
    room = snap.get("rooms", {}).get(rec.room, {})
    temp = room.get("temp_c")

    if temp is None:
        return True, ""

    if load_name == "ac" and temp > rules.COMFORT_MAX_C:
        return False, (f"Refused: {rec.room} is {temp:.1f}°C, above the "
                       f"{rules.COMFORT_MAX_C:.0f}°C comfort limit — turning the "
                       f"A/C off would make the room uncomfortable.")
    if load_name == "heater" and temp < rules.COMFORT_MIN_C:
        return False, (f"Refused: {rec.room} is {temp:.1f}°C, below the "
                       f"{rules.COMFORT_MIN_C:.0f}°C comfort limit — turning the "
                       f"heater off would make the room uncomfortable.")
    return True, ""


@app.post("/api/deep_report")
async def api_deep_report():
    try:
        from cloud_report import generate_report
        report = await asyncio.to_thread(generate_report, STORE.public_state())
        return JSONResponse(report)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    CLIENTS.append(ws)
    try:
        await ws.send_json({"type": "state", "data": STORE.public_state()})
        while True:
            await ws.receive_text()   # keepalive; we do not expect real input
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if ws in CLIENTS:
            CLIENTS.remove(ws)


# Line-buffer stdout. Redirected to a file it block-buffers, which hid every
# diagnostic this process printed — including the [auto] actuation lines —
# until the buffer happened to fill. A log you cannot read during the run is
# not a log.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="warning")
