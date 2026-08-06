<#
  Launch the autonomous demo with everything visible at once.

      .\tools\run_demo.ps1

  Opens three windows tiled on the primary display:

      +---------------------------+---------------------------+
      |  SIMULATOR                |  DASHBOARD                |
      |  (watch presence move)    |  (watch watts drop)       |
      +---------------------------+---------------------------+
      |  SCRIPT OUTPUT - what it is doing and what it EXPECTS  |
      +-------------------------------------------------------+

  Why a launcher instead of just running the script: demo_autopilot.py opens the
  two browser windows itself, but its own output goes to whatever terminal
  started it. If that terminal is behind the browsers, or belongs to another
  process, you cannot watch the narration alongside the UI it is narrating.
  This puts all three on screen together and keeps the terminal open at the end
  so the result stays readable.
#>

param(
    [switch]$NoBrowser,
    [int]$TopFraction = 55        # percent of screen height for the two browsers
)

$ErrorActionPreference = "Stop"
$code = Split-Path -Parent $PSScriptRoot
$hub  = "http://localhost:8000"

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(
      IntPtr hWnd, IntPtr after, int X, int Y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int n);
  [DllImport("user32.dll")] public static extern int GetSystemMetrics(int i);
}
"@

$sw = [Win]::GetSystemMetrics(0)
$sh = [Win]::GetSystemMetrics(1)
$topH  = [int]($sh * $TopFraction / 100)
$botY  = $topH
$botH  = $sh - $topH - 40
$halfW = [int]($sw / 2)

Write-Host "screen ${sw}x${sh} - browsers ${halfW}x${topH}, terminal ${sw}x${botH}" -ForegroundColor DarkGray

# --- stop anything a previous run left behind -------------------------------
# Both the windows AND the script. Two overlapping autopilots interleave their
# steps - one turning the bulb on while the other turns it off - and the result
# is a run where the terminal, the dashboard and the device all disagree and
# every individual reading looks explicable. Seen for real; kill first, ask
# later.
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*demo_autopilot*' -and $_.Name -eq 'python.exe' } |
    ForEach-Object {
        Write-Host "  stopping previous run (pid $($_.ProcessId))" -ForegroundColor DarkYellow
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Get-Process powershell -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -like '*QUAD demo*' } |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*quad_demo_*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 1200

# --- browsers ---------------------------------------------------------------
if (-not $NoBrowser) {
    $browser = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles}\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $browser) {
        Write-Host "No Edge/Chrome found - opening default browser, untiled." -ForegroundColor Yellow
        Start-Process "$hub/simulator"; Start-Process "$hub/"
    } else {
        # --app gives a chromeless window: no tab strip or address bar stealing
        # vertical space, which matters when the window is only ~55% of screen.
        $panes = @(
            @{ url = "$hub/simulator"; x = 0;      profile = "quad_demo_sim"  },
            @{ url = "$hub/";          x = $halfW; profile = "quad_demo_dash" }
        )
        foreach ($p in $panes) {
            Start-Process $browser -ArgumentList @(
                "--app=$($p.url)",
                "--window-position=$($p.x),0",
                "--window-size=$halfW,$topH",
                "--user-data-dir=$env:TEMP\$($p.profile)",
                "--no-first-run", "--no-default-browser-check"
            )
            Start-Sleep -Milliseconds 1400
        }
        Write-Host "simulator (left) and dashboard (right) opened" -ForegroundColor Green
        Write-Host "giving them a moment to connect their websockets..." -ForegroundColor DarkGray
        Start-Sleep -Seconds 4
    }
}

# --- the script, in its own window across the bottom ------------------------
$py  = Join-Path $code ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# -NoExit so the summary stays on screen when it finishes.
# The autopilot is told --no-browser: this launcher already opened them, and
# letting it open its own would close these and undo the tiling.
#
# The new window positions ITSELF via GetConsoleWindow(). Positioning it from
# out here needs Process.MainWindowHandle, which is still zero for the first
# moment after Start-Process returns and often never populates for a console
# host - the first version of this printed "could not position" every time.
# A process always knows its own console handle immediately.
$selfPos = @"
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class W2 {
  [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
  [DllImport("user32.dll")] public static extern bool SetWindowPos(
      IntPtr h, IntPtr a, int X, int Y, int cx, int cy, uint f);
}
'@
[void][W2]::SetWindowPos([W2]::GetConsoleWindow(), [IntPtr]::Zero, 0, $botY, $sw, $botH, 0x0040)
"@

$inner = "$selfPos; `$host.UI.RawUI.WindowTitle = 'QUAD demo - script output'; cd '$code'; & '$py' tools\demo_autopilot.py --no-browser"
Start-Process powershell -ArgumentList @("-NoExit", "-NoProfile", "-Command", $inner) | Out-Null
Write-Host "script output opened across the bottom" -ForegroundColor Green

Write-Host ""
Write-Host "Watch for:" -ForegroundColor Cyan
Write-Host "  simulator : the Away button flashes teal and moves by itself"
Write-Host "  dashboard : living/lights goes on at ~10.8 W, then to 0.0 W"
Write-Host "  the bulb  : physically switches off when the button is 'pressed'"
Write-Host "  terminal  : each step prints what it EXPECTS before the result"
