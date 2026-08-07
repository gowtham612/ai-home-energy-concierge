# QUAD setup & session log — AI Home Energy Concierge

**Purpose of this file — two jobs:**
1. **Golden reference.** If you (or an LLM) had to reproduce this entire
   setup from a completely fresh machine, everything needed is below, in
   order, with exact commands.
2. **Continuity.** If this Claude Code session is lost, compacted, or you
   move to a new one, this file is the full state dump — what's done, what
   broke and how it was fixed, what's blocked, and exactly what to do next.

Not part of the required README content — an internal working log (same
spirit as `CLEANUP_REMINDER.md`), so keep/remove it from the public repo at
your own discretion. **No secrets are written into this file** — every
credential is referenced by variable name and storage location only; see
"Secrets" at the bottom.

---

## 0. Resume point — read this first

> ### ⚡ LATEST STATE (2026-08-06, late) — read §21 before anything else
>
> **⚠ This project is now worked on from MULTIPLE HOSTS, each running Claude.**
> Record every change here. A force-push from the other host already destroyed
> work once — see §21.7. **Pull before you push. Never force-push `main`.**
>
> | | |
> |---|---|
> | PC (broker + hub) | `192.168.86.34` |
> | UNO Q board | `192.168.86.51` (SSID `ArtiFi`), ADB serial `3933751369` |
> | `lights` — KL120 "Bedroom light 2" | 🔴 **NOT ON THE NETWORK** |
> | `ac` — HS110 "Space heater" | 🔴 **NOT ON THE NETWORK** |
>
> 🔴 **CURRENT BLOCKER — the actuator is `simulated`.** Both Kasa devices are
> absent: discovery finds only four *foreign* devices on the venue LAN, and direct
> probes of their last-known IPs (`.49`, `.20`) time out. Sensing, cues, presence,
> rules and `/ask` all work; **the real bulb will not switch**, so the Archetype E
> beat cannot be filmed until those two devices rejoin. **Do NOT bind the actuator
> to the venue's devices — they are not ours.** Full detail in §21.6.
>
> **MQTT runs over the USB tunnel on port `11883`, NOT Wi-Fi.** The board runs its
> own mosquitto on `*:1883`, so `adb reverse tcp:1883` silently fails while looking
> healthy. This is the single most misleading failure on the project — §21.3.
> ```bash
> adb -s 3933751369 reverse tcp:11883 tcp:1883    # board.env: MQTT_PORT=11883
> ```
> **A reboot destroys `adb reverse`** — recreate it. `board.env` survives.
>
> **Buttons (current): A = presence away · B = ambient light · C = reset to steady
> state.** This supersedes the old "A = toggle bulb, B = toggle heater, C = rescan"
> mapping — §21.8. Occupancy has been **removed** from the simulator UI; do not
> reintroduce it.
>
> **Run only ONE hub** — two share client_id `hub-orchestrator` and knock each other
> off the broker forever (§21.4). Hub needs its demo flags or `/ask` 404s:
> `AI_ASK=1 AI_PLAN=1 AI_ANOMALY=1 AI_AUTO_LIGHTS=1`.
>
> **`/api/state` shape:** `loads` is **top-level**, keyed `"living/lights"`;
> presence is at **`user.presence`**. Reading `rooms.living.loads` makes a healthy
> system look dead (§21.9).
>
> **⚠ The single worst trap on this project — read §18.1.** If the board looks
> alive but the hub gets nothing, suspect **CRLF in `board.env`** before
> anything else. It makes the publisher serve *invented* sensor data while
> looking perfectly healthy. Check first:
> ```bash
> adb shell "grep -c $'' /home/arduino/ai-home-energy-concierge/code/arduino/board.env"
> ```
> Non-zero = that is your bug. Fix: `sed -i 's/$//' board.env` and restart
> the publisher.

**Current position (2026-08-05): the full Archetype E loop is CLOSED and
verified on real hardware.** Approve on the hub → the actual Kasa bulb
physically goes dark, confirmed by its own energy meter (10.8 W → 0.0 W).

The breadboard plan in `06_UNO_Q_BRINGUP.md` Steps 2-5/9 is **obsolete** —
we had none of that hardware. See `08_HARDWARE_PIVOT_PLAN.md` for the
replacement architecture, and §14 below for what was actually built.

Verified working end to end:

| Piece | Status |
|---|---|
| Modulino Knob on Qwiic (`Wire1`, `0x3A`) | reads + writes, drives `temp_c` |
| MCU firmware (`arduino/sketch`) | 1 Hz JSON, provenance-stamped, no banner |
| MCU→Linux link | `tcp://127.0.0.1:7500` via `arduino-router` |
| Publisher on the board | MQTT + Kasa actuation + metered watts |
| Kasa KL120 bulb / HS110 plug | switch + **real measured watts** |
| Hub, rules, GenieX narration | unchanged, still 32/32 smoke tests |
| Sensor simulator | served at `/simulator` |
| R7 comfort guardrail | refuses at 29.5 °C with HTTP 409 |

**If picking this up cold**, run §0.1 below, then read §14.

### 0.1 Restart everything after a reboot

```bash
# 0. Kill any stale hub FIRST. Two hubs share client_id and fight forever (§21.4).
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*hub*server.py*' -and \$_.Name -like 'python*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }"

# 1. PC: broker + hub. The AI flags default OFF; without them /ask 404s and the
#    autonomous lights never fire — a failure that looks like broken hardware.
cd ai-home-energy-concierge/code
"/c/Program Files/mosquitto/mosquitto.exe" -c mosquitto.conf -v > mosquitto.log 2>&1 &
export AI_ASK=1 AI_PLAN=1 AI_ANOMALY=1 AI_AUTO_LIGHTS=1
export DEMO_GRACE_S=6 DEMO_AWAY_GRACE_S=5 AUTO_COOLDOWN_S=5 EVAL_INTERVAL_S=2 RESET_SETTLE_S=12
nohup ./.venv/Scripts/python.exe hub/server.py > /tmp/hub.log 2>&1 &
curl -s -o /dev/null -w "ask=%{http_code}\n" http://localhost:8000/ask   # MUST be 200

# 2. MQTT tunnel — port 11883, NOT 1883 (§21.3). A reboot destroys this.
ADB="$LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe"
BOARD=3933751369
$ADB -s $BOARD reverse tcp:11883 tcp:1883
$ADB -s $BOARD reverse --list                 # expect  UsbFfs tcp:11883 tcp:1883

# 3. Board: publisher. Use `setsid ... &` and background the whole adb call —
#    `nohup ... &` inside adb shell holds the channel open and blocks.
#    Never `pkill -9 -f`: it kills the adb shell itself (exit 137).
$ADB -s $BOARD shell "pkill -f uno_q_publisher.py"; sleep 3
$ADB -s $BOARD shell "cd /home/arduino/ai-home-energy-concierge/code/arduino \
   && set -a && . ./board.env && set +a \
   && setsid ~/energy-venv/bin/python3 uno_q_publisher.py \
      > /tmp/publisher.log 2>&1 < /dev/null &" &
sleep 15
$ADB -s $BOARD shell "grep -E 'ready|kasa|MQTT connected' /tmp/publisher.log"

# 4. Prove the board's data actually reaches the PC (not the board's own broker):
curl -s http://localhost:8000/api/state | python -c "import json,sys; d=json.load(sys.stdin); print(d['user']['presence'], {k:v['state'] for k,v in d['loads'].items()})"
```

Expect `[kasa] lights -> …`, `[kasa] ac -> …`, and **`subscribed home/command/#`**.

**If the board's Wi-Fi dropped** (it will, on a new network):
```bash
$ADB shell "nmcli device wifi connect '<SSID>' password '<PASSWORD>' ifname wlan0"
$ADB shell "ip -4 addr show wlan0 | grep inet"
```
Then update `MQTT_HOST` and the `KASA_*_HOST` values in
`code/arduino/board.env` to match the new subnet (`kasa discover` on the PC
prints current IPs).

GenieX (the NPU LLM) also dies on reboot — restart it per §8:
```bash
"C:\Users\QCWorkshop22\AppData\Local\GenieX CLI\geniex.exe" serve &
curl -s http://127.0.0.1:18181/v1/models
```

---

## 1. Machines & network topology

| Machine | Role | Path |
|---|---|---|
| This PC | Copilot+ laptop, Snapdragon X Elite (X1E80100), 45 TOPS Hexagon NPU, 32GB RAM, Windows 11 | — |
| `QUAD-Client-main` | The QUAD client tooling repo — **not** the hackathon project | `C:\Users\QCWorkshop22\Downloads\QUAD\QUAD-Client-main` |
| `ai-home-energy-concierge` | The hackathon project (this repo), cloned as a **sibling** folder | `C:\Users\QCWorkshop22\Downloads\QUAD\ai-home-energy-concierge` |
| Arduino UNO Q board | Dragonwing QRB2210, Debian 13 (trixie), hostname `hec` | **reached over ADB/USB-C** (preferred) or SSH |

> **UPDATE (2026-08-05): use ADB over USB-C, not SSH.** The board exposes an
> `ADB Interface` (`USB\VID_2341&PID_0078&MI_00`), which gives a shell, file
> push, and `adb reverse` port tunnelling **without any network at all**. That
> makes every instruction below that depends on the board's IP optional.
> `adb` lives at `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`
> (installed from Google's official zip — winget's package failed its hash check).

**Network was unstable on this PC** — during setup it flapped across five
subnets (`192.168.4.x`, `10.73.51.x` Qualcomm Wi-Fi, `172.20.10.x` a phone
hotspot, `10.123.72.x`, and finally `192.168.86.x` home Wi-Fi "ArtiFi"). This
is exactly why the ADB path matters: it is immune to all of it.

If you do need the board's IP: `adb shell "ip -4 addr show wlan0 | grep inet"`.

---

## 2. Part A — QUAD-Client environment setup (done once, in `QUAD-Client-main`)

This is the prerequisite tooling setup that happened *before* the hackathon
project was even cloned. All commands below run from
`C:\Users\QCWorkshop22\Downloads\QUAD\QUAD-Client-main`.

