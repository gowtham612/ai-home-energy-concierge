# Organizer requirements — compliance checklist

Distilled from **`Snapdragon Multiverse Hackathon_Internal.pdf`** (orientation, 3 Aug 2026) and **`23-AI-Home-Energy-Concierge.pdf`** (our per-team project sheet). Tick every box. Anything unticked on Friday morning is either lost points or lost eligibility.

---

## A. Blocking — no prize without these

| ☐ | Requirement | Source | Where it lives |
|---|---|---|---|
| ☐ | **Personal GitHub repo**, public | p.7 | — |
| ☐ | **All code open source** | p.7 | stated in `README.md` § License |
| ☐ | **Open-source license file** | p.7 | `code/LICENSE` (Apache 2.0) ✅ *in packet* |
| ☐ | README: **application description** | p.7 | `README.md` top ✅ |
| ☐ | README: **names AND emails of every member** | p.7 | `README.md` § Team — **table has placeholders, fill it** |
| ☐ | README: **setup instructions from scratch, incl. dependencies** | p.7 | `README.md` § Setup ✅ |
| ☐ | README: **run and usage instructions** | p.7 | `README.md` § Usage ✅ |
| ☐ | **App runnable from our own instructions** | p.7 | verify by stranger test Thursday |
| ☐ | **Installs and runs on the Copilot+ PC**, functions as described | p.7 | verify on the actual device |
| ☐ | **EVERY member submits the feedback form** by Fri noon | p.7, p.9 | link emailed **Thursday AM** — owner D tracks each person |
| ☐ | **Repo link submitted via the Microsoft Form**, one per team | p.7, p.41 | Fri, target **10:30 AM** |
| ☐ | **Team demos the project** (required for eligibility) | p.8 | slot emailed Thursday AM, randomized |

## B. Scored — the rubric, and where we earn each part

**Technical Implementation — 40 pts.** *Resource utilization, optimization, latency and performance, energy efficiency.*

| ☐ | Evidence to produce |
|---|---|
| ☐ | `hub/benchmark.py` results: narration latency p50/p95, NPU vs deterministic control |
| ☐ | Rules-engine latency (expect sub-millisecond) |
| ☐ | End-to-end sensor → recommendation latency |
| ☐ | Peak RSS of the hub process |
| ☐ | Broker-traffic reduction from edge filtering on the UNO Q (already implemented — just surface the number) |
| ☐ | **QUAD `/quad-profile`** report: real NPU latency / power / utilization |
| ☐ | **QUAD `/quad-orchestrate`**: CPU vs GPU vs NPU allocation comparison |
| ☐ | Justified runtime choice: GenieX `qairt` (NPU-only, fastest) vs `llama_cpp` (GGUF `Q4_0`, flexible) |
| ☐ | Slide: *"An energy-saving app that measures its own energy cost."* |

**Application Use-Case & Innovation — 25 pts.** *Problem solving, creativity/uniqueness, UX.*

| ☐ | Evidence |
|---|---|
| ☐ | Four-tier AI placement, each justified by latency + privacy budget |
| ☐ | Auditable arithmetic — LLM narrates, Python computes |
| ☐ | Cross-device context fusion no single device could do |
| ☐ | Closed loop with human approval + R7 gating physical action |

**Deployment & Accessibility — 20 pts.** *Ease of installation and use.*

| ☐ | Evidence |
|---|---|
| ☐ | 3-command no-hardware quickstart |
| ☐ | `smoke_test.py` — 22/22, gives a stranger instant confidence |
| ☐ | **Stranger install test passed** on a clean machine |
| ☐ | `requirements.txt` complete (note: `uvicorn[standard]`, not plain `uvicorn`) |
| ☐ | Every dependency and fallback documented |

**Presentation & Documentation — 15 pts.** *Clarity of explanation, code quality and documentation.*

| ☐ | Evidence |
|---|---|
| ☐ | 5-min demo rehearsed 3× with a timer |
| ☐ | Architecture diagram in README |
| ☐ | Well-commented code (already true — keep it that way) |
| ☐ | *Optional but recommended, all four:* tests + testing instructions · Notes section · References · well-commented code |

## C. Our assigned archetype — the defining requirement

> **Archetype E — IoT Sensor → Actuator Physical AI.**
> *"Close the loop from sensing to physical action — servos, lenses, robotic arms, haptics — driven by the Arduino UNO Q + X-Elite brain."*

| ☐ | Requirement |
|---|---|
| ☐ | A **physical actuator** on the UNO Q actually moves |
| ☐ | It is driven by the sensing + reasoning pipeline, not a manual toggle |
| ☐ | Demonstrated live (and **captured on video** Thursday as insurance) |

