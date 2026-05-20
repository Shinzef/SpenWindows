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

# Pointer Device Injection API definitions

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerType", ctypes.c_uint32),
        ("pointerId", ctypes.c_uint32),
        ("frameId", ctypes.c_uint32),
        ("pointerFlags", ctypes.c_uint32),
        ("sourceDevice", ctypes.c_void_p),
        ("hwndTarget", ctypes.c_void_p),
        ("ptPixelLocation", POINT),
        ("ptHimetricLocation", POINT),
        ("ptPixelLocationRaw", POINT),
        ("ptHimetricLocationRaw", POINT),
        ("dwTime", ctypes.c_uint32),
        ("historyCount", ctypes.c_uint32),
        ("InputData", ctypes.c_int32),
        ("dwKeyStates", ctypes.c_uint32),
        ("PerformanceCount", ctypes.c_uint64),
        ("ButtonChangeType", ctypes.c_int32),
    ]

class POINTER_PEN_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", POINTER_INFO),
        ("penFlags", ctypes.c_uint32),
        ("penMask", ctypes.c_uint32),
        ("pressure", ctypes.c_uint32),
        ("rotation", ctypes.c_uint32),
        ("tiltX", ctypes.c_int32),
        ("tiltY", ctypes.c_int32),
    ]

class _POINTER_TYPE_INFO_UNION(ctypes.Union):
    _fields_ = [("penInfo", POINTER_PEN_INFO)]

class POINTER_TYPE_INFO(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("u", _POINTER_TYPE_INFO_UNION)]

PT_PEN = 3
POINTER_FEEDBACK_NONE = 3

POINTER_FLAG_NONE = 0x00000000
POINTER_FLAG_NEW = 0x00000001
POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_FIRSTBUTTON = 0x00000010
POINTER_FLAG_SECONDBUTTON = 0x00000020
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UPDATE = 0x00020000
POINTER_FLAG_UP = 0x00040000

# PEN_FLAGS
PEN_FLAG_NONE = 0x00000000
PEN_FLAG_BARREL = 0x00000001
PEN_FLAG_ERASER = 0x00000004

# PEN_MASK
PEN_MASK_NONE = 0x00000000
PEN_MASK_PRESSURE = 0x00000001
PEN_MASK_ROTATION = 0x00000002
PEN_MASK_TILT_X = 0x00000004
PEN_MASK_TILT_Y = 0x00000008

# Virtual desktop info (for absolute pixel coordinates)

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

# Injector

class DesktopPenInjector:
    """
    Maps S Pen input to synthetic Windows Pointer API events.

    Pen tip touching  = In contact
    Side button       = Barrel button
    Hover             = In range
    """

    def __init__(self, vx, vy, vw, vh, deadzone: float = 0.005):
        self.deadzone  = deadzone
        self.vx = vx
        self.vy = vy
        self.vw = vw
        self.vh = vh
        
        self._in_range = False
        self._is_down  = False
        self._frame    = 0
        
        self.swap_axes = False
        self.invert_x = False
        self.invert_y = False
        self.scale     = 1.0
        self.offset_x  = 0.0
        self.offset_y  = 0.0

        # Initialize the synthetic pen device
        self._device = user32.CreateSyntheticPointerDevice(PT_PEN, 1, POINTER_FEEDBACK_NONE)
        if not self._device:
            print("Failed to initialize Windows Synthetic Pointer Device")
            sys.exit(1)

    def __del__(self):
        if hasattr(self, '_device') and self._device:
            user32.DestroySyntheticPointerDevice(self._device)

    def cleanup(self):
        if self._in_range:
            self._send_pointer_event(self._last_x, self._last_y, 0, 0, 0, False, False, leaving_range=True)
        if self._device:
            user32.DestroySyntheticPointerDevice(self._device)
            self._device = None

    def _send_pointer_event(self, x, y, pressure, tilt_x, tilt_y, touching, side_btn, leaving_range=False):
        pen = POINTER_PEN_INFO()
        pen.pointerInfo.pointerType = PT_PEN
        pen.pointerInfo.pointerId = 0
        pen.pointerInfo.ptPixelLocation.x = int(x)
        pen.pointerInfo.ptPixelLocation.y = int(y)

        # Set masks and values
        pen.penMask = PEN_MASK_PRESSURE | PEN_MASK_TILT_X | PEN_MASK_TILT_Y
        pen.pressure = int(pressure * 1024)
        pen.tiltX = int(tilt_x)
        pen.tiltY = int(tilt_y)
        
        pen.penFlags = PEN_FLAG_BARREL if side_btn else PEN_FLAG_NONE

        flags = 0
        if not self._in_range and not leaving_range:
            flags |= POINTER_FLAG_NEW | POINTER_FLAG_INRANGE
            self._in_range = True
        elif leaving_range:
            flags |= POINTER_FLAG_UPDATE
            self._in_range = False
        else:
            flags |= POINTER_FLAG_INRANGE | POINTER_FLAG_UPDATE

        if not leaving_range:
            if touching and not self._is_down:
                flags |= POINTER_FLAG_INCONTACT | POINTER_FLAG_DOWN | POINTER_FLAG_FIRSTBUTTON
                self._is_down = True
            elif touching and self._is_down:
                flags |= POINTER_FLAG_INCONTACT | POINTER_FLAG_FIRSTBUTTON
            elif not touching and self._is_down:
                flags |= POINTER_FLAG_UP
                self._is_down = False

        pen.pointerInfo.pointerFlags = flags  

        info = POINTER_TYPE_INFO()
        info.type = PT_PEN
        info.u.penInfo = pen

        res = user32.InjectSyntheticPointerInput(self._device, ctypes.pointer(info), 1)
        if res == 0:
             pass

    def inject(self, state: PenState):
        nx, ny = state.x, state.y
        if self.swap_axes:
            nx, ny = ny, nx

        if self.scale != 1.0 or self.offset_x != 0.0 or self.offset_y != 0.0:
            nx = (nx - self.offset_x) * self.scale
            ny = (ny - self.offset_y) * self.scale
            nx = max(0.0, min(1.0, nx))
            ny = max(0.0, min(1.0, ny))

        if self.invert_x:
            nx = 1.0 - nx
        if self.invert_y:
            ny = 1.0 - ny

        # Map to virtual desktop pixels
        px = self.vx + (nx * self.vw)
        py = self.vy + (ny * self.vh)
        
        self._last_x = px
        self._last_y = py

        touching = state.touching and state.pressure > self.deadzone
        self._send_pointer_event(px, py, state.pressure, state.tilt_x, state.tilt_y, touching, state.button)

        self._frame += 1
        if self._frame % 30 == 0:
            action = "DOWN" if self._is_down else "HOVER"
            print(
                f"\r[{state.seq:6d}] {action:<5s}  "
                f"x={state.x:.3f}  y={state.y:.3f}  "
                f"p={state.pressure:.3f}  "
                f"tilt=({state.tilt_x:+.0f}°,{state.tilt_y:+.0f}°)  "
                f"LMB={self._is_down}  RMB={state.button} ",
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

    injector = DesktopPenInjector(vx, vy, vw, vh, deadzone=args.deadzone)
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
        injector.cleanup()
        print("\nStopped.")

if __name__ == "__main__":
    main()