### 2.1 Local hardware (already true, nothing to install)
Snapdragon X Elite X1E80100 · Adreno X1-85 GPU (4.6 TFLOPS) · Hexagon NPU
v73 (45 TOPS) · 31.6GB RAM · QAIRT SDK 2.43.0.260128. Confirm anytime with:
```bash
.venv/Scripts/python.exe -m quad_mcp_client.cli detect
```

### 2.2 UNO Q board connection (SSH via plink)
Board credentials live in `.env_boards` in this directory (gitignored,
**never committed** — copy from `.env_boards.example` if starting fresh).
Vars: `UNOQ_HOST`, `UNOQ_SSH_USER`, `UNOQ_SSH_PASSWORD`, `UNOQ_PLINK_PATH`,
`UNOQ_SSH_HOST_KEY_FINGERPRINT`.

- PuTTY was not installed → `winget install --id PuTTY.PuTTY -e` (plink.exe
  lands at `C:\Program Files\PuTTY\plink.exe`).
- **Gotcha:** that path has a space (`Program Files`) — if you write it into
  `.env_boards` **unquoted**, Bash's `source` silently word-splits it and
  the variable ends up empty with no error. Always quote:
  `UNOQ_PLINK_PATH="C:\Program Files\PuTTY\plink.exe"`.
- Host key fingerprint: unknown on first connect → run `plink` **without**
  `-hostkey` once, it prints the real fingerprint, paste that into
  `UNOQ_SSH_HOST_KEY_FINGERPRINT`. Changes on every board reflash.
- Standard connect pattern used everywhere in this session:
  ```bash
  set -a; source .env_boards 2>/dev/null; set +a
  "$UNOQ_PLINK_PATH" -ssh -pw "$UNOQ_SSH_PASSWORD" -batch \
      -hostkey "$UNOQ_SSH_HOST_KEY_FINGERPRINT" \
      "$UNOQ_SSH_USER@$UNOQ_HOST" "<command>"
  ```
  (`set -a; source .env_boards` must be re-run in **every** Bash tool call —
  shell state does not persist between calls.)

### 2.3 Board identity fix (real bug found and fixed)
`quad.toml`/`.env_boards` originally assumed this board was a Dragonwing
IQ-9075 (QCS9075). A live probe found `soc0/machine = QRB2210`,
device-tree model `"Arduino SA,Imola"` — it's actually the **Arduino UNO Q**
(QCS2210-family), not the IQ-9075. Fixed by renaming throughout:
- `quad.toml`: `[target.iq-9075]` → `[target.uno-q]`
  (`board=uno-q`, `soc=qcs2210`, `hexagon=v66`)
- `.env_boards` / `.env_boards.example`: `IQ9075_*` → `UNOQ_*`
- `CLAUDE.md`: "Board connect (IQ-9075)" → "Board connect (UNO Q)"
- `src/quad_mcp_client/local/capabilities.py`: added `qrb2210`/`qcm2290`
  aliases → `qcs2210` (the registry had **no** entry for the SoC string this
  board actually reports — `lookup_for_chipset('QRB2210')` returned `None`
  before this fix). Regression test added in
  `tests/test_local/test_capabilities.py`.

This board currently has **no on-device AI acceleration stack** — no
fastrpc/DSP skel, no SNPE, no onnxruntime, GPU reports as `rusticl` (Mesa
software, not real Adreno OpenCL). It's bare Debian, CPU-only, until/unless
that's provisioned separately.

### 2.4 NPU runtime provisioning (native ARM64 venv)
**Trap hit and worked around:** the default `.venv` here is x86_64 emulated
Python under Windows-on-ARM (Prism) — `platform.machine()` reports `AMD64`
even though the CPU is genuinely ARM64. NPU wheels need **native** ARM64
Python. Found one already installed:
`C:\Users\QCWorkshop22\AppData\Local\Programs\Python\Python311-arm64\python.exe`.

```bash
"C:\Users\QCWorkshop22\AppData\Local\Programs\Python\Python311-arm64\python.exe" -m venv .venv-arm64
.venv-arm64/Scripts/python.exe -m pip install --upgrade pip
.venv-arm64/Scripts/python.exe -m pip install qai-hub "onnxruntime-qnn>=1.17.0" onnxruntime-genai
```

Verify the NPU device is actually reachable (completing pip install is
**not** proof — must check `get_ep_devices()`):
```bash
.venv-arm64/Scripts/python.exe -c "
import os, onnxruntime_qnn as q
os.add_dll_directory(os.path.dirname(q.__file__))
import onnxruntime as o
o.register_execution_provider_library('QNNExecutionProvider', q.get_library_path())
devs = o.get_ep_devices()
print('QNN NPU device found:', any(d.ep_name=='QNNExecutionProvider' and str(d.device.type).endswith('NPU') for d in devs))
"
# -> QNN NPU device found: True  (confirmed working)
```

### 2.5 AI Hub Models token
Stored in `QUAD-Client-main\.env` as `QAI_HUB_API_KEY` /
`QAI_HUB_API_TOKEN` (same value, two names — some tools read one, some the
other). Also configured into the SDK's own credential store:
```bash
.venv-arm64/Scripts/qai-hub.exe configure --api_token "<token>"
```
Verified live: `.venv-arm64/Scripts/qai-hub.exe list-devices` successfully
returned the full AI Hub Models device catalog, including the exact device
label needed for exports: **`Snapdragon X Elite CRD`**.

### 2.6 QUAD MCP server connectivity
Already configured and confirmed working — `claude mcp list` shows
`quad: https://quad.infra.foundries.io/mcp (HTTP) - ✔ Connected`. (That
command segfaults on exit after printing the correct status — a `claude` CLI
bug, unrelated to QUAD; ignore it.)

**Important, discovered via `hardware_detect`:** the MCP server itself runs
on a **virtualized `AMD EPYC 7B12` Ubuntu host**, `available_runtimes:
["cpu"]` only — it has **no real NPU**. Profiling a model directly against
the server would produce fake/simulated NPU numbers, not real ones. See §8
(Blocked) for what this affects.

Note also: `QUAD-Client-main` is **not a git repo** — it's an extracted
`-main` zip, not a clone. Not an issue for anything done here, just don't
expect `git status` to work in that directory.

---

## 3. Part B — Cloning the hackathon project

```bash
cd /c/Users/QCWorkshop22/Downloads/QUAD
git clone https://github.com/gowtham612/ai-home-energy-concierge.git
```
Clean clone, branch `main`, 4 commits at clone time (latest `051d735`).
`gh auth` is logged in as `gowtham612` (matches repo owner) via a token set
up in a parallel PowerShell session.

**Read-order for this repo's own docs** (per its `README.md`): start with
`CLEANUP_REMINDER.md` and `04_ORGANIZER_REQUIREMENTS.md` before anything
else. **Flagged, not yet acted on:** `04_ORGANIZER_REQUIREMENTS.md` §E
contains the venue Wi-Fi password and laptop login credentials in a
currently-public repo — `CLEANUP_REMINDER.md` already flags this as a
keep-or-redact decision for the team, not something touched automatically.

The authoritative external instructions are also mirrored from
`file:///C:/Users/QCWorkshop22/Downloads/Snapdragon Multiverse Hackathon_Internal.pdf`
(the organizer deck) — its QUAD section (pp. 28–37) matches everything set
up in Part A above.

---

## 4. PC hub environment (this repo's `code/` dir)

```bash
cd /c/Users/QCWorkshop22/Downloads/QUAD/ai-home-energy-concierge/code
"C:\Users\QCWorkshop22\AppData\Local\Programs\Python\Python311-arm64\python.exe" -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install psutil   # needed for Peak RSS in benchmark.py, not in requirements.txt
```
`requirements.txt` was already correctly pinned (`uvicorn[standard]`,
`websockets` — avoids the documented WebSocket-404-on-plain-uvicorn gotcha).

**Gate:**
```bash
.venv/Scripts/python.exe smoke_test.py
# -> 32/32 checks passed
```

---

## 5. MQTT broker

```bash
winget install --id EclipseFoundation.Mosquitto -e --accept-source-agreements --accept-package-agreements
```
Repo's own `mosquitto.conf` is already correct
(`listener 1883 0.0.0.0`, `allow_anonymous true` — needed so the phone/board
can reach it, the mosquitto default binds localhost only).

**Bug found and permanently fixed:** the winget install also auto-registers
a **Windows Service** running mosquitto with its own default config
(loopback-only bind on both `127.0.0.1` and `[::1]`). This coexisted
silently with our own console instance. The hub's default
`MQTT_HOST=localhost` resolved to `::1` and connected to the **wrong**
broker — `mqtt_connected: true` looked completely healthy while zero board
traffic ever arrived, no error anywhere. Diagnosed via `netstat -ano | grep
1883` (two listeners, two different PIDs) and `tasklist | grep mosquitto`.
**Now permanently resolved** — from an elevated PowerShell:
```powershell
Stop-Service mosquitto
Set-Service mosquitto -StartupType Disabled     # must be ONE line, not split across two
```
Confirmed via `Get-Service mosquitto` → `Status: Stopped`, `StartType:
Disabled`. `MQTT_HOST` can safely default to plain `localhost` again now —
there is only one mosquitto instance on this machine going forward.

To (re)start our broker:
```bash
cd /c/Users/QCWorkshop22/Downloads/QUAD/ai-home-energy-concierge/code
"/c/Program Files/mosquitto/mosquitto.exe" -c mosquitto.conf -v > mosquitto.log 2>&1 &
```