**Do not describe this project as "recommendations only."** The first draft of our plan did; that language is now removed everywhere.

## D. The QUAD workflow on our sheet — and how we deviate, deliberately

The organizers' prescribed sequence, with their own verdicts:

| Stage | Their verdict | What we do |
|---|---|---|
| `hardware_detect` | ✅ Automated | Run it — free, and confirms the target |
| `convert_model` | ⚠️ Human (supply calibration, verify accuracy) | Only if we convert a model; GenieX AI Hub bundles are pre-compiled |
| `profile_workload` | ⚠️ Human | **Run it — this is our main use of QUAD.** Produces the 40-point evidence |
| `orchestrate_workload` | ⚠️ Human | Run it for the CPU/GPU/NPU comparison |
| `generate_code` | ❌ **Blocked by G6** | **We bypass it.** Our sketch, publisher and actuator code are hand-written and tested |
| Deploy + integrate | ⚠️ Human | Manual, as expected |

**Primary gap G6 (PENDING):** *Arduino UNO Q sensor/actuator + GPIO codegen (App Lab / Modulino / MPU-MCU bridge) — not started.* Their suggested workaround is QUAD's mock path.

**Our position, and say it on stage:** we did not need the blocked stage. We wrote and tested that layer by hand, so a pending platform gap did not become a project blocker. That is a point in our favour, provided we state it plainly rather than pretending the stage ran.

> **Practical note:** QUAD is driven from **Claude Code or another MCP client**. If you do not have one on your machine on site, use the **dedicated QUAD support sessions** and office hours (Tue/Thu 1:30–3:30, Fri 9–12).

## E. Logistics

| Item | Value |
|---|---|
| **Wi-Fi** | `HaQathon` / `tA20LO26s` — **do NOT use Hydra or Pandora** |
| Laptop login | `QCWorkshopX` / `QCWorkshop123`, PIN `13243546` |
| Dev begins | Mon 11:30 AM |
| Office hours | Tue & Thu 1:30–3:30 PM · Fri 9 AM–12 PM |
| Virtual support | Discord `#support` channel |
| Room | AZ MPR, open to 11 PM (after-hours is personal time) |
| **Submission due** | **Fri 12:00 PM** (schedule slide) — submission slide says 1 PM. **Assume 12:00. Confirm Tuesday.** |
| Demos | Fri 1:00–4:15 PM, **randomized order emailed Thursday AM** |
| Device collection | Fri 4:30–5:00 PM |
| Winners | Fri 5:00 PM · social 5–6 PM |
| Prizes | Top Award (judges) · Team's Choice (teams vote, 1 vote, not your own) · proposal raffle (first 15 teams) |

## F. Hardware, confirmed specs

| Device | Silicon | Notes |
|---|---|---|
| Copilot+ PC | Snapdragon X Elite — Oryon CPU, Adreno GPU, **Hexagon NPU 45 TOPS**, 32 GB | hub / orchestrator |
| Galaxy S25 | Snapdragon 8 Elite — Oryon, Adreno, Hexagon NPU, 12 GB | phone PWA |
| Arduino UNO Q | **Dragonwing QRB2210** quad-core Cortex-A53 + Adreno + **STM32U585 MCU**, **4 GB** | MCU runs sketches over **Zephyr**; MPU↔MCU via built-in **RPC ("Arduino Bridge")**; **Qwiic** for Modulino nodes; App Lab targets both brains |
| Cloud AI 100 | — | off-critical-path deep report |

## G. Useful resources from the deck

- **GenieX** — `github.com/qualcomm/geniex` · OpenAI-compatible server on `:18181` · GGUF + AI Hub bundles · NPU/GPU/CPU
- **Qualcomm AI Hub** — `aihub.qualcomm.com` (filter `runtime=geniex_qairt,geniex_llamacpp`)
- **Arduino RPC example** — `github.com/qualcomm/edge-ai-labs-arduino/tree/main/rpc` ← **directly relevant to our MPU↔MCU actuation path; read this first on Day 1**
- **NPU chatbot w/ AnythingLLM** — `github.com/thatrandomfrenchdude/simple-npu-chatbot`
- **AI Developer Workflow docs** — `docs.qualcomm.com/bundle/publicresource/topics/80-62010-1/welcome.html`
- **QAIRT Visualizer** — `docs.qualcomm.com/doc/80-87189-1/topic/overview.html`
- **Past hackathon projects** — `qualcomm.github.io/awesome-qualcomm-developer/`
