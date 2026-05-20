"""
spen_listener.py
======================================
Listens for S Pen events from the spen_daemon.
"""

import socket
import struct
import select

from spen_adb import (
    PenState, AXIS_DEFAULTS,
    EV_SYN, EV_KEY, EV_ABS, SYN_REPORT,
    ABS_X, ABS_Y, ABS_PRESSURE, ABS_DISTANCE, ABS_TILT_X, ABS_TILT_Y,
    BTN_TOUCH, BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_STYLUS, BTN_STYLUS2,
)

PORT = 5005

# struct timeval { long tv_sec; long tv_usec; }
# unsigned short type, code; int value;
INPUT_EVENT = struct.Struct("qqHHi")

def listen_and_inject(injector_callback=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", PORT))

    print(f"Listening for S Pen events on UDP {PORT}...")

    state = PenState()
    seq = 0

    try:
        while True:
            ready = select.select([sock], [], [], 0.5)
            if not ready[0]:
                continue

            data, addr = sock.recvfrom(INPUT_EVENT.size)
            if len(data) != INPUT_EVENT.size:
                continue

            sec, usec, ev_type, ev_code, ev_value = INPUT_EVENT.unpack(data)

            if ev_type == EV_ABS:
                if   ev_code == ABS_X:        state.x_raw        = ev_value
                elif ev_code == ABS_Y:        state.y_raw        = ev_value
                elif ev_code == ABS_PRESSURE: state.pressure_raw = ev_value
                elif ev_code == ABS_TILT_X:   state.tilt_x_raw   = ev_value
                elif ev_code == ABS_TILT_Y:   state.tilt_y_raw   = ev_value
                elif ev_code == ABS_DISTANCE: state.distance_raw = ev_value

            elif ev_type == EV_KEY:
                if   ev_code == BTN_TOUCH:        state.touching = bool(ev_value)
                elif ev_code == BTN_TOOL_PEN:     state.pen_down = bool(ev_value)
                elif ev_code == BTN_TOOL_RUBBER:  state.eraser   = bool(ev_value)
                elif ev_code == BTN_STYLUS:       state.button   = bool(ev_value)
                elif ev_code == BTN_STYLUS2:      state.button2  = bool(ev_value)

            elif ev_type == EV_SYN and ev_code == SYN_REPORT:
                state.normalize(AXIS_DEFAULTS)
                state.timestamp = sec + (usec / 1000000.0)
                state.seq = seq
                seq += 1

                if injector_callback:
                    injector_callback(state)
                else:
                    if state.pen_down or state.touching or state.eraser:
                        print(state)
                    # Uncomment if you want to see pure hover events too:
                    # else:
                    #     print(f"[HOVER] x={state.x:.3f} y={state.y:.3f} dist={state.distance_raw}")
    except KeyboardInterrupt:
        print("\nStopped.")    
    finally:
        sock.close()

if __name__ == "__main__":
    listen_and_inject()