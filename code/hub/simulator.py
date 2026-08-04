"""Sensor/context simulator — the demo insurance policy.

Publishes realistic fake data to MQTT so the entire pipeline runs with zero
hardware. `--mode demo` runs a scripted 90-second narrative with printed captions
so you can narrate on stage.

Everything downstream of this file is real: real rules, real arithmetic, real LLM,
real dashboard. Only the sensors are simulated — say that out loud if you use it.

Run:  python hub/simulator.py --mode demo
      python hub/simulator.py --mode demo --speed 3     (rehearsal, 3x faster)
      python hub/simulator.py --mode random
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("paho-mqtt required:  pip install paho-mqtt")
    sys.exit(1)

ROOM = "living"


class Publisher:
    def __init__(self, host: str, port: int):
        try:
            self.c = mqtt.Client(client_id="simulator")
        except Exception:
            self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="simulator")
        self.c.connect(host, port, keepalive=30)
        self.c.loop_start()
        self.sim_now = None

    def sensors(self, occupancy: bool, lux: int, temp_c: float, humidity: float):
        self.c.publish(f"home/sensors/{ROOM}", json.dumps({
            "occupancy": occupancy, "lux": lux, "temp_c": round(temp_c, 1),
            "humidity": round(humidity, 1), "ts": self._ts()}))

    def load(self, name: str, state: str, watts: float):
        self.c.publish(f"home/loads/{ROOM}/{name}", json.dumps({
            "state": state, "watts": watts, "ts": self._ts()}))

    def user(self, presence: str, distance_m: int, battery: int = 72):
        self.c.publish("home/context/user", json.dumps({
            "presence": presence, "distance_m": distance_m,
            "battery": battery, "ts": self._ts()}))

    def clock(self, sim_dt: datetime):
        """Drive the hub's virtual clock so on-peak/off-peak logic is exercised."""
        self.sim_now = sim_dt
        offset = sim_dt.timestamp() - time.time()
        self.c.publish("home/context/clock", json.dumps({"offset_s": offset}))

    def _ts(self) -> float:
        return self.sim_now.timestamp() if self.sim_now else time.time()

    def close(self):
        self.c.loop_stop()
        self.c.disconnect()


def banner():
    print("\n" + "!" * 68)
    print("  SIMULATED SENSOR FEED — no physical sensors attached")
    print("  Rules, energy math, LLM narration and dashboard are all REAL")
    print("!" * 68 + "\n")


def caption(t: int, text: str):
    print(f"\n  [t={t:>3}s]  {text}")


def run_demo(pub: Publisher, speed: float):
    """A scripted 90-second narrative. Beats are timed for a 5-minute demo slot."""
    def wait(s): time.sleep(s / speed)

    # Evening, on-peak window, user home.
    sim = datetime.now().replace(hour=18, minute=30, second=0, microsecond=0)
    pub.clock(sim)

    caption(0, "18:30 — you are home, watching TV. On-peak tariff ($0.58/kWh).")
    pub.user("home", 0)
    pub.sensors(True, 120, 23.4, 46)
    pub.load("lights", "on", 240)
    pub.load("ac", "on", 1100)
    pub.load("tv", "on", 120)
    print("         expect: NO findings — everything is legitimately in use")
    wait(15)

    caption(15, "You leave the house. Phone geofence crosses the boundary.")
    sim += timedelta(minutes=5); pub.clock(sim)
    for d in (5, 80, 300, 800):
        pub.user("away" if d > 100 else "home", d)
        wait(2)
    print("         phone -> AWAY at 800 m")
    wait(7)

    caption(25, "Motion sensor times out. Lights and A/C are still running.")
    sim += timedelta(minutes=10); pub.clock(sim)
    pub.sensors(False, 110, 23.6, 47)
    pub.user("away", 2400)
    print("         expect: R2 away_with_hvac_on [CRITICAL] — biggest dollar figure")
    print("         expect: R1 unoccupied_lights_on [SERIOUS]")
    print("         >>> THIS IS THE MONEY MOMENT — phone should buzz <<<")
    wait(20)

    caption(45, "You act on the recommendation: lights off, A/C off.")
    pub.load("lights", "off", 0)
    pub.load("ac", "off", 0)
    pub.load("tv", "off", 0)
    pub.load("standby", "on", 12)
    print("         expect: total power collapses on the sparkline, savings banked")
    wait(15)

    caption(60, "Next morning, 10:00. Bright daylight, but lights came back on.")
    sim = sim.replace(hour=10, minute=0) + timedelta(days=1)
    pub.clock(sim)
    pub.user("home", 0)
    pub.sensors(True, 640, 24.8, 44)
    pub.load("lights", "on", 240)
    print("         expect: R3 daylight_waste [WARNING] — 640 lux vs 300 threshold")
    wait(15)

    caption(75, "17:00 — you start the dryer, right inside the peak window.")
    sim = sim.replace(hour=17, minute=0)
    pub.clock(sim)
    pub.load("dryer", "on", 3000)
    pub.sensors(True, 300, 25.2, 45)
    print("         expect: R6 peak_hour_heavy_load — 'delay until 9 PM, save $X'")
    print("         note: it charges only the RATE DELTA, not the whole cycle")
    wait(15)

    caption(90, "Scenario complete. Ctrl-C to stop, or it loops.")


def run_random(pub: Publisher):
    occupancy = True
    lux, temp, hum = 200, 23.0, 45.0
    lights, ac = "on", "off"
    presence, dist = "home", 0

    while True:
        if random.random() < 0.12:
            occupancy = not occupancy
        if random.random() < 0.08:
            presence = "away" if presence == "home" else "home"
            dist = random.randint(400, 4000) if presence == "away" else 0
        if random.random() < 0.10:
            lights = "off" if lights == "on" else "on"
        if random.random() < 0.06:
            ac = "off" if ac == "on" else "on"

        lux = max(0, min(900, lux + random.randint(-60, 60)))
        temp = max(18.0, min(30.0, temp + random.uniform(-0.3, 0.3)))
        hum = max(30.0, min(75.0, hum + random.uniform(-1.5, 1.5)))

        pub.sensors(occupancy, lux, temp, hum)
        pub.load("lights", lights, 240 if lights == "on" else 0)
        pub.load("ac", ac, 1100 if ac == "on" else 0)
        pub.user(presence, dist)
        print(f"  occ={occupancy!s:5} lux={lux:3} {temp:.1f}C {hum:.0f}%  "
              f"lights={lights:3} ac={ac:3}  user={presence} {dist}m")
        time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["random", "demo"], default="demo")
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--speed", type=float, default=1.0, help="time compression for rehearsal")
    ap.add_argument("--once", action="store_true", help="run the demo once, do not loop")
    args = ap.parse_args()

    banner()
    pub = Publisher(args.broker, args.port)
    print(f"  connected to {args.broker}:{args.port}, mode={args.mode}, speed={args.speed}x")

    try:
        if args.mode == "demo":
            while True:
                run_demo(pub, args.speed)
                if args.once:
                    break
                print("\n  --- looping scenario ---")
                time.sleep(3 / args.speed)
        else:
            run_random(pub)
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        pub.close()


if __name__ == "__main__":
    main()