**Still outstanding (not blocking, just not done properly):** inbound
firewall rules for ports 1883/8000 could not be created — needs admin
rights. Not currently a problem (both ports tested reachable from the board
regardless — this network's Windows Firewall profile already permits it),
but for correctness, from an elevated PowerShell:
```powershell
New-NetFirewallRule -DisplayName "MQTT 1883" -Direction Inbound -LocalPort 1883 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "Hub HTTP 8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

---

## 6. Hub server

```bash
cd /c/Users/QCWorkshop22/Downloads/QUAD/ai-home-energy-concierge/code
nohup .venv/Scripts/python.exe hub/server.py > hub_server.log 2>&1 &
```
(Now that the mosquitto service conflict is permanently fixed, plain
`localhost` default works — no need to pass `MQTT_HOST` explicitly anymore.)

Verify:
```bash
curl -s http://localhost:8000/api/state
# check "mqtt_connected": true AND (once something publishes) "rooms" actually populates —
# mqtt_connected:true alone is NOT sufficient proof, per the bug above.
```
Dashboard: `http://<PC_IP>:8000/` · Phone: `http://<PC_IP>:8000/phone`

---

## 7. UNO Q board — software side

```bash
set -a; source /c/Users/QCWorkshop22/Downloads/QUAD/QUAD-Client-main/.env_boards 2>/dev/null; set +a
PLINK="$UNOQ_PLINK_PATH"
```

**Installing deps — do NOT use pip.** The board's system Python is a PEP 668
externally-managed environment with **no `pip` module at all**
(`No module named pip`), and `python3 -m venv` also fails (`ensurepip` not
available, needs the `python3.13-venv` apt package, which itself needs
sudo). The clean fix is `apt` directly — both needed packages already exist
there:
```bash
"$PLINK" -ssh -pw "$UNOQ_SSH_PASSWORD" -batch -hostkey "$UNOQ_SSH_HOST_KEY_FINGERPRINT" \
    "$UNOQ_SSH_USER@$UNOQ_HOST" \
    "echo '$UNOQ_SSH_PASSWORD' | sudo -S apt install -y python3-serial python3-paho-mqtt"
```
(sudo password happens to be the same as the SSH login password on this
board — confirmed working, not guessed blind.)

Repo cloned directly onto the board (it has outbound internet):
```bash
"$PLINK" -ssh -pw "$UNOQ_SSH_PASSWORD" -batch -hostkey "$UNOQ_SSH_HOST_KEY_FINGERPRINT" \
    "$UNOQ_SSH_USER@$UNOQ_HOST" \
    "rm -rf ~/ai-home-energy-concierge && git clone https://github.com/gowtham612/ai-home-energy-concierge.git"
```

**Full MQTT loop proven** (before real sensors are wired) with the fake-serial publisher:
```bash
"$PLINK" -ssh -pw "$UNOQ_SSH_PASSWORD" -batch -hostkey "$UNOQ_SSH_HOST_KEY_FINGERPRINT" \
    "$UNOQ_SSH_USER@$UNOQ_HOST" \
    "cd ai-home-energy-concierge/code/arduino && ROOM=living MQTT_HOST=<PC_IP> timeout 8 python3 uno_q_publisher.py --fake-serial"
```
✅ Gate confirmed: `subscribed home/command/#` line present (the guide flags
this as the one most likely to silently break actuation later — must be
registered in `on_connect`), and — after the mosquitto bug fix above — the
hub's `/api/state` really does populate `rooms.living` with live data.

---

## 8. GenieX (on-device NPU LLM)

```bash
# Find + verify the exact Windows ARM64 installer (don't guess a URL — check the release):
gh release list --repo qualcomm/geniex --limit 5
gh release view v0.3.18 --repo qualcomm/geniex --json assets --jq '.assets[].name'
# -> geniex-cli-setup-windows-arm64-v0.3.18.exe (+ .sha256)

gh release download v0.3.18 --repo qualcomm/geniex --pattern "geniex-cli-setup-windows-arm64-v0.3.18.exe*"
sha256sum geniex-cli-setup-windows-arm64-v0.3.18.exe   # verify against the .sha256 file before running
```
Installed silently: `Start-Process <installer> -ArgumentList "/S" -Wait`.
Lands at `C:\Users\QCWorkshop22\AppData\Local\GenieX CLI\geniex.exe` (not on
PATH — invoke by full path).

```bash
GENIEX="C:\Users\QCWorkshop22\AppData\Local\GenieX CLI\geniex.exe"
"$GENIEX" --version                                          # v0.3.18, QAIRT Runtime v2.45.0.260326
"$GENIEX" pull ai-hub-models/Qwen3-4B-Instruct-2507           # 3.2 GB, takes several minutes
"$GENIEX" list                                                 # confirm: qualcomm/Qwen3-4B-Instruct-2507, W4A16
nohup "$GENIEX" serve > geniex_serve.log 2>&1 &                 # -> http://127.0.0.1:18181/
```
Verify:
```bash
curl -s http://127.0.0.1:18181/v1/models
curl -s -X POST http://127.0.0.1:18181/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"ai-hub-models/Qwen3-4B-Instruct-2507","messages":[{"role":"user","content":"Reply with exactly one word: OK"}],"max_tokens":10}'
```
Note: the pulled/registered model id (`qualcomm/Qwen3-4B-Instruct-2507:W4A16`)
does **not** exactly match `hub/llm.py`'s default `LLM_MODEL` string
(`ai-hub-models/Qwen3-4B-Instruct-2507`) — tested and confirmed **not a
problem**, GenieX ignores the `model` field when only one model is loaded.

`hub/llm.py` self-test confirms real LLM narration distinct from the
template fallback:
```bash
cd /c/Users/QCWorkshop22/Downloads/QUAD/ai-home-energy-concierge/code
.venv/Scripts/python.exe hub/llm.py
```

Real, measured benchmark with GenieX active (already written into
`code/README.md`'s performance table):
```bash
LLM_BASE_URL=http://127.0.0.1:18181/v1 .venv/Scripts/python.exe hub/benchmark.py --markdown
# Narration - local LLM: p50 3110.222 ms, p95 3272.678 ms
LLM_ENABLED=0 .venv/Scripts/python.exe hub/benchmark.py --markdown
# Peak RSS: 33.984 MB (deterministic control path)
```

---

## 9. Verified-working checklist

| Component | Status |
|---|---|
| `code/.venv` (native ARM64), `requirements.txt` | ✅ `smoke_test.py` 32/32 |
| Mosquitto broker (single instance now, service conflict resolved) | ✅ |
| `hub/server.py` | ✅ dashboard + `/api/state` responding |
| UNO Q board SSH + deps + repo clone | ✅ |
| Board → broker → hub MQTT loop (fake-serial) | ✅ `rooms.living` populated live |
| GenieX (`qairt`, W4A16, Qwen3-4B) | ✅ real narration + benchmark numbers |
| QUAD MCP server connectivity | ✅ (`quad: ... - ✔ Connected`) |
| `/quad-detect` (host + `uno-q` board target) | ✅ |
| `.venv-arm64` NPU runtime (QUAD-Client-main) | ✅ `QNN NPU device found: True` |
| AI Hub Models token | ✅ verified live via `qai-hub list-devices` |

---

## 10. Blocked — documented honestly, not faked

`/quad-profile` / `/quad-orchestrate` on a supplementary demo model (small
MNIST ONNX, `onnx/models` repo, verified as real binary content not an LFS
pointer before use) hit **two independent server-side infrastructure
failures** on the hosted QUAD MCP server (`quad.infra.foundries.io`) — not
fixable client-side, since only MCP tool-level access exists (no shell/SSH
to that server):

**1. QNN/SNPE target (`convert_model`, `target_sdk=qnn`):**
```
Error calling tool 'convert_model': qairt-converter failed (exit 1):
Traceback (most recent call last):
  File "/home/quad/work/QUAD/QUAD/sdks/v2.41.0.251128/lib/python/qti/aisw/dlc_utils/__init__.py", line 59, in <module>
    import libDlModelToolsPy as modeltools
ImportError: libpython3.10.so.1.0: cannot open shared object file: No such file or directory
[... full traceback shows a circular-import cascade from the same missing .so ...]
```

**2. ExecuTorch target (`convert_model`, `target_sdk=executorch`,
`runtime=qnn-htp`):** first call timed out; retry returned:
```
Error calling tool 'convert_model': No ExecuTorch checkout or installed package found. Fetch with `bash scripts/sdk_fetch.sh executorch`.
```

Both are genuine server environment bugs (broken Python shared-library dep;
toolchain never fetched) — raise verbatim at a QUAD office-hours session
(Tue/Thu 1:30–3:30, Fri 9–noon) if you want this filled in before
submission. **GenieX's real, measured NPU benchmark (§8 above) already
stands as valid "real NPU numbers" evidence** for the rubric — it's the
app's actual production AI path on this machine's genuine Hexagon NPU, not
an unrelated demo model, and was the deliberate choice over chasing the
broken profiling path further.

Also worth remembering: even if conversion had worked, profiling directly
against the MCP server would **still** not give real NPU numbers — see §2.6
(the server has no real NPU). Real on-device profiling needs the
client-side `quad-client profile-device --transport ssh --host <board-ip>`
path, which was considered and deliberately not pursued against the UNO Q
board since it currently has no working on-device NPU stack (§2.3) — would
likely just report "NPU unavailable," honest but not useful evidence.

---

## 11. Remaining — physical hardware, needs your hands

**Currently at Step 2/3** (see §0). Full remaining checklist from
`06_UNO_Q_BRINGUP.md`:

- [ ] **Step 2** — Wire sensors (actuator stays disconnected for now):
  | Function | Pin | Wiring |
  |---|---|---|
  | PIR motion `OUT` | D2 | VCC→5V, GND→GND, OUT→D2 |
  | LDR / photoresistor | A0 | 3V3 → LDR → A0 → 10kΩ → GND (divider) |
  | DHT22 `DATA` | D4 | VCC→3V3, GND→GND, DATA→D4, + 10kΩ pull-up DATA→VCC |
- [ ] **Step 3** — Flash this minimal test sketch via App Lab, verify at
      115200 baud: clean JSON once/sec, `"pir"` flips on wave, `"ldr"`
      changes substantially when covered:
      ```cpp
      const uint8_t PIN_PIR = 2;
      const uint8_t PIN_LDR = A0;
      void setup() { Serial.begin(115200); pinMode(PIN_PIR, INPUT); }
      void loop() {
        int pir = digitalRead(PIN_PIR);
        int raw = analogRead(PIN_LDR);
        Serial.print("{\"pir\":"); Serial.print(pir);
        Serial.print(",\"ldr\":"); Serial.print(raw);
        Serial.println("}");
        delay(1000);
      }
      ```
- [ ] **Step 4 (decision point)** — In App Lab's library manager, check
      whether `DHT sensor library` (Adafruit) and `Servo` are available for
      this Zephyr core. Set `#define USE_DHT` / `#define USE_SERVO` (0 or 1)
      at the top of `arduino/sketch/sketch.ino` accordingly. If Servo is
      unavailable, use the relay on D7 instead — actuation still happens
      physically either way.
- [ ] **Step 5** — Flash the real `sketch.ino`. Verify exact JSON shape
      `{"occupancy":bool,"lux":int,"temp_c":float,"humidity":float,"raw_pir":int}`
      and the ~30s occupancy debounce hold (deliberate, not a bug).
- [ ] **Step 6** — Confirm how Linux sees the MCU's serial
      (`ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null` over SSH) — our
      publisher auto-detects `/dev/ttyACM*`, the common case.
- [ ] **Step 8** — Swap `--fake-serial` for the real feed:
      `ROOM=living MQTT_HOST=<PC_IP> python3 uno_q_publisher.py` — verify
      `actuator source: mcu_serial`, not `simulated`.
- [ ] **Step 9** — Wire the actuator:
      | Function | Pin | Notes |
      |---|---|---|
      | Servo signal | D9 | |
      | Servo power | **its own 5V supply** | ⚠️ NOT the board's 3V3 rail — a stalling servo browns out the MCU |
      | Relay `IN` | D7 | or LED + 220Ω |
      | Buzzer `+` | D8 | |
      Test with `CMD lights off` from the serial monitor before involving
      MQTT at all.
- [ ] **Step 10** — Close the loop end-to-end: approve on phone/dashboard →
      servo physically flips a real switch → hub logs
      `[mqtt] actuator confirmed ...` → dashboard shows "Applied — saving
      realized".
- [ ] **Step 10b** — Verify the R7 safety-gate refusal at 29.5°C — nothing
      moves, HTTP 409. This is the single most persuasive demo moment.
- [ ] **Record video of Step 10 and 10b immediately once working** — your
      Archetype E insurance if anything breaks before the actual demo.

## 12. Also outstanding (not a technical blocker, just not done)

- `code/README.md` team table still has `<name>`/`<email>` placeholders —
  hard submission requirement.
- `CLEANUP_REMINDER.md`'s open decision: redact/remove internal planning
  docs (venue Wi-Fi + laptop credentials currently sit in
  `04_ORGANIZER_REQUIREMENTS.md`) before judges see the public repo.
- Every team member's feedback form, due Friday noon (gates prize
  eligibility).
- Firewall rules for 1883/8000 (§5) and NPU stack on the UNO Q board itself
  (§2.3) — both need admin/deeper provisioning, neither currently blocking
  anything.

---

## 13. Secrets — where they live, never repeated here

| Secret | Stored in | Notes |
|---|---|---|
| Board SSH password | `QUAD-Client-main\.env_boards` → `UNOQ_SSH_PASSWORD` | gitignored, also doubles as the sudo password on this board |
| Board sudo password | same as above | confirmed same value as SSH login |
| AI Hub Models token | `QUAD-Client-main\.env` → `QAI_HUB_API_KEY` / `QAI_HUB_API_TOKEN` | obtain from app.aihub.qualcomm.com → Account → API token |
| QUAD MCP bearer token | `QUAD-Client-main\.env` → `QUAD_MCP_TOKEN` | also in `.claude/settings.json` via `${env:...}` |
| Venue Wi-Fi / laptop login | `04_ORGANIZER_REQUIREMENTS.md` §E (in **this public repo**) | flagged in `CLEANUP_REMINDER.md`, redaction is your call |

If starting fully fresh with none of the above available, you'll need to
re-obtain: the board's current IP + SSH/sudo password (physical access to
the board), an AI Hub Models API token (app.aihub.qualcomm.com → Account →
API token), and confirm the QUAD MCP server URL/token from
`QUAD-Client-main\.claude\settings.json`.

