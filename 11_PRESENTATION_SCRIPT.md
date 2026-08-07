# 11 — Three-minute presentation script

Judging is **Technical Implementation 40 · Use-Case & Innovation 25 ·
Deployment & Accessibility 20 · Presentation & Documentation 15**. This script is
weighted to match: the live loop and the measured numbers get the most time, and every
claim in it is a figure this repo can reproduce.

Speaking time **2:58** at 150 wpm — 446 spoken words, leaving room for the demo pauses
(five seconds of silence on the bulb, ~3.4 s while the answer streams). Stage directions
in brackets are not read aloud.

---

## [0:00 – 0:20] The problem

> Everyone's power bill went up. Almost nobody can tell you *which appliance* did it.
> Your utility sends one number a month, and by then the money is spent.
>
> This is a home energy concierge that runs **entirely on this desk** — no cloud,
> nothing leaving the room. And it doesn't just report. It acts.

## [0:20 – 0:40] What it is

*[Point at the board, then the laptop, then the lamp.]*

> An Arduino UNO Q senses the room. A Snapdragon X Elite reasons on the Hexagon NPU.
> Real TP-Link devices carry out the decision.
>
> Sense, decide, physically act. Three tiers of AI — and picking the *cheapest tier that
> can do each job* is the whole design.

## [0:40 – 2:00] The live loop

*[Press **C** first, off-camera, to reach steady state.]*

> The house at steady state: lights on, HVAC on, someone home. Six-thirty, on-peak —
> the most expensive electricity of the day.

*[Press **A**.]*

> I've just left. Watch the lamp.

*[Bulb goes dark in about five seconds. Say nothing — let it land.]*

> No one approved that. A rule fired in **fourteen microseconds** and switched a real
> device. The model isn't in that path — it explains afterwards, it doesn't gate the
> action.

*[Open `/ask`, click **"Why is my bill high?"**]*

> This is thirty-seven days of **real utility data** — fifteen-minute meter readings,
> real SDG&E time-of-use rates.

*[Answer streams in.]*

> A hundred and fifty-seven kilowatt-hours on heating and cooling, fifteen dollars of it
> at peak. That's a 4-billion-parameter model on the NPU, in this room. And every number
> it said is **checked against the source** — that's the verified badge.

*[Turn the Modulino knob until the room reads 29 °C. Click **Approve** on the A/C card.]*

> The room is now twenty-nine degrees. I'll approve its own recommendation to kill the
> air conditioning.

*[409 refusal appears.]*

> It refuses. Its own advice, blocked because you'd be uncomfortable. You can override
> it — and that's recorded as a *human* override, not the system's idea.

## [2:00 – 2:30] Technical implementation

> Everything here is measured. `benchmark.py` reproduces it.
>
> Three tiers: a trained classifier at **30 microseconds** on the board's A53, pure
> Python. Rules at **fourteen microseconds**. The model at **3.3 seconds**, called once
> per *change* of the finding set — not per cycle.
>
> Edge filtering drops **88.7%** of broker traffic. The whole stack is **34 megabytes**
> resident.
>
> And on this NPU, latency tracks **output length**, not model size. Capping answers at
> 160 tokens is a performance decision, not a style one.

## [2:30 – 2:45] Deployment

> One command. No build step, no npm, no CDN — the dashboard is a single HTML file.
> Scan a QR code and it's on your phone.
>
> It degrades honestly: kill the broker, the model, or the board and it keeps running
> and *tells you* what it lost.

## [2:45 – 3:00] Close

> One thing I'll volunteer: the lamp and plug are real, with real meters, but their
> magnitudes are scaled to a real household — the dashboard says so on every row.
>
> Thirty-two tests. Every number traceable to a rule and a threshold. The provenance
> check costs **110 microseconds**, and it's why I can show you a *verified* badge
> instead of asking you to trust me.

---

## Criteria map

| Criterion | Where it lands |
|---|---|
| **Technical Implementation (40)** | 2:00–2:30 block: three tiers with measured p50s, 88.7% traffic reduction, 34 MB RSS, output-length insight. Plus 0:40–2:00, where the 14 µs rule is what actually switches the bulb. |
| **Use-Case & Innovation (25)** | 0:00–0:20 problem framing; the refusal at 1:40 — an assistant that declines its own recommendation is the memorable moment; real utility data rather than synthetic. |
| **Deployment & Accessibility (20)** | 2:30–2:45: one command, no build, single-file dashboard, QR to phone, honest degradation. |
| **Presentation & Documentation (15)** | The whole script is claim-then-evidence; close cites 32 tests and the provenance mechanism. Repo has GLOSSARY.md, a 2,400-line session log, and cited sources on every constant. |

## Delivery notes

- **The five seconds of silence while the bulb goes dark is the most valuable time in
  the talk.** Do not narrate over it.
- Lead with the refusal if you overrun — it is the one beat nobody else will have.
- Say "scaled stand-in" once, clearly. Volunteering it reads as rigour; being asked
  about it later reads as a gap.
- If GenieX is slow, the answer still streams — keep talking, do not stare at the screen.
- Numbers to never round: **30.6 µs**, **0.014 ms**, **3.3 s**, **88.7%**, **110 µs**,
  **157.12 kWh**, **32/32**.

## Pre-flight

```bash
curl -s -o /dev/null -w "%{http_code}\n" --max-time 20 http://localhost:18181/v1/models  # GenieX
curl -s http://localhost:8000/api/pacing        # grace 1, eval 1, settle 3
adb -s 3933751369 shell "grep -a 'actuator:' /tmp/publisher.log"   # want "actuator: kasa"
```

Bulb's **wall switch on**. Fan **plugged into the smart plug**. Narration must read
`(llm)`, not `(template)`.
