# AI Home Energy Concierge

A multi-device AI system that notices wasted household energy, explains in plain
language what it is costing you, and - with your approval - **physically switches it
off**.

Built for the Snapdragon Multiverse hackathon, August 2026.
**Archetype E - IoT Sensor to Actuator Physical AI.**

---

## The problem

Homes waste energy because devices operate independently and have no awareness of
context. Your air conditioner does not know you left the house. Your lights do not
know the sun is out. And in San Diego, SDG&E charges roughly 81% more per kilowatt-hour
between 4 PM and 9 PM than it does overnight - so *when* a load runs matters as much
as *whether* it runs.

No single device can see enough to fix this. A motion sensor sees an occupied room and
concludes everything is fine, even at noon with the blinds open and every light on.

## Closing the loop: human-approved actuation

The system does not stop at advice, and it does not act blindly either:

```
sense -> reason -> recommend -> USER APPROVES -> physically act -> confirm -> book the saving
```

Tap **Approve** on the phone or dashboard and the hub publishes a command; the UNO Q
drives a servo that presses a real light switch (or a relay), then publishes a
confirmation. The hub books the saving as **realized** rather than merely avoidable -
the UI distinguishes *"you could save $X"* from *"you saved $X."*

**The comfort guardrail gates the actuator.** Rule R7 is not advisory: it is enforced
as a pre-flight check in `POST /api/apply`, so the system will **refuse** to switch off
the A/C when the room is above 27 C, even if a user asks it to. That is the difference
between automation and judgment.

## Architecture

```
+------------------------------------+
|  Arduino UNO Q                     |
|  +--------------+ +--------------+ |
|  | STM32U585    | | Dragonwing   | |  MQTT over Wi-Fi
|  | Cortex-M33   | | QRB2210      | |----------------+
|  | (Zephyr)     |>| Debian/Python| |                |
|  | PIR/LDR/temp |<| MQTT client  | |<---- commands  |
|  | SERVO/RELAY  | | edge filter  | |                |
|  +--------------+ +--------------+ |                v
+------------------------------------+   +----------------------------+
                  ^                      | Copilot+ PC (Snapdragon    |
                  | physical action      | X Elite, 45 TOPS NPU)      |
                  |                      |        ORCHESTRATOR        |
+------------------------------+         |                            |
|  Galaxy S25 (Snapdragon      |         |  mosquitto broker          |
|  8 Elite)                    |-------->|  FastAPI + WebSocket       |
|  PWA: presence, geofence,    |         |  rules engine (determin.)  |
|  notifications, APPROVE      |         |  energy model (determin.)  |
+------------------------------+         |  GenieX LLM -- narration   |
                                         |  R7 guardrail -- gates     |
       +-----------------------+         |     physical action        |
       | Qualcomm AI Cloud 100 |<--------|  deep report (off-path)    |
       | weekly deep report    |         +-------------+--------------+
       +-----------------------+                       | WebSocket
                                                       v
                                             Dark dashboard (browser)
```

**Where each piece of intelligence lives, and why:**

| Tier | Device | Intelligence | Why here |
|---|---|---|---|
| Edge | UNO Q - STM32U585 (Zephyr) | 1 Hz sensor sampling; **actuator drive** | hard real time, no OS jitter |
| Edge | UNO Q - Dragonwing QRB2210 (Linux) | median smoothing, occupancy FSM, change-triggered publish, command subscriber | cuts broker traffic ~89%; raw sensor data stays local |
| Hub | Snapdragon X Elite (45 TOPS NPU) | rules engine, energy model, GenieX LLM, safety gate | sub-2 s narration; occupancy data never leaves the house |
| Cloud | AI Cloud 100 | larger model, weekly pattern analysis | seconds of latency are fine off the critical path |

Data flow: sensors + phone presence -> hub fuses a situation snapshot -> deterministic
rules produce findings -> the energy model attaches auditable savings -> the LLM turns
findings into natural language -> user approves -> **the UNO Q physically actuates** ->
the saving is booked as realized.

## What makes this different

**1. Where the AI runs is a design decision, not an accident.** Rules at the edge in
microseconds. A small model on-device via GenieX for private, low-latency narration. A
large model in the cloud for depth. Same problem, three tiers, each chosen against a
latency and privacy budget.

**2. Auditable AI - the model narrates, Python computes.** The LLM never performs
arithmetic and never invents a figure. It receives pre-computed values and may only
phrase them; afterwards every numeric field is overwritten from the deterministic
source. Each recommendation carries a `formula` string, shown in the UI, so any number
on screen can be recomputed by hand:

```
1100 W x 7200 s = 2.2000 kWh; 2.2000 kWh x $0.58/kWh (on_peak) = $1.276;
2.2000 kWh x 0.25 kg/kWh = 0.5500 kg CO2
```

**3. The recommendations require cross-device context fusion.** "Your A/C is cooling an
empty house" needs phone presence AND room occupancy AND load state AND the tariff
window, together. No single device in this system could produce it.

**4. The loop closes, and safely.** Physical actuation, human-approved, with the comfort
guardrail able to veto the machine's own advice.

**5. Every tier degrades gracefully.** Kill the LLM and a deterministic narrator takes
over. Kill the cloud and the report still generates. Kill the sensors and the simulator
drives the same real pipeline. Kill the broker and the hub accepts state over REST. Kill
the servo and the relay/buzzer still confirms the command landed.

## Measured performance

Technical Implementation is judged on *resource utilization, optimization, latency and
performance, and energy efficiency* - so we measure rather than claim. Reproduce with
`python hub/benchmark.py --markdown`.

Measured on a Windows-on-Arm dev machine with `LLM_ENABLED=0` (deterministic path):

| Metric | p50 | p95 | Unit | Notes |
|---|---|---|---|---|
| Energy-model estimate | 0.004 | 0.007 | ms | pure arithmetic + formula string |
| Rules engine (7 rules) | 0.027 | 0.046 | ms | 1 room / 3 loads -> 2 findings |
| Narration - deterministic template | 0.002 | 0.005 | ms | control path; no model, no network |
| Narration - local LLM (GenieX) | *fill in* | *fill in* | ms | run with `geniex serve` active |
| Sensor snapshot -> recommendations | 0.035 | 0.058 | ms | rules + energy model + narration |
| **Broker messages avoided by edge filtering** | **88.7** | - | **%** | 68 published of 600 sampled (10 min @ 1 Hz) |
| Peak RSS | 32.9 | - | MB | hub reasoning stack loaded |

**The reasoning tier costs microseconds.** All the latency that matters is LLM
inference, which is why it is the only part we accelerate on the NPU - and why the
deterministic fallback is instant when the model is unavailable.

**Edge filtering removes ~89% of broker traffic**: the UNO Q samples at 1 Hz but only
publishes on a material change (lux +/-15%, temp +/-0.3 C, occupancy transition) or a
10 s heartbeat.

> **To fill in the NPU row:** start `geniex serve`, then
> `LLM_BASE_URL=http://127.0.0.1:18181/v1 python hub/benchmark.py --markdown`.
> For authoritative NPU latency/power/utilization, run `/quad-profile` via the QUAD MCP
> server and paste its report below this table.

## Rules implemented

| Rule | Detects |
|---|---|
| R1 `unoccupied_lights_on` | room empty > 10 min, lights on |
| R2 `away_with_hvac_on` | user away, A/C or heater running |
| R3 `daylight_waste` | > 300 lux ambient, lights still on |
| R4 `hvac_with_window_open` | A/C running 15+ min, no temperature drop, high humidity (heuristic) |
| R5 `phantom_standby` | standby draw during an absence > 2 h |
| R6 `peak_hour_heavy_load` | deferrable heavy load inside the 4–9 PM peak window; charges only the rate delta |
| R7 `comfort_guardrail` | **suppresses** advice - and **refuses actuation** - above 27 °C / below 16 °C |

R7 is why this is an assistant rather than a thermostat: it removes recommendations that
would make the home uncomfortable, and it blocks the actuator from carrying them out.

## Hardware

- Copilot+ PC, Snapdragon X Series (hub)
- Arduino UNO Q + PIR motion sensor, photoresistor, DHT22, **micro-servo or relay**
- Samsung Galaxy S25 (or any phone with a modern browser)
- Qualcomm AI Cloud 100 (optional)

**The simulator replaces all sensor hardware.** Every feature except the physical servo
movement can be demonstrated with nothing but the PC.

## Setup

### Quickstart, no hardware — 3 commands

```bash
pip install -r requirements.txt
python hub/server.py                      # terminal 1
python hub/simulator.py --mode demo       # terminal 2
```

Open `http://localhost:8000/`. (The broker is optional for this path; the simulator
needs it, so start `mosquitto -c mosquitto.conf -v` first if you want the scripted
scenario. Without a broker, drive the hub over REST — see below.)