---

# 14. Hardware pivot — what was actually built (2026-08-05)

The breadboard build never happened: we had no PIR, LDR, DHT22 or servo. What
we had was a **Modulino Knob**, a pile of **TP-Link Kasa** plugs/bulbs, and a
phone. That turned out better than the original plan — see
`08_HARDWARE_PIVOT_PLAN.md` for the reasoning, this section for the outcome.

## 14.1 Architecture as built

```
Modulino Knob ──I2C/Wire1──▶ STM32 MCU ──RPMsg──▶ arduino-router ──tcp:7500──▶ publisher
                                                                                   │ MQTT
Phone simulator ──HTTP /api/sensor──────────────────────────────▶ hub ◀────────────┘
                                                                   │ approve
                                                        home/command/living/lights
                                                                   │
                                              publisher ──python-kasa──▶ REAL BULB
```

## 14.2 Verified, with the evidence

- **Qwiic is on the MCU as `Wire1`** (I2C4, pins PD12/PD13). Proven by
  `code/arduino/scanner/scanner.ino`, which scans both buses:
  `0x3A Modulino KNOB` on `Wire1`, nothing on `Wire`.
- **Knob reads and writes.** Rotation, press-to-snap, and clamp-with-writeback
  all confirmed live.
- **Telemetry contract preserved.** The sketch emits e.g.
  `{"temp_c":21.9,"temp_src":"knob_sim",...,"nodes":"K"}` at 1 Hz with no boot
  banner, so `uno_q_publisher.py` needed no contract change.
- **Full loop closed.** Approve → `[uno_q] KASA lights -> off ok=True
  measured=0.0W` → the bulb read back `is_on=False, 0.0 W` (from 10.8 W).
  Hub recorded `source=kasa ok=True` and booked the saving as realized.
- **R7 guardrail refuses.** At 29.5 °C, `POST /api/apply` → **HTTP 409**,
  `"gate": "comfort_guardrail"`, nothing switched.
- **32/32 smoke tests still pass.**

## 14.3 Upgrade: power is now MEASURED, not modelled

The KL120 bulb and HS110 plug both report real instantaneous watts. Load
payloads now carry `"metered": true` and the audit line reads
*"Lights still ON at 11 W (measured by the smart plug)"*. This retires the
README's own stated limitation ("load power is modelled, not metered") for
those loads, and it is directly relevant to the 40-point Technical
Implementation criterion. Unmetered loads still fall back to the modelled
table and are marked `"metered": false` — the two are never conflated.

## 14.4 UNO Q platform facts worth knowing (each cost real time)

1. **`Serial` requires `Arduino_RouterBridge`.** The zephyr core ships a stub
   that hard-errors until you install it. Chain:
   `Arduino_RouterBridge → Arduino_RPClite → MsgPack →
   ArxContainer/ArxTypeTraits/DebugLog`.
2. **MCU `Serial` is not `/dev/ttyACM*`.** It goes over RPMsg to
   `arduino-router` (`/dev/ttyHS1`), which republishes on
   **`tcp://127.0.0.1:7500`**; a `socat` unit mirrors that to `/dev/ttyGS0`
   → USB CDC → a COM port on the PC. `find_port()` finds nothing here, by design.
3. **The board flashes itself.** `arduino-cli` is on the board and programs the
   STM32 over SWD (`linuxgpiod` bitbang). No App Lab GUI needed —
   compile + upload + read the monitor entirely over `adb`.
4. **The board has Wi-Fi** (`wlan0`, nmcli). It is simply *disconnected* out of
   the box. It also exposes **ADB over USB-C**, and `adb reverse tcp:1883` tunnels
   MQTT over the cable — immune to venue Wi-Fi.
5. **`tzdata` gap.** python-kasa parses the device timezone and Debian moved
   legacy zone names (`PST8PDT`) into `tzdata-legacy`. Without it every Kasa
   connect fails with `'No time zone found with key PST8PDT'`, which reads like
   a network error and is not. Fix: `pip install tzdata` inside the venv.

## 14.5 Bugs found and fixed this session

1. **Load publishing was accidentally coupled to sensor content.** An early
   `continue` for provenance-only telemetry lines skipped the Kasa load-poll
   block below it, so with `MCU_SIGNALS=` empty the hub received **zero loads**
   and no rule could ever fire. Sensor and load publishing are now independent.
2. **False `ok=False` on a successful switch.** TP-Link firmware serves one
   connection at a time; a concurrent reader makes the write raise
   `Communication error … transition_light_state` *even though the command
   landed*. `KasaBank.switch()` now retries and judges success by **reading the
   device back**, not by whether the write returned cleanly.
3. **Unquoted config values with spaces.** `KASA_AC_ALIAS=Space heater` in
   `board.env` made bash assign `Space` and then try to execute `heater`. Same
   class of bug as the `.env_boards` plink path earlier in this project. All
   values with spaces are now quoted, with a comment saying why.
4. **Stale honesty claim in the rules.** R3's evidence asserted "approximate lux
   from an uncalibrated photoresistor" — hardware we never had. It now reports
   its real source (`lux_src`, or `simulator`).
5. **UDP discovery is unusable here.** The ArtiFi mesh drops client-to-client
   broadcast, *and* a KL120 that is switched OFF stops answering discovery
   entirely — precisely the state you most need to verify after switching
   something off. All device access now uses `Device.connect(host=…)` over TCP.

## 14.6 Gotchas for whoever runs this next

- **Only one process should talk to a Kasa device.** The publisher polls them
  every `KASA_POLL_S`. Running `kasa` CLI or the phone app at the same time
  causes timeouts that look like faults but are contention. Query the hub
  instead of the device.
- **Run `smoke_test.py` with port 8000 free.** It starts its own hub; if another
  hub is already bound it can pick up that instance's state and fail spuriously
  (seen once: "total watts computed 0 W", which passed cleanly on a rerun).
- **`MCU_SIGNALS` decides who owns a signal.** The hub merges room state, so the
  last writer wins and the MCU's 1 Hz stream beats anything POSTed to
  `/api/sensor`. Default `temp_c` (knob owns temperature — the tactile R7 demo);
  set it empty to run entirely from the simulator.
- **`adb shell "... &"` hangs.** adb waits for the stream. Use
  `setsid nohup … > log 2>&1 < /dev/null &`.

## 14.7 Files changed

| File | Change |
|---|---|
| `code/arduino/scanner/scanner.ino` | **new** — dual-bus I2C scanner |
| `code/arduino/knob_test/knob_test.ino` | **new** — Knob smoke test |
| `code/arduino/sketch/sketch.ino` | rewritten for Modulino; provenance keys; print-only |
| `code/arduino/uno_q_publisher.py` | TCP source, partial payloads, `KasaBank`, metered watts, `MCU_SIGNALS` |
| `code/arduino/board.env.example` | **new** — all LAN/device config, env-overridable |
| `code/simulator/index.html` | moved from repo root; served at `/simulator` |
| `code/hub/server.py` | added the `/simulator` route |
| `code/hub/rules.py` | R3 evidence now states its real lux source |
| `code/requirements.txt` | `python-kasa`, `psutil`, Win-ARM `--only-binary` note |
| `code/README.md` | Hardware, Setup, Quickstart rewritten |

