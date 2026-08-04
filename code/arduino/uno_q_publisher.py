#!/usr/bin/env python3
"""UNO Q Linux-side publisher — runs on the Qualcomm Dragonwing (Debian).

This is the architectural differentiator: the UNO Q is a network peer that does
its own edge processing and speaks MQTT over Wi-Fi, not a USB sensor cable.

Edge intelligence performed HERE rather than on the hub:
  - median-of-5 smoothing on lux and temperature (kills sensor spikes)
  - occupancy state machine with a grace hold (stops PIR flapping)
  - change-triggered publishing with a heartbeat (cuts broker traffic sharply)

Run:  ROOM=living MQTT_HOST=192.168.1.50 python3 uno_q_publisher.py
      python3 uno_q_publisher.py --fake-serial      # no MCU attached
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import queue
import random
import signal
import statistics
import sys
import time
from typing import Dict, List, Optional

try:
    import serial
except ImportError:
    serial = None

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("paho-mqtt required: pip3 install paho-mqtt", file=sys.stderr)
    sys.exit(1)

ROOM = os.environ.get("ROOM", "living")
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.1.50")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
BAUD = 115200

# Publish thresholds — the point of edge filtering.
LUX_CHANGE_PCT = 0.15         # 15% relative change is material
TEMP_CHANGE_C = 0.3
HEARTBEAT_S = 10              # publish at least this often regardless
OCC_GRACE_S = 30              # hold "occupied" this long after last motion
SMOOTH_WINDOW = 5             # median-of-5

LOADS_FILE = "/tmp/loads.json"    # flip loads during the demo with no rewiring
LOADS_DEFAULT = {"lights": {"state": "off", "watts": 240},
                 "ac": {"state": "off", "watts": 1100}}

RUNNING = True


def _stop(signum, frame):
    global RUNNING
    RUNNING = False
    print("[uno_q] SIGTERM received, shutting down", flush=True)


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def find_port() -> Optional[str]:
    """The device node differs by image — try the likely ones in order."""
    candidates: List[str] = ["/dev/ttyACM0", "/dev/ttyACM1", "/dev/ttyUSB0"]
    candidates += sorted(glob.glob("/dev/serial/by-id/*"))
    for path in candidates:
        try:
            s = serial.Serial(path, BAUD, timeout=1)
            s.close()
            print(f"[uno_q] MCU serial found at {path}", flush=True)
            return path
        except Exception:
            continue
    return None


class Smoother:
    """Median-of-N — rejects single-sample sensor spikes."""

    def __init__(self, n: int = SMOOTH_WINDOW):
        self.n = n
        self.buf: List[float] = []

    def push(self, v: float) -> float:
        self.buf.append(v)
        if len(self.buf) > self.n:
            self.buf.pop(0)
        return statistics.median(self.buf)


class OccupancyFSM:
    """State machine with a grace hold, so PIR gaps do not flap the state."""

    def __init__(self, grace_s: float = OCC_GRACE_S):
        self.grace = grace_s
        self.last_motion = 0.0
        self.state = False

    def push(self, raw_motion: bool) -> bool:
        now = time.time()
        if raw_motion:
            self.last_motion = now
        self.state = (self.last_motion > 0) and ((now - self.last_motion) < self.grace)
        return self.state


class Publisher:
    def __init__(self, host: str, port: int):
        try:
            self.c = mqtt.Client(client_id=f"unoq-{ROOM}")
        except Exception:
            self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"unoq-{ROOM}")
        self.c.on_connect = self._on_connect
        self.c.on_disconnect = lambda *a, **k: print("[uno_q] MQTT disconnected", flush=True)
        self.host, self.port = host, port
        self.backoff = 1.0
        # Topics to (re)subscribe on every successful connect. Subscribing once at
        # startup is not enough: connect_async() has not completed yet, and any
        # subscription is also lost across a reconnect.
        self.subscriptions: List[str] = []
        self._connect()
        self.c.loop_start()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        print(f"[uno_q] MQTT connected rc={rc}", flush=True)
        for topic in self.subscriptions:
            client.subscribe(topic)
            print(f"[uno_q] subscribed {topic}", flush=True)

    def subscribe(self, topic: str, callback=None) -> None:
        """Register a subscription that survives reconnects."""
        if callback is not None:
            self.c.message_callback_add(topic, callback)
        if topic not in self.subscriptions:
            self.subscriptions.append(topic)
        self.c.subscribe(topic)   # in case we are already connected

    def _connect(self):
        """Auto-reconnect with exponential backoff."""
        while RUNNING:
            try:
                self.c.connect_async(self.host, self.port, keepalive=30)
                print(f"[uno_q] connecting to {self.host}:{self.port}", flush=True)
                self.backoff = 1.0
                return
            except Exception as exc:
                print(f"[uno_q] MQTT connect failed ({exc}); retry in {self.backoff:.0f}s", flush=True)
                time.sleep(self.backoff)
                self.backoff = min(self.backoff * 2, 30.0)

    def sensors(self, payload: Dict):
        self.c.publish(f"home/sensors/{ROOM}", json.dumps(payload))

    def load(self, name: str, state: str, watts: float):
        self.c.publish(f"home/loads/{ROOM}/{name}",
                       json.dumps({"state": state, "watts": watts, "ts": time.time()}))

    def actuator(self, name: str, state: str, reco_id: str, ok: bool, source: str):
        """Confirm a physical action back to the hub — closes the actuation loop."""
        self.c.publish(f"home/actuator/{ROOM}/{name}",
                       json.dumps({"state": state, "source": source, "reco_id": reco_id,
                                   "ok": ok, "ts": time.time()}))

    def close(self):
        self.c.loop_stop()
        self.c.disconnect()


class Actuator:
    """Executes hub commands on the MCU and reports back.

    This is the physical-action half of Archetype E. The hub decides *whether* an
    action is safe (the R7 comfort guardrail gates it there); this class only carries
    the command to the hardware and reports honestly whether it landed.
    """

    def __init__(self, pub: "Publisher", serial_handle=None):
        self.pub = pub
        self.serial = serial_handle
        self.pending: "queue.Queue" = queue.Queue()
        self.last_ack: Dict = {}

    def on_command(self, client, userdata, msg):
        """MQTT callback: home/command/<room>/<load>."""
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            parts = msg.topic.split("/")
            if len(parts) < 4:
                return
            room, load = parts[2], parts[3]
            if room != ROOM:
                return

            action = payload.get("action")
            if action not in ("on", "off"):
                print(f"[uno_q] ignoring bad action {action!r}", flush=True)
                return

            reco_id = payload.get("reco_id", "")
            print(f"[uno_q] COMMAND {load} -> {action} (reco {reco_id})", flush=True)
            self.execute(load, action, reco_id)
        except Exception as exc:
            print(f"[uno_q] bad command on {msg.topic}: {exc}", flush=True)

    def execute(self, load: str, action: str, reco_id: str) -> bool:
        """Send the command to the MCU and publish a confirmation."""
        ok = False
        source = "none"

        if self.serial is not None:
            try:
                line = f"CMD {load} {action}\n".encode("ascii")
                self.serial.write(line)
                self.serial.flush()
                ok, source = True, "mcu_serial"
                print(f"[uno_q] sent to MCU: {line!r}", flush=True)
            except Exception as exc:
                print(f"[uno_q] serial write failed: {exc}", flush=True)
                source = "serial_error"
        else:
            # No MCU attached (development / fake-serial mode). Be explicit rather
            # than silently claiming a physical action occurred.
            ok, source = True, "simulated"
            print(f"[uno_q] SIMULATED actuation {load} -> {action} "
                  f"(no MCU attached)", flush=True)

        # Reflect the new load state so the hub's power figures follow reality.
        loads = read_loads()
        if load in loads:
            loads[load]["state"] = action
            try:
                with open(LOADS_FILE, "w") as f:
                    json.dump(loads, f)
            except Exception:
                pass
            watts = float(loads[load].get("watts", 0)) if action == "on" else 0.0
            self.pub.load(load, action, watts)

        self.pub.actuator(load, action, reco_id, ok, source)
        return ok


def fake_lines():
    """Generate MCU-shaped lines so this can be built before hardware works."""
    occ, lux, t, h = True, 200, 23.0, 45.0
    while RUNNING:
        if random.random() < 0.15:
            occ = not occ
        lux = max(0, min(900, lux + random.randint(-50, 50)))
        t = max(18.0, min(30.0, t + random.uniform(-0.25, 0.25)))
        h = max(30.0, min(70.0, h + random.uniform(-1.0, 1.0)))
        yield json.dumps({"occupancy": occ, "lux": lux, "temp_c": round(t, 1),
                          "humidity": round(h, 1), "raw_pir": 1 if occ else 0})
        time.sleep(1)


def serial_lines(handle):
    """Yield telemetry lines from an already-open serial handle.

    The handle is opened by the caller and shared with the Actuator, so commands
    are written on the same link the telemetry arrives on.
    """
    buf = b""
    while RUNNING:
        try:
            chunk = handle.read(256)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                yield line.decode("utf-8", errors="ignore").strip()
        except Exception as exc:
            print(f"[uno_q] serial error: {exc}", flush=True)
            time.sleep(1)


def read_loads() -> Dict:
    """Load state from a local file so we can flip loads mid-demo."""
    try:
        with open(LOADS_FILE, "r") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return dict(LOADS_DEFAULT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake-serial", action="store_true",
                    help="generate data without an MCU attached")
    ap.add_argument("--broker", default=MQTT_HOST)
    ap.add_argument("--port", type=int, default=MQTT_PORT)
    args = ap.parse_args()

    print(f"[uno_q] room={ROOM} broker={args.broker}:{args.port} "
          f"fake={args.fake_serial}", flush=True)

    # One serial handle, shared: telemetry is read from it, commands are written to it.
    handle = None
    if args.fake_serial or serial is None:
        if serial is None and not args.fake_serial:
            print("[uno_q] pyserial missing — falling back to fake data", flush=True)
        source = fake_lines()
    else:
        port = find_port()
        if port is None:
            print("[uno_q] no MCU serial port found — falling back to fake data", flush=True)
            source = fake_lines()
        else:
            handle = serial.Serial(port, BAUD, timeout=2)
            source = serial_lines(handle)

    pub = Publisher(args.broker, args.port)

    # Subscribe to hub commands so the loop can close: reco -> approve -> actuate.
    actuator = Actuator(pub, handle)
    pub.subscribe("home/command/#", actuator.on_command)
    print(f"[uno_q] command listener ready "
          f"(actuator source: {'mcu_serial' if handle else 'simulated'})", flush=True)

    lux_s, temp_s, hum_s = Smoother(), Smoother(), Smoother()
    occ_fsm = OccupancyFSM()

    last_pub = 0.0
    last: Dict[str, float] = {}
    parsed_n = dropped_n = 0
    last_loads: Dict[str, str] = {}

    try:
        for line in source:
            if not RUNNING:
                break
            if not line:
                continue

            try:
                raw = json.loads(line)
                parsed_n += 1
            except Exception:
                dropped_n += 1
                if dropped_n % 20 == 1:
                    print(f"[uno_q] dropped {dropped_n} malformed lines "
                          f"({dropped_n/(parsed_n+dropped_n)*100:.1f}%)", flush=True)
                continue

            # The MCU echoes an ack after every command on the same link. It is valid
            # JSON but not telemetry, so record it and move on.
            if "ack" in raw:
                actuator.last_ack = raw
                print(f"[uno_q] MCU ack: {raw}", flush=True)
                continue

            # --- edge processing ---
            lux = lux_s.push(float(raw.get("lux", 0)))
            temp = temp_s.push(float(raw.get("temp_c", 0.0)))
            hum = hum_s.push(float(raw.get("humidity", 0.0)))
            occ = occ_fsm.push(bool(raw.get("occupancy", False)))

            now = time.time()
            material = (
                not last
                or occ != last.get("occupancy")
                or abs(temp - last.get("temp_c", 0)) >= TEMP_CHANGE_C
                or abs(lux - last.get("lux", 0)) >= max(LUX_CHANGE_PCT * max(last.get("lux", 1), 1), 10)
            )
            heartbeat = (now - last_pub) >= HEARTBEAT_S

            if material or heartbeat:
                payload = {"occupancy": occ, "lux": int(lux),
                           "temp_c": round(temp, 1), "humidity": round(hum, 1),
                           "ts": now}
                pub.sensors(payload)
                last = payload
                last_pub = now
                why = "change" if material else "heartbeat"
                print(f"[uno_q] pub ({why}) occ={occ} lux={int(lux)} "
                      f"{temp:.1f}C {hum:.0f}%", flush=True)

            # --- load state from the local control file ---
            loads = read_loads()
            for name, spec in loads.items():
                state = spec.get("state", "off")
                if last_loads.get(name) != state:
                    pub.load(name, state, float(spec.get("watts", 0)) if state == "on" else 0.0)
                    last_loads[name] = state
                    print(f"[uno_q] load {name} -> {state}", flush=True)
    finally:
        print(f"[uno_q] parsed={parsed_n} dropped={dropped_n}", flush=True)
        pub.close()


if __name__ == "__main__":
    main()
