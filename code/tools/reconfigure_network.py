#!/usr/bin/env python3
"""Re-point the whole demo at whatever network you are on right now.

Run this after joining a different Wi-Fi (venue, phone hotspot, anything). It:

  1. finds this PC's LAN IP,
  2. discovers the Kasa devices and matches them to the demo's loads by ALIAS
     (aliases travel with the device; IPs do not),
  3. rewrites code/arduino/board.env with the new IPs,
  4. pushes it to the UNO Q over ADB and restarts the publisher,
  5. verifies the board can actually reach the broker and the devices.

    python tools/reconfigure_network.py                  # do it
    python tools/reconfigure_network.py --dry-run        # just show what changed

WHAT THIS CANNOT FIX: a Kasa plug or bulb only joins the ONE Wi-Fi network it
was provisioned for. Moving to a new SSID means re-onboarding each device in the
Kasa app first — this script cannot do that, and will tell you plainly which
devices it could not find. The way to avoid that entirely is to make the new
network present the SAME SSID and password as the one the devices already know
(a phone hotspot can be named anything), in which case everything re-joins on
its own and this script just fixes up the IPs.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD_ENV = ROOT / "arduino" / "board.env"
BOARD_ENV_EXAMPLE = ROOT / "arduino" / "board.env.example"
BOARD_DEST = "/home/arduino/ai-home-energy-concierge/code/arduino"

ADB = os.environ.get("ADB_PATH") or str(
    Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe")


def pc_lan_ip() -> str | None:
    """The address other devices can reach this PC on.

    Uses a UDP connect to a public address, which does not send anything but
    makes the OS pick the interface it would actually route over — more reliable
    than parsing ipconfig when several adapters are up.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


async def discover_kasa(timeout: float = 10.0) -> dict[str, str]:
    """alias -> host for every Kasa device we can see."""
    try:
        from kasa import Discover
    except ImportError:
        print("  ! python-kasa not installed here; skipping device discovery")
        return {}
    found: dict[str, str] = {}
    try:
        devices = await Discover.discover(timeout=timeout)
    except Exception as exc:
        print(f"  ! discovery failed: {exc}")
        return {}
    for host, dev in devices.items():
        try:
            await dev.update()
            found[dev.alias.strip()] = host
        except Exception:
            continue
    return found


async def probe_host(host: str) -> str | None:
    """Alias of the device at `host`, over TCP, or None.

    Worth trying even when UDP discovery came back empty: a Kasa BULB that is
    currently switched OFF stops answering discovery altogether, but still
    answers a direct TCP connect. Since "switched off" is the normal resting
    state of the demo's lights, discovery alone would lose them every time.
    """
    if not host:
        return None
    try:
        from kasa import Device
    except ImportError:
        return None
    # Retry: TP-Link firmware serves one connection at a time, and the board's
    # publisher is very likely polling this same device right now.
    for _ in range(4):
        try:
            dev = await Device.connect(host=host)
            await dev.update()
            alias = dev.alias
            await dev.disconnect()
            return alias
        except Exception:
            await asyncio.sleep(2.5)
    return None