## 14.8 Still open

- `06_UNO_Q_BRINGUP.md` Steps 2-5 and 9 still describe the breadboard build and
  should be replaced or marked superseded by `08_HARDWARE_PIVOT_PLAN.md`.
- Team names/emails in `code/README.md` are still placeholders (**hard
  submission requirement**).
- `CLEANUP_REMINDER.md`'s decision on the venue credentials in
  `04_ORGANIZER_REQUIREMENTS.md` is still open.
- `/quad-profile` remains blocked by the two server-side failures in §10.
- Optional: a Modulino **Thermo** would make `temp_c` a real measurement and
  retire the last declared simulation in the sensing path.

---

# 15. Rebuilding the board from scratch

Nothing here is needed for a normal power cycle — the firmware lives in STM32
flash and the Wi-Fi profile lives in NetworkManager, so both survive unplugging.
This section is for a reflash, a factory reset, or a second board.

## 15.1 What survives a power cycle (do not redo)

| Thing | Survives? |
|---|---|
| Flashed MCU firmware | yes — in STM32 flash |
| Wi-Fi credentials | yes — NetworkManager profile |
| `~/energy-venv`, `~/Arduino/libraries`, the repo clone | yes — on the eMMC |
| The running publisher process | **no** — restart it, see §0.1 |
| `adb reverse` tunnels | **no** — re-run if using USB-only mode |

## 15.2 Full rebuild

```bash
ADB="$LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe"   # from Google's official zip
$ADB devices                                             # expect one device

# 1. Network (skip if you will run USB-only via adb reverse)
$ADB shell "nmcli device wifi connect '<SSID>' password '<PASSWORD>' ifname wlan0"
$ADB shell "ip -4 addr show wlan0 | grep inet"

# 2. Arduino libraries.
#    WITH internet on the board this is one line - arduino-cli resolves the whole
#    dependency tree for you:
$ADB shell "arduino-cli lib install Arduino_Modulino Arduino_RouterBridge"
#
#    WITHOUT internet, clone them on the PC and push. 14 libraries, two chains:
#      Serial support : Arduino_RouterBridge -> Arduino_RPClite -> MsgPack
#                       -> ArxContainer, ArxTypeTraits, DebugLog
#      Modulino       : Arduino_Modulino -> VL53L4CD, VL53L4ED, Arduino_LSM6DSOX,
#                       Arduino_LPS22HB, Arduino_HS300x, ArduinoGraphics,
#                       Arduino_LTR381RGB
#    arduino-libraries/* and stm32duino/{VL53L4CD,VL53L4ED} and hideakitai/{MsgPack,
#    ArxContainer,ArxTypeTraits,DebugLog} on GitHub. Then for each:
#      git clone --depth 1 https://github.com/<org>/<lib>.git && rm -rf <lib>/.git
#      $ADB push <lib> /home/arduino/Arduino/libraries/<lib>
$ADB shell "arduino-cli lib list"        # expect 14

# 3. Python environment
$ADB shell "echo '<sudo-pw>' | sudo -S apt install -y python3-serial python3-paho-mqtt"
$ADB shell "python3 -m venv --system-site-packages ~/energy-venv"
$ADB shell "~/energy-venv/bin/python3 -m pip install python-kasa tzdata"
#   tzdata is NOT optional: python-kasa reads the device timezone and Debian moved
#   legacy zone names (PST8PDT) into tzdata-legacy. Without it every Kasa connect
#   fails with "No time zone found with key PST8PDT", which reads like a network
#   error and is not.

# 4. Code + config
$ADB shell "git clone https://github.com/gowtham612/ai-home-energy-concierge.git"
$ADB push code/arduino/board.env /home/arduino/ai-home-energy-concierge/code/arduino/board.env

# 5. Flash the firmware (the board programs its own STM32 over SWD)
$ADB push code/arduino/sketch /tmp/sketch
$ADB shell "arduino-cli compile --fqbn arduino:zephyr:unoq /tmp/sketch"
$ADB shell "arduino-cli upload  --fqbn arduino:zephyr:unoq /tmp/sketch"

# 6. Start the publisher — see §0.1
```

**Gate:** flash `code/arduino/scanner` first and read the monitor; expect a
Modulino address on `Wire1` (Knob = `0x3A`). If both buses are empty, fix the
Qwiic cable before going further.

## 15.3 With the board unplugged, what still works on the PC

Everything except physical actuation:

- hub, rules engine, GenieX narration, dashboard, `/simulator` — all fine
- `smoke_test.py` — 32/32 (it needs no hardware, and actually prefers the
  publisher stopped; see §14.6)
- **Kasa switching does NOT work.** The publisher that drives the smart devices
  runs *on the board*. With it gone, the hub still publishes
  `home/command/...` and returns HTTP 200, but nothing is listening, so no
  actuator confirmation ever comes back and no lamp moves. Watch for the missing
  `home/actuator/...` record rather than trusting the 200.

---

# §16 Approve returned HTTP 400 — root cause and three fixes (2026-08-05)

Reported: *"it gives the popup right. but if I approve, nothing happens, AC didnt
turn off. for lights, it was showing 0W in dashboard but simulator was showing it
as ON."* Wire log showed `APPLY r2-living-ac -> HTTP 400`, five times.

## 16.1 The bug

`code/simulator/index.html` rendered one Approve button per entry in `r.actions`
and sent that entry as the `action` field of `POST /api/apply`:

```js
var actions = (r.actions && r.actions.length ? r.actions : ["off"]);
... data-action="'+esc(a)+'">Approve: switch '+esc(a)+'
post("/api/apply", { reco_id:recoId, action:action, ... })
```

But `r.actions` is **human-readable advice** — `"Turn off the ac"`, `"Set an away
temperature"`, `"Link HVAC to geofence"` — and `hub/server.py` accepts only:

```python
action = body.get("action", "off")
if action not in ("on", "off"):
    return JSONResponse({"error": "action must be 'on' or 'off'"}, status_code=400)
```

So every approval was rejected. Two aggravating details:

- `/api/apply` derives the target from the **rule** (`load_key = f"{rec.room}/{_load_from_rule(rec)}"`),
  never from `action` — so all three advice strings mapped to the *same single
  operation*. Rendering three buttons was meaningless.
- "Approve: switch Link HVAC to geofence" advertised an action the system cannot
  perform. Worse than a failure: a false capability claim in front of judges.

**Fix:** advice renders as text; one `Approve — switch off` button per card
carrying the real API verb. Proven against the live hub:

```
OLD  action='Turn off the ac'  -> HTTP 400
NEW  action='off'              -> HTTP 200
```

## 16.2 Failed actuations were still booked as savings

`api_apply` books the saving the instant the command is published. Nothing ever
revised it when the confirmation came back `ok=false`, so an unreachable lamp
still showed **"you saved $0.0023"**. `server.py`'s own docstring says
*"USER APPROVES -> physically act -> confirm -> book the saving as realized"* —
the code did not implement its fourth step.

`StateStore.record_actuation()` now un-books on `ok=false` from a real device and
re-arms the recommendation (the problem is unsolved, so it should be re-raised).
`source="simulated"` is a *declared, labelled* path and stays booked. Verified:
`realized $0.00232 -> $0` on a `kasa_error` confirmation.

## 16.3 Duplicate recommendation cards

`RECO_COOLDOWN_S` throttles re-*narration*, but once it lapsed the same finding id
was appended to `STORE.recos` again, and `recos[-12:]` rendered each copy as its
own card — `r3-living-daylight` appeared three times. New `StateStore.latest_recos()`
keeps the append-only history for the audit trail but surfaces each id once, newest
wording first. Used by both `/api/state` and `/api/recos`.

## 16.4 Simulator fighting the real device

The simulator POSTed simulated state for loads backed by a Kasa device; the
publisher re-published true state 5 s later. That is the exact "simulator says ON,
dashboard says 0 W" divergence. Metered loads are now labelled **real device** and
left to the hardware. Also: the read-back line read `l.source || l.src` — keys the
hub never sends. It sends **`metered`** (bool).

Commit `a7b5efd`, pushed. A teammate had meanwhile switched the licence to MIT
(`0180160`); rebased cleanly, no overlap.

## 16.5 ⚠ Network state at time of writing — the Kasa half is DOWN

Not a code fault. Discovered while verifying:

| Thing | State |
|---|---|
| **ArtiFi** (phone hotspot) | **not broadcasting** — absent from a board-side rescan |
| Bulb *Bedroom light 2* (`172.20.10.7`) | off-network, was on ArtiFi |
| Heater plug (`172.20.10.5`) | **broadcasting `TP-LINK_Smart Plug_26B1` at 100%** — it has fallen back to its own setup AP, i.e. **unprovisioned** |
| Board `wlan0` | `NO-CARRIER`, `state DOWN`, no IPv4 |
| Board MQTT | **fine** — rides USB via `adb reverse tcp:1883` |
| Knob telemetry | **fine** — 21.9 °C, `temp_src=knob_sim`, 1 Hz |
| PC | HaQathon |

Board-side saved Wi-Fi profiles: `ArtiFi`, `Nanda's S26`, `QGuest`.
Visible and known-good: `Hydra`, `Qguest`, `HaQathon`.

**To restore the physical-actuation demo**, all three must land on one LAN:
1. Bring up a network the Kasa devices are on (simplest: re-enable the ArtiFi
   hotspot — bulb and board both have saved credentials for it).
2. The heater plug needs **re-provisioning** through the Kasa app — it is in
   setup mode, it will not rejoin on its own.
3. Then `python code/tools/reconfigure_network.py` to rediscover IPs and redeploy.

Until then the loop still closes end-to-end and degrades **honestly**:
`source=kasa_error, ok=false`, and — as of §16.2 — no saving is claimed.

---

# §17 Modulino Buttons as a Kasa debug path + Risk V-2 SETTLED (2026-08-05)

