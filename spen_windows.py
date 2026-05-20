"""
spen_windows.py 
======================================
Moves the actual Windows mouse cursor using SendInput with absolute
coordinates.
"""

import sys, os, ctypes, ctypes.wintypes as wt, argparse
ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor DPI aware

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spen_adb import (
    check_adb, find_spen_event_device,
    read_axis_calibration, stream_events, PenState,
)

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# SendInput definitions

MOUSEEVENTF_MOVE        = 0x0001
MOUSEEVENTF_LEFTDOWN    = 0x0002
MOUSEEVENTF_LEFTUP      = 0x0004
MOUSEEVENTF_RIGHTDOWN   = 0x0008
MOUSEEVENTF_RIGHTUP     = 0x0010
MOUSEEVENTF_ABSOLUTE    = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000   # coords span ALL monitors

INPUT_MOUSE = 0

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   ctypes.c_ulong),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("_u", _INPUT_UNION)]

def send_input(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

def make_mouse_input(flags, dx=0, dy=0, data=0) -> INPUT:
    mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data,
                    dwFlags=flags, time=0,
                    dwExtraInfo=ctypes.pointer(ctypes.c_ulong(0)))
    inp = INPUT(type=INPUT_MOUSE)
    inp._u.mi = mi
    return inp

# Virtual desktop info (for absolute mouse coordinates)

SM_XVIRTUALSCREEN  = 76
SM_YVIRTUALSCREEN  = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

def get_virtual_desktop():
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, w, h

def norm_to_absolute(nx: float, ny: float):
    """Normalized tablet coords → SendInput absolute units (0–65535)."""
    return int(nx * 65535), int(ny * 65535)

# Injector

class DesktopPenInjector:
    """
    Maps S Pen input to whole-desktop mouse events.

    Pen tip touching  = left mouse button held
    Side button       = right mouse button held
    Hover             = cursor moves, no buttons
    """

    def __init__(self, deadzone: float = 0.005):
        self.deadzone  = deadzone
        self._lmb_down = False
        self._rmb_down = False
        self._frame    = 0
        self.swap_axes = False
        self.invert_x = False
        self.invert_y = False
        self.scale     = 1.0
        self.offset_x  = 0.0
        self.offset_y  = 0.0

    def inject(self, state: PenState):
        # Apply optional axis swap/invert before converting to absolute units
        nx, ny = state.x, state.y
        if self.swap_axes:
            nx, ny = ny, nx

        # Adjust the "Active Area" so you don't have to reach across the entire tablet
        if self.scale != 1.0 or self.offset_x != 0.0 or self.offset_y != 0.0:
            nx = (nx - self.offset_x) * self.scale
            ny = (ny - self.offset_y) * self.scale
            nx = max(0.0, min(1.0, nx))
            ny = max(0.0, min(1.0, ny))

        if self.invert_x:
            nx = 1.0 - nx
        if self.invert_y:
            ny = 1.0 - ny

        ax, ay = norm_to_absolute(nx, ny)

        # Always move cursor
        inputs = [make_mouse_input(
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            dx=ax, dy=ay
        )]

        # Left button — pen tip contact
        touching = state.touching and state.pressure > self.deadzone
        if touching and not self._lmb_down:
            inputs.append(make_mouse_input(MOUSEEVENTF_LEFTDOWN))
            self._lmb_down = True
        elif not touching and self._lmb_down:
            inputs.append(make_mouse_input(MOUSEEVENTF_LEFTUP))
            self._lmb_down = False

        # Right button — S Pen side button
        if state.button and not self._rmb_down:
            inputs.append(make_mouse_input(MOUSEEVENTF_RIGHTDOWN))
            self._rmb_down = True
        elif not state.button and self._rmb_down:
            inputs.append(make_mouse_input(MOUSEEVENTF_RIGHTUP))
            self._rmb_down = False

        send_input(*inputs)
        self._frame += 1

        if self._frame % 30 == 0:
            action = "DOWN" if self._lmb_down else "HOVER"
            print(
                f"\r[{state.seq:6d}] {action:<5s}  "
                f"x={state.x:.3f}  y={state.y:.3f}  "
                f"p={state.pressure:.3f}  "
                f"tilt=({state.tilt_x:+.0f}°,{state.tilt_y:+.0f}°)  "
                f"LMB={self._lmb_down}  RMB={self._rmb_down}",
                end="", flush=True
            )

# Main

def main():
    ap = argparse.ArgumentParser(
        description="S Pen to Windows"
    )
    ap.add_argument("--event",    type=int,   default=None)
    ap.add_argument("--deadzone", type=float, default=0.005,
                    help="Pressure threshold to register a click (default 0.005)")
    ap.add_argument("--verbose",  "-v", action="store_true")
    ap.add_argument("--swap-axes", action="store_true",
                    help="Swap tablet X/Y axes (fixes rotated mappings)")
    ap.add_argument("--invert-x", action="store_true",
                    help="Invert the tablet X axis before mapping")
    ap.add_argument("--invert-y", action="store_true",
                    help="Invert the tablet Y axis before mapping")
    ap.add_argument("--wireless", action="store_true",
                    help="Receive UDP packets from tablet daemon instead of running local adb")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="Scale factor for the active area (e.g. 2.5 means you only need to move 40% as far)")
    ap.add_argument("--offset-x", type=float, default=0.0,
                    help="X offset for the active area (0.0 to 1.0)")
    ap.add_argument("--offset-y", type=float, default=0.0,
                    help="Y offset for the active area (0.0 to 1.0)")
    args = ap.parse_args()

    vx, vy, vw, vh = get_virtual_desktop()
    print(f"Virtual desktop      : {vw}×{vh}  origin=({vx},{vy})")

    if not args.wireless:
        check_adb()

        if args.event is not None:
            event_dev = f"/dev/input/event{args.event}"
        else:
            print("Auto-detecting S Pen event device...")
            event_dev = find_spen_event_device()
            if not event_dev:
                print("Could not auto-detect. Use --event N")
                sys.exit(1)
        print(f"Tablet event device  : {event_dev}")

    print("Reading axis calibration...")
    # NOTE: With the wireless daemon, you might need to hardcode parsing or adapt read_axis_calibration to fetch once via IP later
    from spen_adb import AXIS_DEFAULTS
    axis_cal = read_axis_calibration(event_dev) if not args.wireless else AXIS_DEFAULTS

    injector = DesktopPenInjector(deadzone=args.deadzone)
    injector.swap_axes = args.swap_axes
    injector.invert_x  = args.invert_x
    injector.invert_y  = args.invert_y
    injector.scale     = args.scale
    injector.offset_x  = args.offset_x
    injector.offset_y  = args.offset_y

    print("\nReady — move the S Pen over your tablet to control the cursor.")
    print("Tip touch = left click.  Side button = right click.")
    print("Ctrl-C to stop.\n")
    print("-" * 60)

    try:
        if args.wireless:
            from spen_listener import listen_and_inject
            listen_and_inject(injector.inject)
        else:
            stream_events(
                event_dev, axis_cal,
                verbose  = args.verbose,
                callback = injector.inject,
            )
    finally:
        if injector._lmb_down:
            send_input(make_mouse_input(MOUSEEVENTF_LEFTUP))
        if injector._rmb_down:
            send_input(make_mouse_input(MOUSEEVENTF_RIGHTUP))
        print("\nStopped.")

if __name__ == "__main__":
    main()