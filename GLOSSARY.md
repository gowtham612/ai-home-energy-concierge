# Glossary — AI Home Energy Concierge

Every term this project uses, in the order the data actually flows. Written because
the vocabulary accumulated faster than the docs did, and the project is now worked on
from more than one machine.

Where a term is commonly misunderstood, the misunderstanding is called out explicitly.

---

## The shape of the system

```
Modulino Knob/Buttons ──I2C──> STM32 MCU ──serial──> arduino-router ──:7500──> publisher
                                                                                   │ MQTT
                                                                              broker (PC)
                                                                                   │
                                                                                  hub
                                                                        rules → AI → decision
                                                                                   │ MQTT command
                                                                              publisher ──> real bulb
```

One sentence: **the board senses, the PC decides, the board acts.**

---

## 1. The four programs

| Term | What it is |
|---|---|
| **Publisher** (`code/arduino/uno_q_publisher.py`) | Python process on the **board's Linux side**. Two jobs: it reads sensor telemetry from the MCU and *publishes* it to MQTT, **and** it listens for commands and physically switches the Kasa devices. The name describes only the first job; the second is what makes the demo real. |
| **Hub** (`code/hub/server.py`) | The brain, on the **PC**. Ingests MQTT, runs the rules, calls the AI, decides, and serves all four web pages. |
| **Simulator** (`/simulator`) | Browser page with sliders for lux / humidity / temperature and Home / Away buttons. Lets you supply sensors you do not physically have, and displays recommendations and refusals. |
| **Phone page** (`/phone`) | Mobile page titled "Energy Concierge" — the recommendation feed with **Approve** buttons and realized savings. What a homeowner would actually use. |

> **It is not a PWA.** `code/phone/index.html` has no manifest and no service worker, so it
> does not install, does not work offline, and is not a Progressive Web App. It is a plain
> mobile-friendly web page. It is also **not** a sensor simulator — the sliders are on
> `/simulator`.

---

## 2. Hardware

| Term | Meaning |
|---|---|
| **UNO Q** | The Arduino board. Has two processors on one PCB — see MCU and MPU. |
| **MCU** | STM32U585 microcontroller. Runs `sketch.ino`, owns the Modulinos and the three LEDs. Small, real-time, no operating system. |
| **MPU** | Dragonwing **QRB2210** — quad-core Cortex-A53 running Debian. Runs the publisher. **Has no NPU**, which is why no LLM runs here. |
| **Dual brain** | The UNO Q having both on one board. They talk over an internal link (RPMsg), not over USB. |
| **Modulino** | Arduino's snap-together sensor modules. In use: **Knob** (rotary dial, drives temperature) and **Buttons** (A / B / C, each with an LED). |
| **Qwiic** | The 4-pin connector standard the Modulinos plug into. |
| **I²C** | The two-wire protocol running over Qwiic. |
| **`Wire1`** | The specific I²C bus the UNO Q wires Qwiic to. Not `Wire` — using the wrong one finds no devices. |
| **Kasa** | TP-Link's smart-home brand. |
| **KL120** | The smart **bulb** — "Bedroom light 2", the `lights` load. |
| **HS110** | The smart **plug** — "Space heater", the `ac` load. Has real energy metering. |
| **emeter** | The HS110's built-in power meter. The reason watts are *measured* rather than estimated. |

> **TP-Link firmware serves one connection at a time.** Two things polling the same device
> concurrently is what caused the bulb-flicker bug and the autopilot fighting the publisher.

---

## 3. Getting data off the board

