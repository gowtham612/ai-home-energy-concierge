# ⚠ CLEANUP REMINDER — read before the judges do

**You chose to push the entire packet, including internal planning documents.** That was a
deliberate call for convenience; this file is the reminder you asked for.

## Decide before Friday: keep or remove?

These files are **internal strategy**, not deliverables. They are currently **public**:

| File | Why you might want it gone |
|---|---|
| `01_LLM_PROMPT_PACK.md` | Reads as "how we drove an LLM to write this." Accurate and unembarrassing, but it is scaffolding, not product. **Most likely to remove.** |
| `02_DEMO_SCRIPT.md` | Contains your **Q&A prep** — the anticipated judge questions and prepared answers. A judge reading this before your demo changes the dynamic. |
| `00_MASTER_PLAN.md` | Contains the honest internal reasoning, including *"this reverses an earlier position"* and the cut list. Shows good engineering process; also shows the seams. |
| `04_ORGANIZER_REQUIREMENTS.md` | Quotes the organizers' internal decks, including the **venue Wi-Fi password and laptop login credentials**. |

## Act on this one now, not later

`04_ORGANIZER_REQUIREMENTS.md` §E contains:

- Wi-Fi SSID `HaQathon` and its password
- Laptop login `QCWorkshopX` / `QCWorkshop123` and PIN `13243546`

**These are shared event credentials in a public repository.** Low harm — they are
short-lived, venue-local, and were handed to every participant — but publishing them is
still the wrong default. Recommended: redact those four values and keep the rest of the
file. It is genuinely useful as a compliance record.

The organizer decks are also marked *"Confidential – Qualcomm Technologies… May Contain
Trade Secrets."* You are quoting requirements and a rubric rather than reproducing the
decks, which is a reasonable line — but it is a line worth deciding on consciously rather
than by accident.

## Fastest cleanup

Redact just the credentials:

```bash
cd ~/github/personal/ai-home-energy-concierge
# edit 04_ORGANIZER_REQUIREMENTS.md, replace the Wi-Fi/login values with "<redacted>"
git commit -am "Redact shared venue credentials from the compliance notes"
git push
```

Remove the internal planning docs entirely:

```bash
git rm 01_LLM_PROMPT_PACK.md 02_DEMO_SCRIPT.md
git commit -m "Remove internal planning documents from the public submission"
git push
```

> **Note:** `git rm` removes them from the *current* tree, not from history. Anyone can
> still read them in earlier commits. If they must be genuinely gone, the clean route is to
> delete the repo and re-push from a fresh `git init` with only the files you want — cheap
> to do while the repo is new and has no stars, forks, or external clones.

## What is definitely fine to keep public

- `README.md` — the packet index
- `05_FILE_STRUCTURE_AND_RUN.md` — the operator's manual, genuinely useful to a reader
- `presentation.html` — the deck itself
- `code/` — the submission, Apache 2.0 licensed
- The three screenshots

## Also still outstanding for the submission

Independent of this cleanup decision:

- [x] **Team members' names and emails** — DONE. All four are listed with emails in
      both `code/README.md` § Team and the root `README.md` § Team.
- [x] The GenieX/NPU row in the benchmark table — DONE. Measured with `geniex serve`
      running the `qairt` W4A16 runtime on the X-Elite NPU: narration p50 3110 ms,
      p95 3273 ms, peak RSS 34.0 MB.
- [ ] The `/quad-profile` report — **blocked server-side**, not by us. Two independent
      failures on the hosted QUAD MCP server (broken `qairt-converter`, missing
      ExecuTorch toolchain); see `code/README.md` and `07_QUAD_SESSION_LOG.md` §10.
      The GenieX row above already provides real measured NPU numbers.

**So the redaction decision at the top of this file is now the only open item here.**

And every team member must submit the **feedback form** by Friday noon — it gates prize
eligibility.
