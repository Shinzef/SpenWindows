# SpenWindows

S Pen input bridge for Windows. Maps S Pen events from a Samsung tablet to the Windows mouse.

## Requirements
- Windows with Python
- Android `adb` in path
- NDK if you want use it wirelessly

## Files
- `spen_windows.py` — Main injector that moves the Windows cursor.
- `spen_adb.py` — Reads and parses S Pen events from the tablet via ADB.
- `spen_listener.py` — UDP listener for events emitted by the `spen_daemon` on the tablet.
- `spen_daemon.c` — Small C daemon (for Android) that reads the tablet event device and sends events over UDP.
- `launch.pwa.py` — Launches the fullscreen blackscreen PWA in Chrome on the tablet and lowers brightness to 0%.
- `build-push-run-spen-daemon.ps1` — Helper to build the daemon, push it to the tablet, and run it.

## Quick usage

Run with ADB (USB) — auto-detect S Pen device:

```powershell
python spen_windows.py
```

If auto-detection fails, discover the event device on the tablet and supply it:

```powershell
# On your PC
python spen_windows.py --event 4

# Or to list devices from the tablet (useful to find correct event number)
python spen_adb.py --list
```

Run wirelessly (daemon running on tablet; see next section):

```powershell
python spen_windows.py --wireless
```

Common useful options:

- `--swap-axes` : Swap tablet X/Y axes.
- `--invert-x` / `--invert-y` : Invert axes before mapping.
- `--scale <factor>` : Scale the active area (e.g. `--scale 3.0` makes the active area 1/3
- `--offset-x <val>` / `--offset-y <val>` : Offset the active area (0.0–1.0).
- `--deadzone <val>` : Pressure threshold to register a click (default `0.005`).
- `--verbose` or `-v` : Print more event information.

Example (wireless with axis tweaks):

```powershell
python spen_windows.py --wireless --swap-axes --invert-y --scale 3.1
```

After the daemon is running on the tablet, run `spen_windows.py --wireless` on the PC.

## Blackscreen PWA launcher

If you want a simple fullscreen blackscreen setup on the tablet, run:

```powershell
python launch.pwa.py
```

## Troubleshooting
- Idk bru, check if adb is running and dev options are enabled.

---
Generated README for this workspace.