| Term | Meaning |
|---|---|
| **`arduino-router`** | Linux service on the board that relays the MCU's serial stream and republishes it on a TCP socket. |
| **Monitor socket / `:7500`** | That TCP socket, `tcp://127.0.0.1:7500`. The MCU's serial does **not** appear as `/dev/ttyACM*`, so this is the only way in or out. It is **bidirectional** — writes reach the MCU. |
| **Telemetry** | The 1 Hz JSON line the MCU emits: temperature, button counters, network status, which Modulino nodes it found. |
| **Provenance / `*_src`** | Tags such as `temp_src: "knob_sim"` or `"synthetic"`, marking where each value came from so a stub can never masquerade as a measurement. Added after a bug where fake readings looked exactly like real ones. |
| **`nodes`** | Telemetry field listing detected Modulinos — `"KB"` means Knob and Buttons both found. Check this first when buttons seem dead. |
| **adb** | Android Debug Bridge. Talks to the board over USB. Pin the board with `adb -s 3933751369`. |
| **`adb reverse` / tunnel** | Makes a port on the *board* forward to the *PC*. How MQTT crosses the USB cable. **Ours is on 11883, not 1883** — the board runs its own broker on 1883, so that tunnel silently fails. |
| **SIMBTN** | A command written to `:7500` that makes the firmware act as if a button were pressed. Lets you test the chain without touching hardware. |

---

## 4. Messaging

| Term | Meaning |
|---|---|
| **MQTT** | Lightweight publish/subscribe messaging. Senders publish to a *topic*; receivers subscribe to it. Nobody addresses anybody directly. |
| **Broker** | The server every message passes through. Here: **mosquitto**, on the PC. |
| **Topic** | The address of a message, e.g. `home/loads/living/lights`. |
| **Cue** | A message asking the *simulator page* to move one of its own controls. Button A sends a cue rather than writing state directly, so the UI visibly moves instead of numbers changing behind its back. Topic: `home/demo/cue`. |
| **Retained / on-change publishing** | Loads publish only when they *change*, which is why a freshly restarted hub shows no loads until something toggles. |

---

## 5. The AI