### Full setup

**1. Dependencies**

```bash
pip install -r requirements.txt
```

> **Important:** it must be `uvicorn[standard]` and `websockets`. Plain `uvicorn` has no
> WebSocket support — the API works but `/ws` returns 404 and the dashboard renders
> blank with no server-side error.

**2. MQTT broker**

Install mosquitto, then run it with the included config (the default config binds only
localhost, so the phone and UNO Q could not reach it):

```bash
mosquitto -c mosquitto.conf -v
```

Open the firewall once, elevated:

```powershell
New-NetFirewallRule -DisplayName "MQTT 1883" -Direction Inbound -LocalPort 1883 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "Hub HTTP 8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

**3. Local LLM via GenieX (optional but recommended)**

[GenieX](https://github.com/qualcomm/geniex) is Qualcomm's on-device inference runtime.
It ships an OpenAI-compatible server, so no application code changes:

```bash
# Windows ARM64: installer from https://github.com/qualcomm/geniex/releases
geniex pull ai-hub-models/Qwen3-4B-Instruct-2507
geniex serve                        # -> http://127.0.0.1:18181/v1
```

Those are already the defaults in `hub/llm.py`, so usually nothing to export. To
override, or to point at any other OpenAI-compatible endpoint:

```bash
export LLM_BASE_URL=http://127.0.0.1:18181/v1
export LLM_MODEL=ai-hub-models/Qwen3-4B-Instruct-2507
export LLM_ENABLED=1          # set 0 to force the deterministic narrator
```

GenieX offers two runtimes, and the choice is deliberate:

| Runtime | Model source | Format | Compute | We use it when |
|---|---|---|---|---|
| `qairt` | Qualcomm AI Hub, pre-compiled | per-chipset bundle | **NPU only** | default — maximum NPU performance |
| `llama_cpp` | Hugging Face | GGUF (`Q4_0` best for Hexagon) | NPU · GPU · CPU | a specific model is needed |

**Without any LLM the system still works** — `template_narrate()` produces the same
recommendations deterministically, and the dashboard labels which path produced each
card.

**4. Run the hub**

```bash
python hub/server.py
```

It prints the LAN URL to use from other devices.

**5. Arduino UNO Q — sensors and actuator**

Flash `arduino/sketch/sketch.ino` to the STM32 side (App Lab targets both brains; the
Arduino IDE/CLI programs the MCU only). Confirm one JSON line per second at 115200 baud.
Then on the Dragonwing Linux side:

```bash
pip3 install paho-mqtt pyserial
ROOM=living MQTT_HOST=<PC_IP> python3 uno_q_publisher.py
```

This publishes sensor data **and subscribes to `home/command/#`** so approved
recommendations reach the actuator.

Without hardware: `python3 uno_q_publisher.py --fake-serial`
Without the Linux side: `python arduino/bridge.py --port COM5` (runs on the PC over USB)

Wiring:

| Function | Pin |
|---|---|
| PIR motion out | D2 |
| LDR divider (10k to GND) | A0 |
| DHT22 data | D4 |
| Servo signal | D9 |
| Relay / LED | D7 |
| Buzzer | D8 |

> Power the servo from its own 5 V supply, not the board's 3V3 rail — a stalling servo
> browns out the MCU. Tune `SERVO_OFF_DEG` / `SERVO_ON_DEG` so the arm reaches the switch
> without pressing hard against it. If no servo is attached, the relay and buzzer still
> confirm the command landed, and the confirmation reports `source` honestly.

**6. Phone**

Join the same network, open `http://<PC_IP>:8000/phone`, tap **Enable alerts**.
Geolocation is optional and degrades silently to the manual HOME/AWAY toggle — browsers
commonly block it over plain HTTP.

**7. Cloud report (optional)**

```bash
export CLOUD_BASE_URL=<AI Cloud 100 endpoint>
export CLOUD_MODEL=<larger model>
```

Unset, the button still works and returns a deterministic Python report.

## Usage

### Run the scripted demo

```bash
python hub/simulator.py --mode demo          # 90-second narrative, loops
python hub/simulator.py --mode demo --speed 3 # faster, for rehearsal
python hub/simulator.py --mode random         # continuous plausible drift
```

The scenario prints a caption at each beat:

