#!/usr/bin/env python3
"""
spen_adb.py
===========
Reads raw S Pen input events from your Samsung tablet over ADB
and decodes them into structured pen state.

No Android app required — just USB + ADB.

Usage:
    python spen_adb.py               # auto-detect the S Pen event device
    python spen_adb.py --event 4     # force /dev/input/event4
    python spen_adb.py --list        # list all input devices and exit

Requirements:
    pip install pyserial  # not needed, stdlib only
    adb in your PATH (from Android platform-tools)

How it works:
    adb shell getevent -t /dev/input/eventN
    Each line is:  [timestamp] EV_TYPE  ABS_CODE  value
    We parse EV_ABS packets into X, Y, pressure, tilt, and EV_KEY for buttons.
    On EV_SYN SYN_REPORT we emit a complete PenState snapshot.
"""

import subprocess
import sys
import re
import argparse
import time
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# evdev constants (matching Linux input.h)
# ---------------------------------------------------------------------------
EV_SYN       = 0x00
EV_KEY       = 0x01
EV_ABS       = 0x03

SYN_REPORT   = 0x00

ABS_X         = 0x00
ABS_Y         = 0x01
ABS_PRESSURE  = 0x18
ABS_DISTANCE  = 0x19
ABS_TILT_X    = 0x1a
ABS_TILT_Y    = 0x1b

BTN_TOUCH     = 0x14a
BTN_TOOL_PEN  = 0x140
BTN_TOOL_RUBBER = 0x141
BTN_STYLUS    = 0x14b   # S Pen side button
BTN_STYLUS2   = 0x14c   # second button (some pens)

# Samsung-specific: some Tab S models also emit MT events.
# We ignore ABS_MT_* and focus on the non-MT stylus axes.
ABS_MT_SLOT        = 0x2f
ABS_MT_POSITION_X  = 0x35
ABS_MT_POSITION_Y  = 0x36
ABS_MT_PRESSURE    = 0x3a

# human-readable names for logging
EV_TYPE_NAMES = {EV_SYN: "EV_SYN", EV_KEY: "EV_KEY", EV_ABS: "EV_ABS"}
ABS_CODE_NAMES = {
    ABS_X: "ABS_X", ABS_Y: "ABS_Y",
    ABS_PRESSURE: "ABS_PRESSURE", ABS_DISTANCE: "ABS_DISTANCE",
    ABS_TILT_X: "ABS_TILT_X", ABS_TILT_Y: "ABS_TILT_Y",
}
KEY_CODE_NAMES = {
    BTN_TOUCH: "BTN_TOUCH", BTN_TOOL_PEN: "BTN_TOOL_PEN",
    BTN_TOOL_RUBBER: "BTN_TOOL_RUBBER",
    BTN_STYLUS: "BTN_STYLUS", BTN_STYLUS2: "BTN_STYLUS2",
}

