#!/usr/bin/env python3
"""USB-serial fallback bridge — runs on the PC, not the UNO Q.

Use this if the UNO Q's Linux side is unavailable and the board is acting as a
plain USB sensor. Same parsing and smoothing as uno_q_publisher.py, so the hub
sees identical traffic either way.

Run:  python bridge.py --port COM5 --broker localhost
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from typing import Dict, List, Optional

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("pyserial required: pip install pyserial", file=sys.stderr)
    sys.exit(1)

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("paho-mqtt required: pip install paho-mqtt", file=sys.stderr)
    sys.exit(1)

BAUD = 115200
HEARTBEAT_S = 10
OCC_GRACE_S = 30
TEMP_CHANGE_C = 0.3
LUX_CHANGE_PCT = 0.15


def autodetect() -> Optional[str]:
    """Pick the most likely Arduino port so we do not guess COM numbers."""
    for p in serial.tools.list_ports.comports():
        blob = f"{p.description} {p.manufacturer or ''}".lower()
        if any(k in blob for k in ("arduino", "usb serial", "stm", "qualcomm", "acm")):
            print(f"[bridge] auto-detected {p.device} ({p.description})")
            return p.device
    ports = list(serial.tools.list_ports.comports())
    if ports:
        print(f"[bridge] guessing {ports[0].device}; override with --port")
        return ports[0].device
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None, help="COM port; auto-detected if omitted")
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--room", default="living")
    args = ap.parse_args()

    port = args.port or autodetect()
    if not port:
        print("[bridge] no serial port found", file=sys.stderr)
        sys.exit(1)

    try:
        c = mqtt.Client(client_id=f"bridge-{args.room}")
    except Exception:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bridge-{args.room}")
    c.connect(args.broker, args.mqtt_port, keepalive=30)
    c.loop_start()
    print(f"[bridge] {port} -> {args.broker}:{args.mqtt_port} room={args.room}")

    s = serial.Serial(port, BAUD, timeout=2)
    lux_buf: List[float] = []
    temp_buf: List[float] = []
    last_motion = 0.0
    last_pub = 0.0
    last: Dict = {}
    parsed = dropped = 0

    try:
        while True:
            raw_line = s.readline().decode("utf-8", errors="ignore").strip()
            if not raw_line:
                continue
            try:
                d = json.loads(raw_line)
                parsed += 1
            except Exception:
                dropped += 1
                continue

            now = time.time()
            if d.get("occupancy"):
                last_motion = now
            occ = (last_motion > 0) and ((now - last_motion) < OCC_GRACE_S)

            lux_buf.append(float(d.get("lux", 0)))
            temp_buf.append(float(d.get("temp_c", 0.0)))
            lux_buf[:] = lux_buf[-5:]
            temp_buf[:] = temp_buf[-5:]
            lux = statistics.median(lux_buf)
            temp = statistics.median(temp_buf)

            material = (not last or occ != last.get("occupancy")
                        or abs(temp - last.get("temp_c", 0)) >= TEMP_CHANGE_C
                        or abs(lux - last.get("lux", 0)) >= max(LUX_CHANGE_PCT * max(last.get("lux", 1), 1), 10))

            if material or (now - last_pub) >= HEARTBEAT_S:
                payload = {"occupancy": occ, "lux": int(lux), "temp_c": round(temp, 1),
                           "humidity": round(float(d.get("humidity", 0.0)), 1), "ts": now}
                c.publish(f"home/sensors/{args.room}", json.dumps(payload))
                last, last_pub = payload, now
                print(f"[bridge] occ={occ} lux={int(lux)} {temp:.1f}C  "
                      f"(parsed={parsed} dropped={dropped})")
    except KeyboardInterrupt:
        print(f"\n[bridge] stopped. parsed={parsed} dropped={dropped}")
    finally:
        c.loop_stop()
        c.disconnect()
        s.close()


if __name__ == "__main__":
    main()