| t | What happens | Expected |
|---|---|---|
| 0s | home, evening, on-peak, TV + A/C + lights | no findings — everything is in use |
| 15s | user leaves, geofence crosses | presence → away |
| 25s | motion times out, loads still on | **R2 critical + R1 serious** |
| 45s | user acts, loads off | power collapses |
| 60s | next morning, 640 lux, lights on | **R3 warning** |
| 75s | dryer starts at 17:00 | **R6, rate delta only** |

### Drive it with no broker and no hardware

```bash
curl -X POST http://localhost:8000/api/sensor -H "Content-Type: application/json" \
  -d '{"room":"living","occupancy":false,"lux":110,"temp_c":23.6,"humidity":47}'
curl -X POST http://localhost:8000/api/load -H "Content-Type: application/json" \
  -d '{"key":"living/ac","state":"on","watts":1100}'
curl -X POST http://localhost:8000/api/presence -H "Content-Type: application/json" \
  -d '{"presence":"away","distance_m":2400}'
```

### Approve an action — the closed loop

Tap **Approve & turn it off** on the phone (or **Apply** on the dashboard). Or by hand:

```bash
curl -X POST http://localhost:8000/api/apply -H "Content-Type: application/json" \
  -d '{"reco_id":"r2-living-ac","action":"off","approved_by":"cli"}'
```

What happens: the hub runs the R7 pre-flight check → publishes
`home/command/living/ac` → the UNO Q drives the servo/relay and publishes
`home/actuator/living/ac` → the hub books the saving as **realized** and the finding
closes.

To see the guardrail refuse an unsafe action, set the room above 27 °C first:

```bash
curl -X POST http://localhost:8000/api/sensor -H "Content-Type: application/json" \
  -d '{"room":"living","occupancy":false,"lux":110,"temp_c":29.5,"humidity":52}'
# a subsequent apply on the A/C returns HTTP 409 with the reason
```

### Verify the install

```bash
python smoke_test.py
```

32 checks covering arithmetic, every rule, the comfort guardrail (as both a filter and
an actuation gate), LLM fallback, cloud fallback, the live server, and the full
approve → command → realized-saving loop. Exits non-zero on failure.

### Measure performance

```bash
python hub/benchmark.py              # human-readable report
python hub/benchmark.py --markdown   # table for this README
python hub/benchmark.py --json       # machine-readable
```

Run it twice to compare runtimes:

```bash
LLM_ENABLED=0 python hub/benchmark.py                             # deterministic control
LLM_BASE_URL=http://127.0.0.1:18181/v1 python hub/benchmark.py    # GenieX on the NPU
```

### Test the individual modules

```bash
python hub/energy_model.py    # prints a hand-checkable table of estimates
python hub/rules.py           # fires each rule in its own scenario
python hub/llm.py             # compares LLM and template narration
python hub/cloud_report.py    # generates a report from a synthetic digest
```

Actuation can also be tested directly from a serial monitor at 115200 baud — type
`CMD lights off` and the sketch replies `{"ack":"lights","state":"off","ok":true}`.

## MQTT topic contract

| Topic | Publisher | Payload |
|---|---|---|
| `home/sensors/<room>` | UNO Q | `{"occupancy":bool,"lux":int,"temp_c":float,"humidity":float,"ts":epoch}` |
| `home/loads/<room>/<load>` | UNO Q / sim | `{"state":"on"\|"off","watts":float,"ts":epoch}` |
| `home/context/user` | phone | `{"presence":"home"\|"away","distance_m":int,"battery":int,"ts":epoch}` |
| `home/reco` | hub | `{"id","severity","title","body","kwh","usd","co2_kg","actions"}` |
| `home/command/<room>/<load>` | hub | `{"action":"on"\|"off","reco_id":str,"approved_by":str,"ts":epoch}` |
| `home/actuator/<room>/<load>` | UNO Q | `{"state":"on"\|"off","source":str,"reco_id":str,"ok":bool,"ts":epoch}` |
| `home/state` | hub | full snapshot |

All payloads are JSON, all carry `ts`, all units appear in the key name.

The `command` / `actuator` pair is the actuation loop: the hub publishes an approved
command, the UNO Q executes it physically and confirms. `source` reports honestly how it
was carried out (`mcu_serial`, `simulated`, or `serial_error`), so a confirmation never
overstates what happened.

## Repo layout