A Modulino Buttons node was daisy-chained off the Knob. Asked for: two buttons
that directly toggle the bulb and the heater, as a hardware check that the Kasa
path works through the UNO Q without the browser in the way.

## 17.1 ⭐ Risk V-2 is settled: host -> MCU serial WORKS

Open since the first bring-up and the reason `MCU_ACCEPTS_COMMANDS` shipped as
`0`: nobody had verified the UNO Q's Bridge/RPC path delivers **Linux -> MCU**
bytes at all. It does. Writing to the same `tcp://127.0.0.1:7500` monitor socket
that carries telemetry the other way:

```
--> CMD lights on ok
<-- {"ack":"lights","state":"on","ok":true,"via":"pixels"}
```

`MCU_ACCEPTS_COMMANDS` is now **1**. This is what lets the button LEDs show what
the *device reported* rather than what was *asked for* — for a debug tool the
difference is the whole point.

## 17.2 Button map

| Button | Action | LED |
|---|---|---|
| **A** | toggle Kasa bulb (`lights`) | LED0 = bulb **confirmed** on |
| **B** | toggle Kasa plug (`ac` / heater) | LED1 = plug **confirmed** on |
| **C** | rescan the Qwiic bus (hot-plug) | LED2 = last action failed / never confirmed |

Chirps: 660 Hz = press taken, awaiting device · 880 = confirmed on ·
440 = confirmed off · 196 = failed, or no confirmation within `ACK_TIMEOUT_MS`
(12 s).

Occupancy override moved off button A to the simulator UI, alongside the other
declared-simulation inputs.

## 17.3 Why counters, not edges

The MCU cannot reach a Kasa device — it has no network. So a press bumps a
monotonic counter (`bl`, `ba`) carried in every telemetry line;
`uno_q_publisher.py`'s new `ButtonWatch` acts on the **delta** since the line it
last saw, then writes `CMD <load> <on|off> <ok|fail>` back.

A delta survives what an edge flag does not:

- **dropped line** — the count still climbs, the press is not lost
- **duplicate line** — no change, so no double-switch
- **publisher restart** — first sight BASELINES only; without this every press
  since MCU boot would replay on each restart
- **MCU reflash** — counter resets to 0, i.e. *decreases*; treated as a reboot
  and re-baselined rather than actuated (a real wrap needs 65535 presses)

Seven cases unit-tested offline (baseline / single / repeat / even delta / odd
delta / reset / unbound) — all pass.

## 17.4 A bug this shook out

`KasaBank.toggle()` first read current state via `poll()` and returned
`"unbound"` when that came back empty. But TP-Link firmware **serves one
connection at a time**, so the publisher's own 5 s sweep (or the phone app) can
lock a read out for a moment — and a bulb sitting right there got reported as
*"no Kasa device bound"*. Caught live: toggle 1 succeeded, toggle 2 said
`unbound` with the bulb plainly connected.

`self.devices` is the authority on binding; a failed poll is not. Now: retry
once, then report `kasa_error`, which is a different claim and gets a different
log line. The distinction reaches the MCU too.

## 17.5 Verified end to end

| Link | How | Result |
|---|---|---|
| Buttons node enumerates | `"nodes":"KB"` in telemetry | ✅ |
| Press -> counter | `"bl":0,"ba":0` present and live | ✅ |
| Counter -> toggle logic | 7 offline unit cases | ✅ |
| Toggle -> real bulb | `toggle('lights')` twice on the board | ✅ physically switched, `ok=True source=kasa` |
| Publisher -> MCU LED | `CMD` write, MCU acked | ✅ |
| Contention handling | concurrent poll + write | ✅ "write reported KasaException but the device IS off — trusting the device" |

**Only the physical press itself is unverified** — that needs a finger on the
button. Everything it depends on is proven.

## 17.6 Network note

ArtiFi came back mid-session; board is on `172.20.10.8`, bulb bound at
`172.20.10.7` (1.7 W, metered). The heater plug at `172.20.10.5` is still
**unprovisioned** — it was broadcasting its own `TP-LINK_Smart Plug_26B1` setup
AP. Until it is paired, **button B will chirp low and light the error LED**,
which is the correct honest answer, not a fault in the button path.

The hotspot is flaky: restoring the bulb took three discovery attempts before
one answered. Worth knowing before blaming code on demo day.

---

# §18 Session of 2026-08-05/06 — fixes, and the CRLF trap

## 18.1 ⚠⚠ THE CRLF TRAP — most dangerous bug in the project

**Symptom:** the board looks completely healthy — publisher running, both Kasa
devices bound, a plausible temperature updating once a second on the dashboard —
but the hub receives **nothing**, and the temperature you are watching is
**invented**. The real knob sat still at 21.9 °C while the dashboard showed a
drifting 22.5–23.6 °C.

**Cause:** `reconfigure_network.py` wrote `board.env` from Windows in text mode,
so every `\n` became `\r\n`. `board.env` is sourced by **bash on the board**,
which keeps the CR as part of the value:

```
MCU_TCP_HOST = '127.0.0.1\r'
MQTT_HOST    = '192.168.86.34\r'
```

Both hostnames are invalid. The MCU connect fails → publisher falls back to
synthetic data. The MQTT connect fails → nothing reaches the hub. Nothing errors
out; it just quietly lies.

**This has now happened twice.** Defended twice as of `42b290a`:
1. the tool writes with `newline="\n"`
2. it runs `sed -i 's/\r$//' board.env` on the board after pushing

**Diagnose in one command:**
```bash
adb shell "grep -c $'\r' /home/arduino/ai-home-energy-concierge/code/arduino/board.env"
```
Non-zero → this is your bug.

## 18.2 Two hardening changes that came out of it

- **The MCU probe was one-shot.** `arduino-router` restarts with the sketch and
  takes seconds to return after a re-plug; losing that race committed the
  publisher to synthetic data *for its whole lifetime*. Now polls for
  `MCU_WAIT_S` (default 30 s, env-overridable), and the fallback prints a banner
  saying plainly that every value below is invented.
- **`fake_lines()` declared nothing.** The hub merges room state with
  `{**prev, **payload}`, so a `temp_src:"knob_sim"` from an earlier real run
  **survived** while invented values overwrote `temp_c` — a synthetic reading
  wearing the physical knob's label. Now stamps `*_src="synthetic"` on every
  line. **A generated value must never inherit a measured value's label.**

## 18.3 Simulator + dashboard (`ca725c6`)

- **Approve returned HTTP 400 on every recommendation** (§16): the card sent
  `r.actions` prose ("Turn off the ac") as the API `action`, which only accepts
  `on`/`off`. One button per card now sends the real verb.
- **Websocket reconnect loop.** `connectWS()` closed the old socket, which fired
  *its* `onclose`, which scheduled another `connectWS()`; `onopen` reset
  `wsRetry=0` so backoff never grew. One blip → permanent 1 Hz loop flooding the
  log. Handlers are now detached before closing. Logging follows real
  transitions: one line on connect, one per genuine drop, one on recovery.
- **Dashboard now labels provenance.** Real and simulated loads always shared the
  table but rendered identically with a literal `—` in the Detail column. The hub
  has always sent `metered`; the dashboard never read it. Now shows
  "real device · measured" vs "simulated · modelled".
- **Failed actuations no longer book a saving** (§16.2), and duplicate
  recommendation cards are collapsed (§16.3).

## 18.4 Git identity corrected

All commits were authored as `Chris <hl3838@columbia.edu>` — the machine's global
git identity, unrelated to the team. The *push* credential was always correct
(`gh auth status` → `gowtham612` active, `repo` scope); author identity and push
credential are independent, which is why pushes succeeded while commits read as
someone else.

- repo-**local** identity set to `gowtham612 <gowthamraj.b@gmail.com>` (global
  left alone — shared machine)
- all 18 commits rewritten and force-pushed with `--force-with-lease`
- verified via the GitHub API: **18/18 → `author=gowtham612`**, zero unlinked
- content untouched (`git diff pre-author-rewrite HEAD` empty)
- safety net kept **locally, unpushed**: tag `pre-author-rewrite` and branch
  `backup-before-author-rewrite` at the original `ab1feec`

**SHAs all changed.** Anyone holding a clone must
`git fetch && git reset --hard origin/main` — a plain `git pull` will try to
merge the old history back.

## 18.5 State at handoff

| Piece | State |
|---|---|
| Board | `192.168.86.51`, USB attached, publisher running |
| MCU | real knob, steady **21.9 °C**, `temp_src=knob_sim`, `"bl":2` |
| MQTT | `connected rc=0`, ~5 msgs / 10 s |
| `lights` | KL120 `192.168.86.49`, on, **1.7 W metered** |
| `ac` | HS110 `192.168.86.20`, on, 0.0 W metered |
| Hub / broker | up on `192.168.86.34`, dashboard `/`, simulator `/simulator` |
| Repo | clean, in sync, `42b290a` |

Cosmetic leftover: `living/dryer` sits in the load map at off/0 W from a
mixed-provenance test. Harmless; clear it by restarting the hub.

## 18.6 Still open

- **`demo.mp4`** — not recorded. Slide 13 references it; renders a placeholder
  without it.
- **Venue credential redaction** — `04_ORGANIZER_REQUIREMENTS.md` §E.
- **Yash / Ajay workstreams** — assignments in the team table are guesses.
- **Feedback forms** — every member, gates prize eligibility.
- **Third smart plug** (`TP-LINK_Smart Plug_5307`, EP40, `192.168.86.30`) was
  being paired; not yet bound to a load.
- **Stale-data indicator** — the dashboard showed 86-minute-old readings as if
  live during this session. Proposed but NOT built: grey out anything older than
  ~15 s. Worth doing before the demo.

---

# §19 AI enhancement plan (09_AI_ENHANCEMENT_PLAN.md) — all six tasks done

All of P0-A → P3-F implemented, gated and pushed. `smoke_test.py` 32/32 after
every task.

## 19.1 ⚠ Run the gate with the board publisher STOPPED

`smoke_test.py` asserts `total_watts == 1340` from its own fixtures. The live
publisher republishes the REAL bulb (1.7 W) every 5 s and overwrites them, giving
a spurious **23/25**. It is interference, not a regression.

```bash
adb shell "pkill -9 -f '[u]no_q_publisher.py'"
python smoke_test.py            # 32/32
# then relaunch the publisher
```