# ---------------------------------------------------------------------------
# Axis calibration  (read from your device via --list, then set here)
# ---------------------------------------------------------------------------
# These are the defaults for Galaxy Tab S6/S7/S8.
# If your pen tracks wrong, run --list and read maxX/maxY from your device.
AXIS_DEFAULTS = {
    ABS_X:        {"min": 0, "max": 14679},   # Updated for your specific tablet
    ABS_Y:        {"min": 0, "max": 23487},
    ABS_PRESSURE: {"min": 0, "max": 4095},
    ABS_TILT_X:   {"min": -63, "max": 63},
    ABS_TILT_Y:   {"min": -63, "max": 63},
    ABS_DISTANCE: {"min": 0, "max": 255},
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
@dataclass
class PenState:
    # Raw values from kernel
    x_raw:        int   = 0
    y_raw:        int   = 0
    pressure_raw: int   = 0
    tilt_x_raw:   int   = 0
    tilt_y_raw:   int   = 0
    distance_raw: int   = 0

    # Normalized
    x:        float = 0.0   # 0.0 – 1.0
    y:        float = 0.0
    pressure: float = 0.0   # 0.0 – 1.0

    # Degrees
    tilt_x:   float = 0.0   # –90 to +90
    tilt_y:   float = 0.0

    # Buttons
    touching:  bool = False  # BTN_TOUCH
    pen_down:  bool = False  # BTN_TOOL_PEN
    eraser:    bool = False  # BTN_TOOL_RUBBER
    button:    bool = False  # BTN_STYLUS (side button)
    button2:   bool = False  # BTN_STYLUS2

    # Meta
    timestamp: float = 0.0  # kernel timestamp (seconds)
    seq:       int   = 0    # increments every SYN_REPORT

    def normalize(self, axis_cal: dict):
        def norm(val, mn, mx):
            r = mx - mn
            return (val - mn) / r if r else 0.0

        self.x        = norm(self.x_raw,        *_cal(axis_cal, ABS_X))
        self.y        = norm(self.y_raw,         *_cal(axis_cal, ABS_Y))
        self.pressure = norm(self.pressure_raw,  *_cal(axis_cal, ABS_PRESSURE))
        self.tilt_x   = float(self.tilt_x_raw)
        self.tilt_y   = float(self.tilt_y_raw)

    def __str__(self):
        action = "ERASE" if self.eraser else ("DOWN" if self.touching else "HOVER")
        return (
            f"[{self.seq:6d}] {action:<6s}  "
            f"x={self.x:.4f}  y={self.y:.4f}  "
            f"p={self.pressure:.4f}  "
            f"tilt=({self.tilt_x:+.1f}°, {self.tilt_y:+.1f}°)  "
            f"btn={self.button}  btn2={self.button2}"
        )

def _cal(axis_cal, code):
    d = axis_cal.get(code, {"min": 0, "max": 1})
    return d["min"], d["max"]

# ---------------------------------------------------------------------------
# ADB helpers
# ---------------------------------------------------------------------------

def adb(*args, capture=True) -> str:
    cmd = ["adb"] + list(args)
    if capture:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.stdout + r.stderr
    return ""


def check_adb():
    out = adb("devices")
    lines = [l for l in out.splitlines() if l.strip() and "List of" not in l]
    if not lines:
        print("ERROR: No ADB devices found. Connect your tablet and enable USB debugging.")
        sys.exit(1)
    print(f"ADB device: {lines[0]}")


def list_input_devices():
    """Print all /dev/input devices and their reported names/events."""
    print("\n=== Input devices on tablet ===")
    out = adb("shell", "getevent", "-i")
    print(out)


def find_spen_event_device() -> Optional[str]:
    """
    Auto-detect the S Pen event node.
    Looks for a device advertising ABS_PRESSURE and BTN_TOOL_PEN.
    Returns e.g. "/dev/input/event4" or None.
    """
    out = adb("shell", "getevent", "-i")
    current_dev = None
    has_pressure = False
    has_pen_btn  = False
    candidates = []

    for line in out.splitlines():
        # Device header
        m = re.match(r"add device \d+: (/dev/input/event\d+)", line)
        if m:
            if current_dev and has_pressure and has_pen_btn:
                candidates.append(current_dev)
            current_dev  = m.group(1)
            has_pressure = False
            has_pen_btn  = False
            continue

        # Name line — fast pre-filter
        if "name:" in line.lower():
            name = line.lower()
            # Wacom/stylus hint
            if any(k in name for k in ("wacom", "stylus", "pen", "spen", "wcom")):
                has_pen_btn = True   # trust the name

        # Events line — look for ABS_PRESSURE (0018) and BTN_TOOL_PEN (0140)
        if "0018" in line:  # ABS_PRESSURE
            has_pressure = True
        if "0140" in line:  # BTN_TOOL_PEN
            has_pen_btn = True

    # flush last device
    if current_dev and has_pressure and has_pen_btn:
        candidates.append(current_dev)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # Prefer the one with "pen" or "wacom" in the name
    for c in candidates:
        dev_info = adb("shell", f"cat /sys/class/input/{c.split('/')[-1]}/device/name")
        if any(k in dev_info.lower() for k in ("wacom", "pen", "spen", "stylus")):
            return c
    return candidates[0]


def read_axis_calibration(event_dev: str) -> dict:
    """
    Read min/max for each axis from the kernel via getevent -i.
    Returns dict keyed by ABS code int.
    """
    out = adb("shell", "getevent", "-i", event_dev)
    cal = dict(AXIS_DEFAULTS)  # start with defaults

    # Pattern: "    0000  : value 0, min 0, max 20967, fuzz 0, flat 0, resolution 100"
    axis_re = re.compile(r"(\w{4})\s+:\s+value\s+\d+,\s+min\s+(-?\d+),\s+max\s+(-?\d+)")
    for line in out.splitlines():
        m = axis_re.search(line)
        if m:
            code = int(m.group(1), 16)
            mn   = int(m.group(2))
            mx   = int(m.group(3))
            if code in AXIS_DEFAULTS:
                cal[code] = {"min": mn, "max": mx}
                print(f"  Axis {ABS_CODE_NAMES.get(code, hex(code))}: min={mn} max={mx}")

    return cal


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
# getevent -t output format (with timestamps):
#   [   12345.678901] /dev/input/event4: 0003 0000 00001234
#   type code  value all in hex
#
# getevent without -t (no device name, just events when scoped to one device):
#   0003 0000 00001234

LINE_RE_WITH_TS   = re.compile(r"\[\s*([\d.]+)\]\s+\S+:\s+([0-9a-f]{4})\s+([0-9a-f]{4})\s+([0-9a-f]{8})")
LINE_RE_WITHOUT_TS= re.compile(r"([0-9a-f]{4})\s+([0-9a-f]{4})\s+([0-9a-f]{8})")


def parse_line(line: str):
    """Returns (timestamp, ev_type, ev_code, ev_value) or None."""
    m = LINE_RE_WITH_TS.search(line)
    if m:
        ts  = float(m.group(1))
        typ = int(m.group(2), 16)
        cod = int(m.group(3), 16)
        val = int(m.group(4), 16)
        # ev_value is a signed 32-bit int
        if val >= 0x80000000:
            val -= 0x100000000
        return ts, typ, cod, val

    m = LINE_RE_WITHOUT_TS.search(line)
    if m:
        typ = int(m.group(1), 16)
        cod = int(m.group(2), 16)
        val = int(m.group(3), 16)
        if val >= 0x80000000:
            val -= 0x100000000
        return time.monotonic(), typ, cod, val

    return None


# ---------------------------------------------------------------------------
# Main event loop
# ---------------------------------------------------------------------------

def stream_events(event_dev: str, axis_cal: dict, verbose: bool, callback=None):
    """
    Opens `adb shell getevent -t <event_dev>` as a subprocess and
    continuously reads lines, emitting a PenState on every SYN_REPORT.

    callback(state: PenState) is called on each sync if provided.
    """
    cmd = ["adb", "shell", "getevent", "-t", event_dev]
    print(f"\nStreaming from {event_dev}  (Ctrl-C to stop)\n{'-'*60}")

    state  = PenState()
    seq    = 0
    proc   = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)

    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue

            parsed = parse_line(line)
            if parsed is None:
                if verbose:
                    print(f"[unparsed] {line}")
                continue

            ts, ev_type, ev_code, ev_value = parsed

            if ev_type == EV_ABS:
                if   ev_code == ABS_X:        state.x_raw        = ev_value
                elif ev_code == ABS_Y:        state.y_raw        = ev_value
                elif ev_code == ABS_PRESSURE: state.pressure_raw = ev_value
                elif ev_code == ABS_TILT_X:   state.tilt_x_raw   = ev_value
                elif ev_code == ABS_TILT_Y:   state.tilt_y_raw   = ev_value
                elif ev_code == ABS_DISTANCE: state.distance_raw = ev_value
                # ignore MT axes

            elif ev_type == EV_KEY:
                if   ev_code == BTN_TOUCH:        state.touching = bool(ev_value)
                elif ev_code == BTN_TOOL_PEN:     state.pen_down = bool(ev_value)
                elif ev_code == BTN_TOOL_RUBBER:  state.eraser   = bool(ev_value)
                elif ev_code == BTN_STYLUS:       state.button   = bool(ev_value)
                elif ev_code == BTN_STYLUS2:      state.button2  = bool(ev_value)

            elif ev_type == EV_SYN and ev_code == SYN_REPORT:
                state.normalize(axis_cal)
                state.timestamp = ts
                state.seq       = seq
                seq += 1

                if callback:
                    callback(state)
                else:
                    # Skip pure hover with zero pressure to reduce noise
                    if state.pen_down or state.touching or state.eraser:
                        print(state)
                    elif verbose:
                        print(f"[HOVER] x={state.x:.3f} y={state.y:.3f} dist={state.distance_raw}")

    except KeyboardInterrupt:
        print(f"\n\nStopped. Total syncs: {seq}")
    finally:
        proc.terminate()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Read S Pen events from Samsung tablet via ADB"
    )
    ap.add_argument("--event", type=int, default=None,
                    help="Force event device number (e.g. 4 for /dev/input/event4)")
    ap.add_argument("--list",  action="store_true",
                    help="List all input devices on the tablet and exit")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Print all events including hover and unparsed lines")
    args = ap.parse_args()

    check_adb()

    if args.list:
        list_input_devices()
        sys.exit(0)

    # Resolve event device
    if args.event is not None:
        event_dev = f"/dev/input/event{args.event}"
        print(f"Using forced device: {event_dev}")
    else:
        print("Auto-detecting S Pen event device...")
        event_dev = find_spen_event_device()
        if event_dev is None:
            print(
                "Could not auto-detect S Pen device.\n"
                "Run with --list to see all devices, then use --event N."
            )
            sys.exit(1)
        print(f"Found: {event_dev}")

    # Read actual axis calibration from device
    print("\nReading axis calibration from device:")
    axis_cal = read_axis_calibration(event_dev)

    # Stream
    stream_events(event_dev, axis_cal, verbose=args.verbose)


if __name__ == "__main__":
    main()