```
code/
  hub/
    energy_model.py    deterministic arithmetic — the source of every number
    rules.py           R1-R7 waste detection, no LLM
    llm.py             GenieX narration + deterministic fallback
    server.py          MQTT, state fusion, FastAPI, WebSocket, /api/apply + safety gate
    simulator.py       scripted and random sensor feeds
    cloud_report.py    AI Cloud 100 deep report + deterministic fallback
    benchmark.py       latency / memory / efficiency measurements
  dashboard/index.html hub dashboard with Apply buttons (no build step)
  phone/index.html     phone PWA with Approve buttons (no build step)
  arduino/
    sketch/sketch.ino  STM32 firmware: sensors + servo/relay actuator
    uno_q_publisher.py Dragonwing Linux publisher + command subscriber
    bridge.py          USB fallback, runs on the PC
  mosquitto.conf       broker config that binds 0.0.0.0
  requirements.txt
  smoke_test.py        32 checks, including the actuation loop and safety gate
  LICENSE              Apache 2.0
```

## Limitations, honestly

- **Load power is modelled, not metered.** Wattages are published typical figures (DOE,
  Energy Star), each carrying a `source` string visible in the audit panel. Clamp meters
  or smart plugs are the obvious next step.
- **Lux is uncalibrated.** A photoresistor divider approximates lux; we use it only
  against a coarse threshold, and the rule evidence says so.
- **R4 is a heuristic.** We infer an open window from A/C runtime, temperature stall and
  humidity — we do not sense the window. The evidence text states this.
- **Actuation is human-approved by design, not autonomous.** The system will not act
  without a tap. That is a deliberate trust decision rather than a missing feature: R7
  demonstrates the machine refusing its own advice, and we would want a lot more
  validation before removing the human from that loop.
- **One actuator, one room.** The command topic is already per-room/per-load and the
  rules iterate over rooms, so scaling is a matter of adding publishers — but we
  demonstrate one room and one switch.
- **NPU figures need the QUAD profile to be authoritative.** `benchmark.py` measures
  end-to-end latency on this machine; `/quad-profile` measures on real silicon with power
  and utilization. Run both before quoting NPU numbers.
- **NPU acceleration not yet measured.** The local model runs through the runtime's
  default path. QNN/Hexagon acceleration is the natural next step; we did not want to
  claim a number we had not measured.

## Team

> **REQUIRED BEFORE SUBMISSION** — the organizers require the name *and email* of every
> team member. Replace this table; do not submit with placeholders.

| Name | Email | Role |
|---|---|---|
| Gowtham Raj Baskaran | gbaskara@qti.qualcomm.com | Hub / AI orchestration |
| *&lt;name&gt;* | *&lt;email&gt;* | Embedded / UNO Q |
| *&lt;name&gt;* | *&lt;email&gt;* | Mobile / PWA |
| *&lt;name&gt;* | *&lt;email&gt;* | Front-end / documentation |
| *&lt;name&gt;* | *&lt;email&gt;* | Cloud / validation |

Project proposal submitted by Nanda Kishore Nagabhushana.

## License

Licensed under the **Apache License 2.0** — see [LICENSE](LICENSE). All code in this
repository is open source.

## References

- [Qualcomm GenieX](https://github.com/qualcomm/geniex) — on-device inference runtime used for local LLM narration
- [Qualcomm AI Hub](https://aihub.qualcomm.com) — model source for the `qairt` NPU path
- [Arduino UNO Q documentation](https://docs.arduino.cc/hardware/uno-q/) — dual-brain MPU/MCU architecture and the Arduino Bridge RPC library
- [Qualcomm AI Developer Workflow docs](https://docs.qualcomm.com/bundle/publicresource/topics/80-62010-1/welcome.html)
- Load power figures: US DOE and ENERGY STAR published typical values (each entry in `hub/energy_model.py` carries its own `source` string)
- Tariff structure: SDG&E TOU-DR1 residential time-of-use schedule
- Grid carbon intensity: EPA eGRID CAMX region / California Energy Commission

## Notes

- **Archetype:** IoT Sensor → Actuator Physical AI. The loop closes with human approval —
  the system recommends, the user approves, the UNO Q physically actuates. See
  [Closing the loop](#closing-the-loop-human-approved-actuation).
- **On QUAD codegen:** the organizers' project sheet flags gap **G6** (UNO Q
  sensor/actuator + GPIO codegen, not yet implemented) as blocking the `generate_code`
  stage. We did not need it — the sketch, the MPU-side publisher, and the actuator path
  are hand-written and tested. QUAD is used for what it does well here: profiling on
  real silicon.
- Lux, load power and the R4 window heuristic are approximations, each labelled as such
  in the UI evidence text. See [Limitations](#limitations-honestly).