| Term | Meaning |
|---|---|
| **Rules R1–R7** | Deterministic checks in `code/hub/rules.py`. R1 = lights on in an empty room. R2 = HVAC on while away. R7 = the comfort guardrail. About **0.014 ms**. |
| **Finding** | What a rule produces: "lights on in an empty living room for 3 minutes". |
| **Recommendation** | A finding after the LLM has written it up for a human. |
| **Guardrail (R7)** | Refuses to switch the AC off above 27 °C — even if a rule asks and even if a human approves. Returns HTTP 409. This is demo Beat 5. |
| **NPU** | Neural Processing Unit — a chip dedicated to running models. |
| **Hexagon** | Qualcomm's NPU, in the PC's Snapdragon. **Not** on the board. |
| **GenieX** | The local server that runs the model on that NPU. Port **18181**, OpenAI-compatible API. |
| **Qwen3-4B-Instruct-2507 W4A16** | The model. 4 billion parameters; **W4A16** = 4-bit weights, 16-bit activations, quantised to fit the NPU. |
| **Three tiers** | Edge logistic regression (**30.6 µs**) → rules (**0.014 ms**) → LLM (**~3.3 s**). The fast tiers act; the slow one explains. |
| **Edge tier / anomaly detector** | A trained logistic regression in pure Python, running on the board's A53. Cheap enough to run continuously where an LLM would be absurd. |
| **Planner** | One LLM call that ranks all findings together, instead of narrating each separately. Caches on the set of finding ids, so it only calls out when that set changes. |
| **Narration** | The per-finding LLM write-up. The fallback beneath the planner. |
| **Provenance verifier** | Checks that every number the model emits actually appears in its source data. Catches invented figures. |
| **History digest** | 37 days of real 15-minute utility data, rolled into per-bucket totals plus a typical-day baseline. The **past** window. |
| **Disaggregation** | Splitting a whole-home meter into per-appliance buckets. Here it is **inferred** from the size and time of each 15-minute jump over that day's 10th-percentile baseline — never measured per-circuit. |
| **Computed answer** | A question the hub already knows the answer to (R7's verdict, arithmetic, the anomaly score). These bypass the model entirely — it can only degrade a known-correct result. |
| **LIVE vs HISTORY** | Two separate windows. "What am I drawing now" is LIVE; "why is my bill high" is HISTORY. Answering one with the other is the main hazard of feeding both to a model. |

> **Latency tracks output length, not input.** 135 characters ≈ 2.6 s; 1060 ≈ 11.4 s.
> Capping output length is the single most effective speed control.

---

## 6. Demo vocabulary

| Term | Meaning |
|---|---|
| **Beat** | One segment of the demo video. |
| **HITL** | Human-in-the-loop — a person approves before anything switches. |
| **Actuator / actuation** | The act of physically switching a device. |
| **`source: simulated`** | No real device was reachable, so the switch was pretended. Honest degradation, but it means **nothing physically happened**. |
| **`source: kasa`** | A real device was switched, and its state was read back to confirm. |
| **Grace period** | How long a condition must hold before a rule fires. Stops the system reacting to you stepping out for ten seconds. |
| **Settle** | A pause after a reset during which auto-actuation is suppressed, so the reset is not immediately undone. |
| **Cooldown** | Minimum gap before the same load can be auto-acted on again. |
| **Eval interval** | How often the rules run. |
| **Auto-act** (`AI_AUTO_LIGHTS`) | The system switching the lights **without** a human. Scoped to lights only, never comfort-affecting loads. Turning this on makes "a human approves every action" false — say so if asked. |
| **Archetype E** | The hackathon category: sense the world, then *physically act* on it. Why a real bulb going dark matters more than a convincing screen. |

---

## 7. Operations

| Term | Meaning |
|---|---|
| **Smoke test** | `code/smoke_test.py` — 32 fast checks that nothing fundamental broke. Uses no hardware. Must stay **32/32**. |
| **Feature flags** | `AI_ASK`, `AI_PLAN`, `AI_ANOMALY`, `AI_AUTO_LIGHTS`. **All default off**, so the hub must be started deliberately. A hub without them looks healthy and silently has no AI. |
| **Demo pacing** | `DEMO_GRACE_S`, `DEMO_AWAY_GRACE_S`, `AUTO_COOLDOWN_S`, `EVAL_INTERVAL_S`, `RESET_SETTLE_S`. Environment overrides only; shipping defaults are untouched. |
| **`/api/pacing`** | Reports the pacing values the running hub is actually using, so they can be verified rather than assumed. |
| **mDNS / `.local`** | Name resolution on the local link. `DESKTOP-BBAGVJC.local` follows the PC across networks, unlike an IP. Requires both devices on the same LAN. |
| **`board.env`** | Board-side config: broker host/port, Kasa IPs. Gitignored. **Must be LF-only** — CRLF makes the publisher serve invented data while looking healthy. |

---

## 8. Where things live

| Path | What |
|---|---|
| `code/arduino/sketch/sketch.ino` | MCU firmware |
| `code/arduino/uno_q_publisher.py` | Publisher (board) |
| `code/hub/server.py` | Hub: HTTP, MQTT, eval loop, auto-act |
| `code/hub/rules.py` | R1–R7 |
| `code/hub/llm.py` | GenieX client |
| `code/hub/planner.py` | Tier-2 plan synthesis |
| `code/hub/anomaly.py` | Edge logistic regression |
| `code/hub/ask.py` | `/ask` Q&A + deterministic answerer |
| `code/hub/history_digest.py` | 37-day usage rollup |
| `code/hub/provenance.py` | number-in-source verifier |
| `code/tools/history_disaggregate.py` | builds the labelled CSV from raw utility data |
| `code/tools/ask_score.py` | scored regression probe for `/ask` |
| `code/simulator/index.html` | Sensor + control page |
| `code/phone/index.html` | Approval feed |
| `code/tools/run_demo.ps1` | Tiled demo launcher |
| `code/smoke_test.py` | The 32 checks |
| `07_QUAD_SESSION_LOG.md` | **The golden reference.** §0 is the resume point. |