## 19.2 The plan's latency budget was wrong, and why

Plan assumed ~3.1 s for GenieX. Measured: **11.4 s**, because latency tracks
**output length**, steeply:

| output | latency |
|---|---|
| 135 chars | 2.6 s |
| 378 chars | 5.5 s |
| 1060 chars | 11.4 s |
| ~600 tokens | **~135 s** |

`llm.py` asked for `max_tokens=300` with no brevity instruction, so the model
rambled to ~1060 chars and blew its own 8 s timeout — **every narration had been
silently falling back to the template**, while README quoted 3110 ms. Capping
output at 160 tokens + a brevity instruction + a 20 s timeout gives **2.3 s and
`narrated_by=llm`**: 5× faster *and* actually using the NPU. Approved by the user
before changing shared code.

## 19.3 Measured, on real silicon

| Tier | Where | Measured |
|---|---|---|
| 1 · edge anomaly | **UNO Q A53**, pure Python | **30.6 µs** p50 |
| — provenance check | hub | 110 µs |
| — rules engine | hub | 0.014 ms |
| 2 · narration | Hexagon NPU | 3.33 s |
| 2 · plan synthesis | Hexagon NPU | 11.5 s, **per change** |
| 3 · Q&A | Hexagon NPU | ~3.4 s, first token 2.4 s |

Anomaly model: 14 simulated days, 840 samples, **holdout 0.9714**, precision
0.947, recall 0.900, seed 20260806. **Training data is SIMULATED** — stated in
the model file, in `model_provenance()`, and in every evidence line.

## 19.4 Two bugs the rehearsal caught — read these

**(a) A learned finding switched the WRONG DEVICE and skipped the safety gate.**
`server.py::_load_from_rule` maps `rule_name` → load and defaulted to `"lights"`.
A learned finding carries `rule_name="learned_anomaly"`, absent from that table,
so `learned-living-ac` resolved to `living/lights`. Approving it would have
published `home/command/living/lights` — and the comfort guardrail, which keys
off the load name, saw "lights" and allowed it at 29.5 °C. Two failures from one
silent default. Fixed by carrying the Finding's real `load_key` onto the
Recommendation; the six mapped rules are byte-identical. **Now HTTP 409.**

**(b) `detector` died at the narration boundary.** It lived on the Finding but
not the Recommendation, so every dashboard card looked equally rule-derived.
Now carried through and rendered as a `rule` / `learned · 0.999` badge.

## 19.5 §5 rehearsal result

| Step | Result |
|---|---|
| 1 · rule finding ranked by planner | ✅ `planned_by=llm`, provenance verified |
| 2 · learned finding at 3 AM | ✅ score 0.805, tagged `learned` |
| 3 · approve → real bulb dark | ✅ 1.7 W → 0.0 W, `source=kasa ok=True`, booked |
| 4 · R7 refusal | ✅ **HTTP 409** (after fixing 19.4a) |
| 5 · /ask on the NPU | ✅ 4.1 s, `PROVENANCE=VERIFIED` |
| 6 · GenieX dead | ✅ all paths degrade to `template`, still functional |

Step 6 used an unreachable `LLM_BASE_URL` rather than killing the user's GenieX
service — same timeout/exception path, no risk of leaving the demo broken.

## 19.6 The provenance verifier earned its place

It caught the model doing forbidden arithmetic, unprompted, during development:

```
Q: "What if I shift the dryer to 9 PM?"
A: "...reducing cost from $0.39 to $0.19, saving $0.20."   -> UNVERIFIED [0.19]
```

`$0.39` was in the digest; `$0.19` was not. Nobody anticipated that specific
failure — the check found it. Cost: 110 µs.

## 19.7 Flags

All OFF by default. `AI_ANOMALY=1` `AI_PLAN=1` `AI_ASK=1`. Q&A page at `/ask`.

---

# §20 The tariff was approximate; now it is SDG&E's published table (2026-08-06)

Branch **`sdge-real-tariff`** → PR #1 against `main`. Contained: five code files
plus doc sweeps. `smoke_test.py` 32/32.

## 20.1 What was wrong

`energy_model.py` carried two constants labelled *"Rates approximate SDG&E
TOU-DR1"*: `$0.32` off-peak, `$0.58` on-peak, 4–9 PM, no seasons. Against the
utility's own table they are 35–40% low **and missing an entire pricing tier**.

| period | was | SDG&E summer | SDG&E winter |
|---|---|---|---|
| on-peak | 0.58 | **0.69654** | **0.62200** |
| off-peak | 0.32 | **0.47560** | **0.54019** |
| super off-peak | *did not exist* | **0.38818** | **0.44933** |

Source: Schedule TOU-DR1 Total Rates Table effective 1/1/2026, Total Electric
Rate column (UDC + EECC + WF-NBC/DWR-BC), bundled residential.

## 20.2 The third tier is a different recommendation, not a rounding fix

With two tiers the only advice expressible is *"move it out of peak"*. With
super off-peak the system can say *"run it after midnight"*, and that is worth
roughly 40% more per kWh shifted:

```
off-peak delta        0.69654 - 0.47560 = $0.22094/kWh
super off-peak delta  0.69654 - 0.38818 = $0.30836/kWh
```

R6 now measures against `cheapest_rate()` and its suggested action is "Delay
this cycle until after midnight". Quoting the off-peak delta understated the
saving by a third *and* named a worse time to run the load.

## 20.3 Rates are data, not constants

`data/sdge_tou_dr1.json` holds the table with `source_url`, `effective_date`
and which column was transcribed — the same discipline as the `formula`
strings. Updating on the next revision is a transcription, not a code change.

Loaded **and validated** at import (every season × period must be a number);
a missing or truncated file falls back to the old built-in rates with a printed
warning. A tariff file is not worth a dead demo.

`energy_model.py`'s docstring previously claimed "No I/O … pure functions only".
That is now false by one file read, and the docstring says so rather than
quietly becoming wrong.

## 20.4 Periods

| period | hours |
|---|---|
| on-peak | 16:00–21:00 every day, both seasons — the *price* differs by season, the hours do not |
| super off-peak | weekdays 00:00–06:00 and 10:00–14:00; weekends 00:00–14:00 |
| off-peak | the remainder |

The weekday 10:00–14:00 block was previously March/April only and is now
year-round. Public holidays are treated as weekdays: the calendar is not
modelled, which makes estimates **conservative** (it charges the higher rate).

## 20.5 October is summer — the bug this already caused

First revision had summer as June–September. SDG&E's summer is **June 1 –
October 31**, winter **November 1 – May 31**. The error priced every October
evening at the winter on-peak rate, `$0.62200` instead of `$0.69654`: a 12%
understatement for a whole month, on the tier where the money is. Fixed in
`8d0f4c1`.

The rate table splits Summer/Winter **without printing the date ranges**, so the
boundary is the one value here not read off an SDG&E document. It selects which
column applies, never a rate. Flagged in the JSON under
`not_confirmed_from_primary_source`. Closing it properly needs a real SDG&E bill
from June–October. It does **not** affect the demo, which runs in August.

## 20.6 The rate reached only one of the three model tiers

Worth checking after any change to what the models are told:

| tier | before | why |
|---|---|---|
| narration `llm.py` | OK | R6's evidence lines carry both rates and the schedule/effective date |
| Q&A `ask.py` | **NO** | the digest exposed `on_peak_rate` and `off_peak_rate` only |
| cloud report | **NO** | same digest, same omission |

So the cheapest tier — the entire point of the change — was invisible to the
tier a judge is most likely to interrogate. *"When is the cheapest time to run
the dryer?"* was unanswerable, and had the model named `$0.38818` anyway, the
provenance verifier would have flagged a **correct** answer as invented, because
that number was never given to it. `build_digest()` now carries
`super_off_peak_rate` and `tariff_source`.

**No markdown file is ever read by any model.** Every prompt is assembled in
Python from the rules engine and the digest — the only `.md` reference in `hub/`
is a `print()` in `benchmark.py`. That is what makes the provenance check
meaningful: the set of facts the model was given is enumerable.

## 20.7 A near-miss worth recording

Committing on `main` with `git add -A` staged **30 MB of recorded household
data**. The `code/data/sessions/` and `reco_dataset.csv` gitignore rules existed
only on a feature branch, so `main` had no protection at all. Caught before
push; the rules are in this PR. The project claims occupancy data never leaves
the house — that has to be true of the repo too.

## 20.8 State

- One smoke-test expectation moved with the rates: 2 h of A/C on-peak is
  **$1.532**, not $1.276. The code was right; the constant was stale.
- Docs swept for the old figures: `code/README.md`, `01_LLM_PROMPT_PACK.md`,
  `05_FILE_STRUCTURE_AND_RUN.md`, `08_HARDWARE_PIVOT_PLAN.md`,
  `00_MASTER_PLAN.md`.
- **NOT verified: no LLM ran.** GenieX needs Snapdragon; this was built on an
  x86 box, so narration, plan and Q&A paths are unexercised. Run
  `AI_ASK=1 python hub/server.py` on the X Elite and ask *"when is the cheapest
  time to run the dryer?"* — that question is now answerable and is a good demo
  beat.

---

# 21. Multi-host session (2026-08-06) — transport, staleness and process bugs

> **Written from the Windows workshop PC.** This project is now worked on from
> **multiple hosts, each running Claude.** Anything learned here must land in this
> file, or the other host re-derives it — or silently undoes it (see §21.7).

## 21.1 Button A did nothing — the publisher was ~10 minutes behind

**The headline bug.** Pressing A set `presence=away` in the UI but nothing else
happened, and no error appeared anywhere.

The MCU emits at a steady 1 Hz. `uno_q_publisher.py` read **one line per pass** while
doing **synchronous Kasa I/O in the same loop** (seconds when a device is slow to
answer). Falling behind a producer you cannot outrun makes the lag **permanent** — the
backlog only grows. Measured **87 KB queued** on the router socket, about ten minutes of
telemetry. The press incremented the MCU counter instantly, but `ButtonWatch.observe()`
was still working through lines from before the press.

**Fix (commit `dda7b42`):** drain the socket and coalesce in `tcp_lines()`.

