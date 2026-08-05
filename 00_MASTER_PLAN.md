# AI Home Energy Concierge — Master Execution Plan

**Event:** Snapdragon Multiverse 2026, Qualcomm San Diego (AZ MPR ABC)
**Window:** Mon Aug 3, 11:30 AM (dev begins) → **Fri Aug 7, 12:00 PM submit**
**Working time:** ~4.5 days. Treat **Thursday 5 PM** as the real deadline; Friday morning is buffer only.

**Assigned archetype (from the organizers' project sheet #23):**
> **Archetype E — IoT Sensor → Actuator Physical AI.** *"Close the loop from sensing to
> physical action — servos, lenses, robotic arms, haptics — driven by the Arduino UNO Q +
> X-Elite brain."*

This is binding: the project must **act physically**, not only advise. See Decision 4.

**Hard submission requirements (orientation deck p.7 — all mandatory):**
1. Personal **GitHub repository**, public, submitted via a **Microsoft Form** (one per team)
2. **All code open source**
3. README containing: application description · **names and emails of every team member** · setup instructions from scratch including dependencies · run and usage instructions
4. An **open-source license file**
5. The app must be **installable and runnable on the Copilot+ PC from our own instructions**
6. **Every team member must submit the feedback form** — gates prize eligibility
7. Must demo to be eligible for a prize

*Recommended but optional:* tests + testing instructions, a Notes section, references, well-commented code. **We do all four** — they are cheap and feed the Documentation score.

**Evaluation criteria — point-weighted (orientation p.8):**

| Criterion | Points | What is actually evaluated |
|---|---|---|
| **Technical Implementation** | **40** | resource utilization, optimization, **latency and performance**, **energy efficiency** |
| Application Use-Case & Innovation | 25 | problem solving, creativity and uniqueness, user experience |
| Deployment & Accessibility | 20 | **ease of installation and use** |
| Presentation & Documentation | 15 | clarity of explanation, code quality and documentation |

> **The heaviest criterion is scored on measurements, not claims.** 40 of 100 points ride on
> latency, resource use and energy efficiency — so we measure them (`hub/benchmark.py`) and
> put the numbers on a slide. See Decision 5.

> **Deadline conflict, resolved:** the schedule slide says *"12pm: Submissions / Survey due"*;
> the submission slide says *"1 PM PST"*. **Assume 12:00 PM.** Submit by **10:30 AM**.
> Confirm at Tuesday office hours.

---

## 0. The five decisions that de-risk this project

Read these before anything else. They are the difference between a working demo and a broken one.

### Decision 1 — The LLM never does arithmetic

The single biggest failure mode for an "AI saves you energy" demo is a judge asking *"where did that number come from?"* and the answer being "the model said so."

**Rule:** Python computes every number deterministically (watts → kWh → dollars → kg CO₂). The LLM receives those numbers already computed and is only allowed to **explain, prioritize, and phrase** them. If the LLM output disagrees with the computed number, the computed number wins and the LLM text is discarded.

This is enforced in `hub/energy_model.py` (math) and `hub/llm.py` (narration only, with a schema-validated response and a deterministic template fallback). It is also your best talking point: *"our savings estimates are auditable — the model explains, it does not invent."*

It also matches QUAD's own stated philosophy — *"honest, not magic… no fabricated results"* — which is the vocabulary the organizers themselves use.

### Decision 2 — The Arduino UNO Q is a Snapdragon Linux computer, not a classic Arduino

The UNO Q is a **dual-brain** board: a Qualcomm **Dragonwing QRB2210** (quad-core Arm Cortex-A53) running **Debian Linux** alongside an **STM32U585** microcontroller. Most teams will use it as a dumb sensor board over USB serial. Do not.

**Use both brains and say so on stage:**
- **MCU (STM32U585, Cortex-M33, running Arduino sketches over Zephyr):** hard real-time sensor sampling — PIR occupancy, LDR/lux, temperature. Streams compact JSON lines. Also **drives the actuator** (Decision 4).
- **Linux (Dragonwing QRB2210, quad-core Cortex-A53, Debian, 4 GB):** runs Python, holds a real MQTT client, does local debouncing/aggregation, and publishes to the broker over Wi-Fi. **No USB tether to the PC required.**

This turns your "IoT leg" from a sensor cable into a genuine third compute node, which is exactly what the rules reward: *"the focus is on how systems work together."*

**Confirmed from the orientation deck:** the MPU↔MCU link is a built-in **RPC library ("Arduino Bridge")**, and the board has a **Qwiic connector for Modulino** sensor nodes. **App Lab** is the recommended IDE and targets both brains together; Arduino IDE/CLI programs *only* the MCU.

> **Day-1 discovery task — do not skip, do not guess.** The exact App Lab **Python API** (module names, Bridge call signatures, what "Bricks" are, the project layout) could not be verified off-site. Find it on the actual board: `pip list` on the Linux side, and read the **App Lab example projects**, which show the sketch-side registration and the Python-side call as a matched pair. **Nobody on the team should invent this API** — if it resists, fall back to plain serial (which is what our tested code already uses).

### Decision 3 — Add the cloud leg; your proposal is missing it

The rules state every team gets a **Qualcomm AI Cloud 100** and frame the event as *"compute, mobile, IoT, **and cloud**."* Your accepted proposal lists no cloud component. Judges will notice the gap.

**Cheapest credible cloud leg — the "heavy thinker":**

| Tier | Where | Model | Job | Latency |
|---|---|---|---|---|
| Edge | UNO Q (Dragonwing) | none — rules only | debounce, threshold, publish | ms |
| **Hub** | Copilot+ PC (X Elite, 45 TOPS NPU) | small local LLM via **GenieX** | real-time narration of live events | < 2 s |
| **Cloud** | AI Cloud 100 | larger model (8–20B) | on-demand **whole-home report**: patterns, weekly plan, ranked retrofits | seconds, off critical path |

The demo story becomes *"instant answers stay on-device and private; the deep weekly analysis bursts to the accelerator."* Put the cloud call **off the critical demo path** — it is a button, not a dependency. If the AI 100 is unavailable, the same code path points at any OpenAI-compatible endpoint and the architecture slide is unchanged.

This is exactly the hybrid pattern Ray Stephenson's orientation talk presented as the reference architecture (local → cloud → local). Use his framing; it is the organizers' own.

### Decision 4 — Close the loop physically (the archetype requires it)

**This reverses an earlier position.** The first draft of this plan said *"recommendations are the stated scope; do not drift into automation."* The organizers' project sheet assigns **Archetype E — IoT Sensor → Actuator Physical AI**, whose defining characteristic is *closing the loop from sensing to physical action*. Advice-only would leave the single heaviest criterion arguably unmet.

**Design: human-approved actuation.** This keeps the "concierge" identity while satisfying the archetype:

```
sense → reason → recommend → USER APPROVES → physically act → confirm → book the saving
```

- The dashboard and phone each gain an **Apply** button on a recommendation card.
- Approval publishes `home/command/<room>/<load>`; the UNO Q drives a **servo or relay** and publishes `home/actuator/...` confirmation.
- The hub then closes the finding and books the saving as **realized** rather than **avoidable** — so the UI can distinguish "you could save $X" from "you saved $X."
- **The comfort guardrail (R7) becomes a pre-flight gate on physical action**, not merely advisory. A command that R7 would suppress is *refused*. This is the strongest safety story available to us: *the guardrail does not just filter advice, it prevents the machine from acting.*

Hardware, cheapest credible first: micro-servo pressing a real light switch → relay module on a lamp → LED + buzzer as a last-resort indicator. Modulino over Qwiic if supplied.

**Actuation is never cut.** It is the archetype.

### Decision 5 — Measure, because 40 points are scored on measurements

The Technical Implementation criterion (40 pts) evaluates *resource utilization, optimization, latency and performance, and energy efficiency*. Claims score nothing here; numbers score.

- **`hub/benchmark.py`** produces the table: narration latency p50/p95 (NPU vs deterministic control), rules-engine latency, end-to-end sensor→recommendation, peak RSS, and the broker-traffic reduction from edge filtering (which we already implemented and never quantified).
- **QUAD's `profile_workload` / `/quad-profile`** gives authoritative NPU latency, power and utilization on real silicon. There is **dedicated QUAD support all week** plus office hours — use them.
- Add a slide: **"An energy-saving app that measures its own energy cost."** The symmetry is the point, and it directly answers the criterion in its own language.

> **What QUAD is and is not, for us.** QUAD is driven from Claude Code / an MCP client, which you do not have on your own machine on site — so use it at the **dedicated QUAD support sessions**. Its value to us is **profiling**, not codegen: the organizers' own sheet marks `generate_code` as **Blocked by gap G6** (UNO Q sensor/actuator + GPIO codegen, not started). We do not need that stage — our sketch, publisher and actuator path are hand-written and tested. **Say this on stage:** we routed around a known platform gap instead of waiting on it. That reads as engineering judgment, not a shortfall.

---

## 1. Architecture

```
┌────────────────────────────────────┐
│  Arduino UNO Q                     │
│  ┌──────────────┐ ┌──────────────┐ │
│  │ STM32U585    │ │ Dragonwing   │ │  MQTT over Wi-Fi
│  │ Cortex-M33   │ │ QRB2210      │ │────────────────┐
│  │ (Zephyr)     │→│ Debian/Python│ │                │
│  │ PIR/LDR/temp │←│ MQTT client  │ │◀──── commands  │
│  │ SERVO/RELAY ⚙│ │ edge filter  │ │                │
│  └──────────────┘ └──────────────┘ │                ▼
└────────────────────────────────────┘   ┌────────────────────────────┐
                  ▲                      │ Copilot+ PC (Snapdragon    │
                  │ physical action      │ X Elite, 45 TOPS NPU)      │
                  │                      │        ORCHESTRATOR        │
┌──────────────────────────────┐         │                            │
│  Galaxy S25 (Snapdragon      │         │  mosquitto broker          │
│  8 Elite)                    │────────▶│  FastAPI + WebSocket       │
│  PWA: presence, geofence,    │         │  rules engine (determin.)  │
│  notifications, APPROVE ✓    │         │  energy model (determin.)  │
└──────────────────────────────┘         │  GenieX LLM ── narration   │
                                         │  R7 guardrail ── gates     │
       ┌───────────────────────┐         │     physical action        │
       │ Qualcomm AI Cloud 100 │◀────────│  deep report (off-path)    │
       │ weekly deep report    │         └────────────┬───────────────┘
       └───────────────────────┘                      │ WebSocket
                                                      ▼
                                             Dark dashboard (browser)
```

**Data flow, one sentence:** sensors + phone presence → hub fuses into a *situation snapshot* → deterministic rules fire *findings* → energy model attaches *auditable savings* → LLM turns findings into *natural-language recommendations* → user approves → **UNO Q physically actuates** → saving booked as realized.

### MQTT topic contract — freeze this on Day 1

Every team member codes against this and nothing else. Changing it mid-week is what kills integration.

| Topic | Publisher | Payload |
|---|---|---|
| `home/sensors/<room>` | UNO Q | `{"occupancy":bool,"lux":int,"temp_c":float,"humidity":float,"ts":epoch}` |
| `home/loads/<room>/<load>` | UNO Q / sim | `{"state":"on"\|"off","watts":float,"ts":epoch}` |
| `home/context/user` | phone PWA | `{"presence":"home"\|"away","distance_m":int,"battery":int,"ts":epoch}` |
| `home/reco` | hub | `{"id":str,"severity":str,"title":str,"body":str,"kwh":float,"usd":float,"co2_kg":float,"actions":[str]}` |
| **`home/command/<room>/<load>`** | **hub** | **`{"action":"on"\|"off","reco_id":str,"approved_by":str,"ts":epoch}`** |
| **`home/actuator/<room>/<load>`** | **UNO Q** | **`{"state":"on"\|"off","source":str,"reco_id":str,"ok":bool,"ts":epoch}`** |
| `home/state` | hub | full snapshot for dashboard/debug |

The two **command/actuator** topics are the actuation loop (Decision 4). They were added, not changed — every existing publisher and subscriber is unaffected.

**Rule:** all payloads are JSON, all carry `ts`, all units are in the key name (`_m`, `_c`, `_kg`). No exceptions.

---

## 2. Team roles (3–5 people)

Assign owners **Monday morning**. Every owner also owns their section of the README.

| Owner | Scope | Day-1 deliverable |
|---|---|---|
| **A — Hub/AI lead** (you) | repo, MQTT contract, rules engine, energy model, LLM client, GenieX | broker up + simulator → dashboard end-to-end |
| **B — Embedded** | UNO Q both brains, sensors, **actuator**, publisher | one real sensor value on the broker |
| **C — Mobile** | PWA: presence/geofence, notifications, **Approve button** | phone reaches PC over Wi-Fi, publishes presence |
| **D — Front-end / story** | dashboard, README, slides, demo video | dashboard shell rendering simulated data |
| **E — Cloud / measurement** | AI Cloud 100 report, **QUAD profiling**, `benchmark.py`, judge-proofing | cloud endpoint reachable, one report generated |

**If you are 3 people:** A takes E's cloud work, D takes C's PWA, B stays embedded (sensors **and** actuator — that is the archetype, so protect B's time). Cut the chat panel first.

**Everyone, individually:** submit the **feedback form** when it arrives Thursday morning. Prize eligibility requires *all* members to have submitted by Friday noon. Owner D tracks completion.

---

## 3. Day-by-day

**Logistics fixed from the orientation deck:**
- **Wi-Fi: `HaQathon` / `tA20LO26s`.** Do **NOT** use Hydra or Pandora.
- Laptop login `QCWorkshopX` / `QCWorkshop123`, PIN `13243546`.
- **Office hours:** Tue & Thu **1:30–3:30 PM**, Fri **9–12**. Dedicated QUAD support all week. Discord `#support` for virtual help.
- **AZ MPR open until 11 PM** (after-hours is personal time). Attendance is optional except kickoff and demos.

### Day 1 — Mon Aug 3 (dev begins 11:30 AM): skeleton walking end-to-end
Goal: **a fake but complete pipeline**. Nothing real, everything connected.

- [ ] Collect devices. Confirm UNO Q image and whether the Linux side is exposed.
- [ ] **COMPLIANCE, 45 min, do it now while it is cheap:**
  - [ ] Create the **personal, public** GitHub repo; push the packet; add everyone as collaborators.
  - [ ] `LICENSE` present (MIT — already in the packet).
  - [ ] Fill the **team table** in `README.md` with every member's **name and email**.
  - [ ] Confirm the deadline at office hours (**12:00 PM vs 1:00 PM** — the two decks disagree).
- [ ] **Freeze the MQTT contract** (above, including the two new command/actuator topics) in `docs/CONTRACT.md`.
- [ ] Install mosquitto with the provided `mosquitto.conf`. Open port 1883. Confirm phone **and** UNO Q reach it.
- [ ] `python smoke_test.py` → expect **22/22**. This proves the environment before anyone writes code.
- [ ] Run `hub/server.py` + `hub/simulator.py --mode demo` → dashboard shows moving data and real recommendations.
- [ ] **GenieX:** install, `geniex pull`, `geniex serve`, point `LLM_BASE_URL` at `:18181`, get one real narration.
- [ ] **App Lab discovery (B):** find the real Bridge/RPC Python API on the board. Do not invent it.

**Exit gate:** simulated sensor → rule fires → number computed → text on dashboard. If this is not working by end of Day 1, cut scope, not quality.

### Day 2 — Tue Aug 4: make it real, and close the loop
- [ ] UNO Q publishes real PIR + lux + temp to the broker (B).
- [ ] **Actuator wired and driven end-to-end** — approve on the phone, servo/relay moves, confirmation returns (B + A). *This is the archetype; it outranks everything else today.*
- [ ] `POST /api/apply` with the **R7 pre-flight gate**; realized-vs-avoidable savings in the UI (A).
- [ ] Phone PWA publishes real presence; **Approve button** works (C).
- [ ] Dashboard on live data, KPI tiles + power sparkline + Apply (D).
- [ ] **Office hours 1:30–3:30 → get QUAD connected and run the first `/quad-profile`** (E).
- [ ] AI Cloud 100 reachable; `hub/cloud_report.py` returns one real report (E).

**Exit gate:** walk out of the room with the light on → phone buzzes → tap Approve → **the light physically switches off** → the dashboard books the saving as realized.

### Day 3 — Wed Aug 5: measurement and differentiators
**Measurement is not optional — it is the 40-point criterion.**
- [ ] **`hub/benchmark.py`** run and results captured: narration latency p50/p95 (NPU vs deterministic control), rules latency, end-to-end sensor→reco, peak RSS, edge-filter traffic reduction (E).
- [ ] **QUAD `/quad-profile` + `/quad-orchestrate`** report captured into `README.md` and the deck (E).
- [ ] NPU-vs-CPU comparison table finished — this is the slide that earns Technical Implementation points.

Then pick **two** differentiators, no more:
- [ ] **Auditable savings panel** — already built; make sure it is demoed explicitly.
- [ ] **Cloud deep report** — "Weekly Plan" button → AI 100 → ranked actions with payback.
- [ ] **Tariff/time-of-use awareness** — already built; SDG&E 4–9 PM peak, "shift the dryer."
- [ ] **Natural-language chat** — "why is my bill high?" against real logged state.

### Day 4 — Thu Aug 6: FREEZE at 5 PM
**No new features today.** This day wins or loses 35 of the 100 points.
- [ ] 09:00 Feature freeze. Bugs only.
- [ ] **Demo order arrives by email this morning — it is randomized. Assume you are first.** Be fully demo-ready tonight.
- [ ] **FEEDBACK FORM: every team member submits.** Link arrives Thursday morning. **Prize eligibility requires all members.** Owner D confirms each person individually — do not assume.
- [ ] README final: description, architecture, **setup a stranger can follow**, usage, license, team names+emails, benchmark table, QUAD report, tests, notes, references.
- [ ] **Stranger install test** — a teammate does `git clone` → running dashboard on a clean machine without asking questions. This *is* the 20-point Deployment criterion.
- [ ] Build the 5-minute deck (see `02_DEMO_SCRIPT.md`), including the efficiency slide.
- [ ] **Record a full video of the working demo**, including the physical actuation. Insurance against venue Wi-Fi. Non-negotiable.
- [ ] Rehearse **three times with a timer.**
- [ ] Office hours 1:30–3:30 for any final QUAD numbers.
- [ ] Tag a release. Verify a fresh `git clone` runs.

### Day 5 — Fri Aug 7: submit early
- [ ] 09:00 final smoke test on venue Wi-Fi, at the actual demo table.
- [ ] **10:30 submit** the GitHub repo link via the **Microsoft Form** (one submission per team). Deadline is **12:00 PM** — do not cut it close.
- [ ] 10:45 confirm **all** feedback forms are in.
- [ ] **13:00–16:15 demos** (your slot was emailed Thursday). 16:30 device collection. **17:00 winners.**
- [ ] Vote for **Team's Choice** — one vote per team, cannot vote for yourselves.

---

## 4. Demo failure chain — decide this Thursday, not on stage

Venue Wi-Fi will be hostile. Have all five rungs ready:

1. **Full live:** UNO Q + phone + PC, all over venue Wi-Fi (`HaQathon`).
2. **PC hotspot:** PC hosts its own AP; UNO Q + phone join that. **Likely your primary — rehearse it as the default.**
3. **Simulator:** `hub/simulator.py --mode demo` replays a scripted scenario through the real rules engine, real energy model, real LLM, real dashboard. Only the sensors are fake. Honest *if you say so out loud.*
4. **REST injection:** no broker at all — `curl` the `/api/sensor`, `/api/load`, `/api/presence` endpoints.
5. **Video:** the Thursday recording.

**Actuation-specific fallback:** if the servo/relay fails on stage, fall back to the LED/buzzer indicator and say plainly that the command reached the board and the actuator is the failed link. Have the **video of the working physical actuation** ready — for Archetype E this is the single most important thing to capture on Thursday.

Rehearse the switch between rungs 1→2→3 so it takes under 20 seconds. Practice saying it calmly: *"venue Wi-Fi is fighting us, switching to our simulated sensor feed — everything downstream is live."* Judges respect a prepared fallback; they punish fumbling.

---

## 5. Scope discipline — cut list, in cut order

When you are behind (you will be), cut from the top:

1. Native Android app → PWA is enough
2. Chat panel
3. Historical database / multi-day charts
4. Multi-room (one room demos identically)
5. Cloud deep report → the deterministic fallback path still demos the tier
6. Servo pressing a real switch → downgrade to a relay, then to an LED + buzzer indicator

**Never cut:** the auditable number · **the physical actuation** (it is the archetype) · **the measured latency/power numbers** (40 points) · the phone approval moment · the README with license and team emails · **every member's feedback form** · the video.

> **Reversed from the first draft.** This list previously read *"Actual device control (relays) → recommendations are the stated scope; do not drift into automation."* That was written before we saw the organizers' project sheet assigning **Archetype E (IoT Sensor → Actuator Physical AI)**. Actuation is now a requirement, not a stretch goal. Likewise, "NPU inference is optional, mention it as roadmap" was wrong — **energy efficiency and latency are explicitly scored**, so GenieX on the NPU plus measured numbers moved from optional to core.

---

## 6. What "innovation" means to these judges

Your use case (home energy) is not novel by itself — assume another team has something adjacent. Your differentiation is the **orchestration quality**, which is precisely what the rules say they care about. Lead with these four claims:

1. **Four-tier AI placement is deliberate, not accidental** — rules at the edge (ms), small LLM on-device via GenieX for privacy and latency (< 2 s), large model in cloud for depth. Have a slide that justifies *why each* piece of intelligence lives where it lives.
2. **Auditable AI** — the model narrates, arithmetic is deterministic and inspectable. Rare and immediately credible. It is also the organizers' own stated value: QUAD's pitch is *"honest, not magic."*
3. **Context fusion is what unlocks it** — no single device could produce the recommendation; it requires phone presence AND room occupancy AND load state AND time-of-use tariff together. Say this explicitly: *"this recommendation is impossible on any one of these devices alone."*
4. **The loop actually closes, and safely** — sense → reason → recommend → human approves → **physical action** → confirmation → realized saving. And the comfort guardrail **gates the actuator**, so the system will refuse to execute its own advice when the room is too hot. That is the difference between automation and judgment.

**For the 40-point criterion specifically, lead with numbers, not adjectives:** narration latency on NPU vs CPU, rules-engine latency, peak memory, and the broker-traffic reduction from edge filtering. *"An energy-saving app that measures its own energy cost"* is the line that ties the whole thing together.

**For Team's Choice** (voted by the other teams, not the judges): engineers remember the audit panel and the servo physically flipping a switch. Make sure other teams actually watch your demo.

---

## 7. Files in this packet

| File | Use |
|---|---|
| `00_MASTER_PLAN.md` | this document |
| `01_LLM_PROMPT_PACK.md` | **copy-paste prompts for the on-site LLM** + the QUAD section |
| `02_DEMO_SCRIPT.md` | minute-by-minute 5-min demo + slides + Q&A prep |
| `03_SETUP_CHEATSHEET.md` | commands for broker, GenieX, QUAD, UNO Q, phone, actuator |
| `04_ORGANIZER_REQUIREMENTS.md` | **compliance checklist distilled from the organizer decks** — tick every box |
| `code/` | working backbone — clone into the repo Day 1 |

Carry this whole folder in on a USB stick **and** push it to the repo from home before Monday. Do not rely on having network access to get your own plan.