def adb(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="show the new config without writing or deploying it")
    ap.add_argument("--skip-board", action="store_true",
                    help="rewrite board.env but do not push/restart over ADB")
    args = ap.parse_args()

    print("=" * 62)
    print(" Re-pointing the demo at the current network")
    print("=" * 62)

    ip = pc_lan_ip()
    if not ip:
        print("! could not determine this PC's LAN IP — are you on a network?")
        return 1
    print(f"\n1. This PC: {ip}")

    src = BOARD_ENV if BOARD_ENV.exists() else BOARD_ENV_EXAMPLE
    env = read_env(src)
    if not env:
        print(f"! no config found at {src}")
        return 1
    print(f"2. Config template: {src.name}")

    # Stop the board publisher BEFORE touching any device. It polls the same
    # Kasa devices every few seconds and TP-Link firmware serves one connection
    # at a time, so leaving it running makes our discovery and probes time out
    # intermittently — which looks exactly like "device not on this network".
    # We restart it at the end anyway.
    if not args.dry_run and not args.skip_board and Path(ADB).exists():
        if "\tdevice" in adb("devices").stdout:
            adb("shell", "for p in $(pgrep -f uno_q_publisher.py); do kill -9 $p; done")
            print("   (paused the board publisher to avoid device contention)")
            import time as _t
            _t.sleep(2)

    loads = [s.strip() for s in env.get("KASA_LOADS", "lights,ac").split(",") if s.strip()]
    wanted = {ld: env.get(f"KASA_{ld.upper()}_ALIAS", "") for ld in loads}
    print(f"   loads: {', '.join(f'{k}={v!r}' for k, v in wanted.items())}")

    print("\n3. Discovering Kasa devices...")
    found = asyncio.run(discover_kasa())
    if found:
        for alias, host in sorted(found.items()):
            print(f"   {host:<16} {alias}")
    else:
        print("   (none found — see the note about re-provisioning below)")

    lower = {a.lower(): h for a, h in found.items()}
    resolved: dict[str, str] = {}
    kept: list[str] = []
    missing: list[str] = []
    for load, alias in wanted.items():
        host = lower.get(alias.strip().lower(), "")
        if host:
            resolved[load] = host
            continue
        # Discovery missed it. Before giving up, try the previously-configured
        # host over TCP — a switched-off bulb answers that but not discovery.
        old = env.get(f"KASA_{load.upper()}_HOST", "")
        seen = asyncio.run(probe_host(old))
        if seen and (not alias or seen.strip().lower() == alias.strip().lower()):
            resolved[load] = old
            kept.append(f"{load} -> {old} ({seen}, still reachable)")
        elif alias:
            missing.append(f"{load} ({alias!r})")

    print("\n4. New configuration")
    print(f"   MQTT_HOST = {ip}   (was {env.get('MQTT_HOST', '?')})")
    for load in loads:
        old = env.get(f"KASA_{load.upper()}_HOST", "?")
        new = resolved.get(load)
        print(f"   KASA_{load.upper()}_HOST = {new or '(unresolved)'}   (was {old})")

    if kept:
        print("\n   (discovery missed these; kept the old IP after a TCP check —")
        print("    a switched-off bulb answers TCP but not UDP discovery)")
        for k in kept:
            print(f"     {k}")

    if missing:
        print("\n   ! NOT FOUND on this network: " + ", ".join(missing))
        print("     A Kasa device only joins the ONE network it was provisioned")
        print("     for. Re-onboard it in the Kasa app, or give this network the")
        print("     same SSID + password as the one the device already knows.")
        print("     Kasa devices are 2.4 GHz only — if this is a phone hotspot,")
        print("     make sure it is not 5 GHz-only (iPhone: 'Maximize Compatibility').")

    # Rewrite only the value lines we own; keep every comment intact.
    text = src.read_text(encoding="utf-8")
    def set_kv(t: str, key: str, val: str) -> str:
        pat = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        line = f'{key}={val}'
        return pat.sub(line, t) if pat.search(t) else t + f"\n{line}\n"

    text = set_kv(text, "MQTT_HOST", ip)
    for load, host in resolved.items():
        text = set_kv(text, f"KASA_{load.upper()}_HOST", host)

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0

    # newline="\n" is load-bearing, not style. This runs on Windows, where the
    # default text mode turns every "\n" into "\r\n" — and board.env is sourced
    # by bash ON THE BOARD, which keeps the CR as part of the VALUE. That gives
    # MCU_TCP_HOST="127.0.0.1\r" and MQTT_HOST="192.168.86.34\r": both connects
    # fail, so the publisher silently falls back to synthetic sensor data AND
    # never reaches the broker. It looks like a dead board, not a corrupt file.
    # This exact bug has now bitten the project twice; hence the explicit
    # newline plus the CR strip on deploy.
    BOARD_ENV.write_text(text, encoding="utf-8", newline="\n")
    print(f"\n5. Wrote {BOARD_ENV}")

    if args.skip_board:
        print("   (--skip-board: not deploying)")
        return 0

    if not Path(ADB).exists():
        print(f"   ! adb not found at {ADB}; set ADB_PATH. Board not updated.")
        return 1

    r = adb("devices")
    if "\tdevice" not in r.stdout:
        print("   ! no board over ADB. Connect USB-C, or copy board.env across by hand.")
        return 1

    print("6. Deploying to the board...")
    adb("push", str(BOARD_ENV), f"{BOARD_DEST}/board.env")
    # Strip CR on the board as well. The write above is the real fix, but this
    # also protects against a board.env edited by hand on Windows, or pushed by
    # some other route. A stray CR here does not fail loudly — it produces a
    # running publisher quietly serving invented data, so it is worth being
    # defensive twice.
    adb("shell", f"sed -i 's/\\r$//' {BOARD_DEST}/board.env")
    adb("shell", "for p in $(pgrep -f uno_q_publisher.py); do kill -9 $p; done")
    # Launch WITHOUT waiting. `adb shell "cmd &"` does not return: adb keeps the
    # connection open as long as anything holds the stream, even with nohup and
    # redirected fds. So fire it off with Popen and confirm via pgrep instead.
    launch = (f"cd {BOARD_DEST} && set -a && . ./board.env && set +a && "
              f"setsid nohup ~/energy-venv/bin/python3 -u uno_q_publisher.py "
              f"> /tmp/publisher.log 2>&1 < /dev/null &")
    subprocess.Popen([ADB, "shell", launch],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    import time
    for _ in range(15):
        time.sleep(2)
        if adb("shell", "pgrep -f uno_q_publisher.py").stdout.strip():
            break
    time.sleep(6)
    log = adb("shell", "grep -E 'kasa\\]|ready|MQTT connected' /tmp/publisher.log").stdout
    print("\n7. Board reports:")
    for line in log.strip().splitlines()[:8]:
        print("   " + line.strip())

    ok = "command listener ready" in log
    print("\n" + ("DONE — publisher is up." if ok else
                  "! publisher did not report ready; check /tmp/publisher.log"))
    print("Open the simulator at:  http://%s:8000/simulator" % ip)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