- Acks are **events** so every one is still forwarded.
- Telemetry is a **state snapshot**, and the button fields are **cumulative counters**,
  so the newest line already carries everything the skipped ones would have said. That
  losslessness is exactly why counters were chosen over edges in the first place.
- The skipped total is **logged, not hidden** (`STALE_SKIPPED`).

**Diagnostic — the single most useful command on this project:**

```bash
adb -s 3933751369 shell "ss -tanp | grep 7500"
# The socket owned by python3 (the publisher) must have recv-q ~0.
```

## 21.2 A socket on :7500 with a huge recv-q and no owner is NOT the bug

`ss -tanp` only shows owners for **processes you own**. A connection with a large,
growing recv-q and a blank owner column belongs to a **root** process:
`arduino-router-serial.service`, the socat proxy mirroring :7500 to `/dev/ttyGS0`. It
backs up whenever no host reads that COM port, and it reappears after every reboot.

**Verified harmless** — a fresh reader still gets a clean 1 Hz (11 lines in 11 s). Only
ever judge lag by **the publisher's own socket**.

## 21.3 MQTT tunnel must be port 11883 — 1883 silently fails

**The board runs its own mosquitto on `*:1883`.** So `adb reverse tcp:1883 tcp:1883`
**cannot bind**. `adb reverse --list` still shows the mapping, but it is dead, and the
publisher connects to the **board's local broker** instead. The publisher logs
`MQTT connected rc=0` and looks perfectly healthy while the hub sits at `loads {}` and
`presence None` forever. Nothing errors.

```bash
adb -s 3933751369 reverse --remove tcp:1883
adb -s 3933751369 reverse tcp:11883 tcp:1883    # board.env: MQTT_PORT=11883
```

Prove data actually lands on the **PC** broker by subscribing to `home/#` there. Do not
trust `adb reverse --list` or the publisher's own log.

> This **supersedes the section 0 claim that MQTT runs over Wi-Fi.** It is the USB
> tunnel, on 11883, per the instruction to keep ADB-over-USB.

## 21.4 Only ever run ONE hub

Two `hub/server.py` processes both use client_id `hub-orchestrator` and kick each other
off the broker forever (`[mqtt] disconnected rc=7`). Neither ingests reliably. Seen with
**four** hub processes alive at once.

```bash
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*hub*server.py*' -and $_.Name -like 'python*' } | ForEach-Object { Write-Host $_.ProcessId }"
```

## 21.5 The LLM banner lied (commit `af4cf4b`)

The startup banner carried its own stale default `http://localhost:8080/v1` from an
earlier llama.cpp setup, while `llm.py` had moved to GenieX on
`http://127.0.0.1:18181/v1`. Inference worked fine; the banner sent anyone debugging a
quiet LLM to a port nothing was listening on. It now reads the constant from `llm.py` so
the two cannot drift again.

## 21.6 The network has moved three times — and the devices are currently GONE

The SSID stays `ArtiFi`; the network underneath does not:

```
172.20.10.8/28 (iPhone hotspot) -> 192.168.137.104/24 (Windows ICS) -> 192.168.86.51/24
```

**CURRENT BLOCKER.** After the board rebooted at 02:52, the Kasa devices are not present.
Discovery from the PC finds four devices, none of them ours:

```
192.168.86.28 Living room light 2 | .23 Front door light | .30 TP-LINK_Smart Plug_5307 | .39 Office 1
```

Direct probes of the IPs documented in section 0 (`192.168.86.49`, `192.168.86.20`) both
time out. Those four are almost certainly **other people's devices on the venue LAN — do
NOT bind the actuator to them.**

Result: `[uno_q] command listener ready (actuator source: simulated)`. Sensing, cues,
presence, rules and `/ask` all still work; **only the real physical actuation is dead**,
which is the Archetype E beat.

Established while diagnosing:

- The board **can** reach Kasa devices on `192.168.86.x` by **unicast** (`.28:9999` and
  `.39:9999` both open), so board-side actuation works once the right devices return.
- The board's **UDP discovery broadcast is blocked** on this LAN — that is why the board
  found nothing while the PC found four. Set explicit IPs in `board.env`.
- The board **cannot ping the PC** (Windows firewall drops ICMP). Harmless, because MQTT
  goes over the USB tunnel.
- **A reboot destroys `adb reverse`**; recreate it. `board.env` survives a reboot.
  A missing `/tmp/publisher.log` is a reliable reboot tell.

## 21.7 A force-push from another host destroyed work

`origin/main` went `7c62547 -> fb0993c`, **skipping `96169b9`**. Lost: the Occupancy
removal, the auto-actuation cooldown fix, and env-overridable grace periods. Recovered by
merging local into `origin/main` as `2355f31`.

**Commits being reachable is NOT proof the content survived.** Audit by content:

```bash
git show origin/main:code/simulator/index.html | grep -c 'id="occOn"'   # expect 0
git show origin/main:code/hub/server.py | grep -c 'RESET_SETTLE_S'      # expect >=1
[ "$(git rev-parse HEAD^{tree})" = "$(git rev-parse origin/main^{tree})" ] && echo IDENTICAL
```

**Pull before you push. Never force-push `main`.**

## 21.8 Current button mapping (supersedes section 0)

| Button | Action | Counter |
|---|---|---|
| **A** | presence to **away**, and sets occupancy false | `b1` |
| **B** | ambient light toggle (lux 900 / 60, threshold 300) | `b2` |
| **C** | **reset to steady state** (home, lights + HVAC on) | `b3` |

Presses travel as **cues** on MQTT `home/demo/cue` so the simulator moves its own
controls rather than having values change behind its back. A must set **both** presence
and occupancy — setting only presence left occupancy true, R1 bailed on its first line,
and beat 2 silently did nothing.

**Occupancy was removed from the simulator UI entirely** — `setPres()` drives `setOcc()`.
Do not reintroduce it.

Simulate a press without hardware (always `shutdown()` then `close()`, or you strand a
socket):

```bash
adb -s 3933751369 shell "timeout 10 python3 -c \"
import socket,time
s=socket.create_connection(('127.0.0.1',7500),5)
try: s.sendall(b'SIMBTN a\n'); time.sleep(2)
finally: s.shutdown(socket.SHUT_RDWR); s.close()\""
```

## 21.9 State-shape gotcha

`/api/state` puts **`loads` at the top level**, keyed `"living/lights"` — *not*
`rooms.living.loads`. Presence is at **`user.presence`** — *not* top level. Reading the
wrong paths makes a completely healthy system look dead.

## 21.10 Verified at end of session

| Check | Result |
|---|---|
| `smoke_test.py` | **32/32** |
| Board runs the committed publisher | byte-identical after CRLF normalization |
| Publisher recv-q | **0** (was 87 KB) |
| `SIMBTN a` to hub | `presence=away`, re-confirmed after a cold boot |
| Banner LLM URL | `http://127.0.0.1:18181/v1`, matches `llm.py` |
| Physical actuation | **simulated** — devices absent, see 21.6 |

`smoke_test.py` has **zero** coverage of `uno_q_publisher.py`, so 21.1 is verified
behaviourally, not by tests.

## 21.11 Beat 2 latency: 9.6 s -> 0.6 s, by reordering the tick

**Symptom:** pressing A took ~10 s to switch the bulb, while pressing C switched it
instantly. The instinct that "this is not Kasa delay" was correct.

**Measured hop by hop** with a passive MQTT probe on the PC broker (never poll Kasa
directly for this — the firmware serves one connection at a time, and a second poller
is what caused the flicker bug):

```
cue "presence away"      t+4.11s
command lights->off      t+16.69s   <- 9.6 s after the finding was actionable
kasa switched, ok=true   t+17.27s   <- 0.6 s
```

So the Kasa switch was never the problem. Two false leads were eliminated first:

- **Not the grace period.** `DEMO_GRACE_S` was already dropped 6 -> 1 with no effect.
- **Not R1's conditions.** Occupancy false, `unoccupied_s >= grace` and lights `"on"`
  were all satisfied ~9 s before the command went out.

**Root cause:** `evaluation_tick()` ran in the order

```
rules.evaluate()        0.014 ms
planner.PLANNER.plan()  NPU call, seconds     <- AI_PLAN=1
AUTO-ACT block          the command
```

The planner caches on the frozenset of finding ids, so it only calls the model when the
finding set **changes** — which is exactly the tick where R1 first fires. The one tick
that mattered paid the full model cost before it was allowed to switch a bulb.

**Fix:** move the AUTO-ACT block **before** plan synthesis and narration. Nothing in it
depends on the plan. Result:

```
cue "presence away"      t+4.11s
command lights->off      t+4.13s    <- 20 ms
kasa switched, ok=true   t+4.71s    <- 0.6 s
```

**Cue to bulb dark: 0.60 s.** Press-to-dark is about 1.7 s including the 1 Hz MCU
telemetry hop — inside the 3 s target.

This is also the honest shape of the three-tier story: the deterministic rule fires in
microseconds and the model explains afterwards, rather than the explanation gating the
action. **Do not reorder these back.**

### Two measurement traps that produced wrong numbers first

1. **A freshly restarted hub has no load state.** Loads publish on change only, so R1
   cannot fire until the publisher's next **30 s** Kasa poll repopulates it. An early
   measurement showed 6.2 s that was entirely this artifact. Always measure on a warm hub.
2. **Button C's reset takes ~10 s** to finish its Kasa switching. Pressing A before it
   settles measures the tail of the reset, not beat 2. Wait ~30 s after C.

Current demo timings in use (shipping defaults untouched, all env-overridable):
`DEMO_GRACE_S=1 DEMO_AWAY_GRACE_S=1 AUTO_COOLDOWN_S=3 EVAL_INTERVAL_S=1 RESET_SETTLE_S=3`

### Also observed, unresolved

- `[planner] PROVENANCE FAIL - numbers not in source: ['2400']` — the verifier caught
  the model inventing a figure. Working as designed; good Beat 3 material.
- Every reco reads **$0.00**. The HS110 "Space heater" plug is ON and metering correctly
  but has **nothing plugged into it** (`power=0 W, current=0.0118 A` standby leakage), so
  the only real load in the demo is the 10.8 W bulb. Plugging a real load in is the honest
  fix for the "dollar figures too small" beat issue — a 1500 W heater turns $0.042 into
  about **$1.05/hour** at the on-peak rate, genuinely measured rather than scaled.
