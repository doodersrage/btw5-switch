#!/usr/bin/env python3
"""
btw5-switch - control the Creative BT-W5 Bluetooth audio dongle on Linux.

Reverse engineered from the traffic between Creative's Windows app and the
BT-W5 (see bt-w5.pcapng in this repo). Requires PyUSB and write access to the
dongle's USB device: run with sudo, or install 70-btw5-switch.rules.

Protocol summary (HID output report 3, sent as a SET_REPORT control transfer
to interface 0; replies arrive as input report 3 on interrupt endpoint 0x81):

    03 5a <cmd> <len> <payload[len]>   (zero-padded to 65 bytes)

    cmd 0x6b  settings / Bluetooth
        0a <key> <val>      set setting      (key 01 HFP, 02 auto-HFP, 03 aptX mode)
        09 <key>            get setting  ->  6b 03 09 <key> <val>
        04 01               disconnect headset and start scanning
                            ->  6b <n> 05 <addr6> <nlen> <name[nlen]> <rssi int16le> ..
        04 02 <addr6>       connect/pair to address
        06                  query connection -> 6b 29 06 <state> <addr6> <32> <name[32]>
                            (the same frame is also sent unprompted on (dis)connect)
        02 <slot>           read pairing slot 0-3
                            ->  6b 2d 02 <slot> <80=valid> <addr6> <31> <name[31]> ..
        07 ff               forget all paired devices
    cmd 0x09  device info
        02                  firmware version
        03                  serial number
    acks:  5a 02 0a <cmd> 00 <first payload byte>
"""

import argparse
import signal
import sys
import time

try:
    import usb.core
    import usb.util
except ImportError:
    sys.exit("PyUSB not found. Install it with:\n"
             "  Arch Linux:    sudo pacman -S python-pyusb\n"
             "  Debian/Ubuntu: sudo apt install python3-usb")

VENDOR_ID = 0x041e   # Creative Technology
PRODUCT_ID = 0x3130  # BT-W5
HID_INTERFACE = 0
EP_IN = 0x81
REPORT_ID = 0x03
MAGIC = 0x5a
REPORT_LEN = 65

CMD_BT = 0x6b
CMD_INFO = 0x09

# setting keys used with "set" (0a) ...
SET_HFP, SET_AUTO_HFP, SET_APTX = 0x01, 0x02, 0x03
# ... and the keys that read the current state back (09)
# (key 08 also echoes 20/40 but didn't track the configured mode reliably in the capture)
GET_HFP, GET_AUTO_HFP, GET_APTX = 0x06, 0x07, 0x03

APTX = {"ll": 0x20, "hq": 0x40}
APTX_NAMES = {0x20: "Low Latency", 0x40: "High Quality"}
HFP_VALUES = {"on": 0x01, "off": 0x02}
AUTO_HFP_VALUES = {"on": 0x01, "off": 0x00}

# connection states in 6b 06 frames
CONN_STATES = {0x06: "disconnecting", 0x08: "connecting", 0x0a: "disconnected", 0x0b: "connected"}
PAIR_SLOTS = 4
ZERO_ADDR = bytes(6)

VERBOSE = False
REJECTED = "rejected"  # get() result when the dongle refuses a query (ack status != 0)


def is_ack(cmd, payload, sub):
    """Ack frame: 5a 02 <len> 6b <status> <sub ...>; status 00 = OK, 80 seen for 'not available'."""
    return cmd == 0x02 and len(payload) >= 3 and payload[0] == CMD_BT and payload[2] == sub


def hexs(b):
    return " ".join(f"{x:02x}" for x in b)


def parse_addr(s):
    parts = s.replace("-", ":").split(":")
    if len(parts) != 6:
        raise argparse.ArgumentTypeError("address must look like 68:00:1a:bc:5c:eb")
    try:
        return [int(p, 16) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError("address must be 6 hex bytes")


def fmt_addr(b):
    return ":".join(f"{x:02x}" for x in b)


def cstr(b):
    return bytes(b).split(b"\0")[0].decode("utf-8", "replace")


def parse_conn(p):
    """6b 06 frame -> (state, addr, name) or None."""
    if len(p) < 9 or p[0] != 0x06:
        return None
    return p[1], bytes(p[2:8]), cstr(p[9:9 + p[8]])


def parse_scan(p):
    """6b 05 frame -> (addr, name, rssi) or None."""
    if len(p) < 8 or p[0] != 0x05:
        return None
    n = p[7]
    if len(p) < 10 + n:
        return None
    return bytes(p[1:7]), cstr(p[8:8 + n]), int.from_bytes(p[8 + n:10 + n], "little", signed=True)


def parse_slot(p):
    """6b 02 frame -> (slot, valid, addr, name) or None."""
    if len(p) < 10 or p[0] != 0x02:
        return None
    return p[1], bool(p[2] & 0x80), bytes(p[3:9]), cstr(p[10:10 + p[9]])


def headset_label(addr, name):
    return f"{name} ({fmt_addr(addr)})" if name else fmt_addr(addr)


class BTW5:
    def __init__(self):
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.dev is None:
            sys.exit("Creative BT-W5 not found. Is it plugged in? (check with: lsusb -d 041e:3130)")
        self.reattach = False

    def __enter__(self):
        try:
            if self.dev.is_kernel_driver_active(HID_INTERFACE):
                self.dev.detach_kernel_driver(HID_INTERFACE)
                self.reattach = True
            usb.util.claim_interface(self.dev, HID_INTERFACE)
        except usb.core.USBError as e:
            if e.errno == 13:
                sys.exit("Permission denied accessing the BT-W5.\n"
                         "Run with sudo, or install the udev rule (see README) and re-plug the dongle.")
            if e.errno == 16:
                sys.exit("The BT-W5 is in use by another program (is 'btw5-switch monitor' running?).")
            raise
        return self

    def __exit__(self, *exc):
        # The dongle re-enumerates after some commands (HFP toggle, forget), so
        # failures while releasing are expected and harmless.
        try:
            usb.util.release_interface(self.dev, HID_INTERFACE)
        except usb.core.USBError:
            pass
        usb.util.dispose_resources(self.dev)
        if self.reattach:
            try:
                self.dev.attach_kernel_driver(HID_INTERFACE)
            except usb.core.USBError:
                pass
        return False

    # --- low level -------------------------------------------------------

    def send(self, cmd, payload):
        data = [REPORT_ID, MAGIC, cmd, len(payload)] + list(payload)
        data += [0x00] * (REPORT_LEN - len(data))
        if VERBOSE:
            print(f">> {hexs(data[:4 + len(payload)])}", file=sys.stderr)
        self.dev.ctrl_transfer(0x21, 0x09, wValue=0x0200 | REPORT_ID,
                               wIndex=HID_INTERFACE, data_or_wLength=data)

    def recv(self, timeout_ms):
        """Return (cmd, payload) of the next input report, or None on timeout."""
        try:
            raw = bytes(self.dev.read(EP_IN, REPORT_LEN, timeout=timeout_ms))
        except usb.core.USBError as e:
            if e.errno in (110, None) or "timed out" in str(e).lower():
                return None
            raise
        if VERBOSE:
            print(f"<< {hexs(raw.rstrip(b'\0'))}", file=sys.stderr)
        if len(raw) < 4 or raw[0] != REPORT_ID:
            return (None, raw)
        if raw[1] != MAGIC:
            return (raw[1], raw[2:])  # other report family (e.g. 03 df ...)
        n = raw[3]
        return (raw[2], raw[4:4 + n].ljust(n, b"\0"))

    def drain(self):
        while self.recv(30) is not None:
            pass

    def request(self, cmd, payload, match, timeout=1.5):
        """Send a command and wait for the first reply where match(cmd, payload) is true."""
        self.drain()
        self.send(cmd, payload)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self.recv(max(1, int((deadline - time.monotonic()) * 1000)))
            if r and r[0] is not None and match(*r):
                return r[1]
        return None

    def listen(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            r = self.recv(max(1, int((deadline - time.monotonic()) * 1000)))
            if r:
                yield r

    # --- settings --------------------------------------------------------

    def get(self, key):
        p = self.request(CMD_BT, [0x09, key],
                         lambda c, p: (c == CMD_BT and len(p) >= 2 and p[0] == 0x09 and p[1] == key)
                         or (is_ack(c, p, 0x09) and p[1] != 0))
        if p is None:
            return None
        if p[0] == CMD_BT and p[1] != 0:   # dongle answered with a rejecting ack instead of a value
            return REJECTED
        return p[2] if len(p) > 2 else 0

    def set(self, key, value):
        """Return the ack status (0 = accepted), or None if no ack arrived."""
        ack = self.request(CMD_BT, [0x0a, key, value], lambda c, p: is_ack(c, p, 0x0a))
        return None if ack is None else ack[1]

    def info(self, field):
        p = self.request(CMD_INFO, [field],
                         lambda c, p: c == CMD_INFO and len(p) >= 1 and p[0] == field)
        if p is None:
            return None
        s = p[1:].split(b"\0")[0]
        return s.lstrip(bytes(range(0x20))).decode("ascii", "replace")

    def connection(self, timeout=1.0):
        """Return (state, addr, name) of the headset link, or None if the dongle didn't say.

        The dongle answers with one or more 6b 06 frames (e.g. connecting, then
        connected); the last one is the current state."""
        self.drain()
        self.send(CMD_BT, [0x06])
        last = None
        for c, p in self.listen(timeout):
            conn = parse_conn(p) if c == CMD_BT else None
            if conn:
                last = conn
                if conn[0] in (0x0a, 0x0b):
                    break
        return last

    def slots(self):
        """Return the pairing slots as a list of (slot, addr, name); empty slots have addr 00:..:00."""
        out = []
        for i in range(PAIR_SLOTS):
            p = self.request(CMD_BT, [0x02, i],
                             lambda c, p: c == CMD_BT and len(p) >= 2 and p[0] == 0x02 and p[1] == i)
            s = parse_slot(p) if p is not None else None
            if s is None or not s[1]:
                break
            out.append((s[0], s[2], s[3]))
        return out


# --- commands ------------------------------------------------------------

def onoff(v, on=1):
    if v is None:
        return "unknown (no reply)"
    if v == REJECTED:
        return "not available right now"
    return "on" if v == on else "off"


def report_set(status):
    if status is None:
        print("warning: no acknowledgement from the dongle", file=sys.stderr)
    elif status != 0:
        sys.exit(f"The dongle rejected the command (status 0x{status:02x}). "
                 "Is a compatible headset connected?")


def cmd_status(d, args):
    aptx = d.get(GET_APTX)
    hfp = d.get(GET_HFP)
    auto = d.get(GET_AUTO_HFP)
    if aptx in APTX_NAMES:
        mode = APTX_NAMES[aptx]
    elif aptx is None:
        mode = "unknown (no reply)"
    elif aptx == REJECTED:
        mode = "not available (no aptX Adaptive headset connected?)"
    else:
        mode = f"not reported (0x{aptx:02x}; no aptX Adaptive headset connected?)"
    conn = d.connection()
    if conn is None:
        headset = "unknown (no reply)"
    elif conn[0] == 0x0b:
        headset = headset_label(conn[1], conn[2])
    elif conn[0] == 0x0a:
        headset = "none connected"
    else:
        headset = f"{headset_label(conn[1], conn[2])}, {CONN_STATES.get(conn[0], f'state 0x{conn[0]:02x}')}"
    print(f"Headset            : {headset}")
    print(f"aptX Adaptive mode : {mode}")
    print(f"HFP (headset mic)  : {onoff(hfp, HFP_VALUES['on'])}")
    print(f"Auto HFP switching : {onoff(auto, AUTO_HFP_VALUES['on'])}")


def cmd_info(d, args):
    print(f"Firmware : {d.info(0x02) or 'unknown'}")
    print(f"Serial   : {d.info(0x03) or 'unknown'}")


def cmd_aptx(d, args):
    print(f"Enabling aptX Adaptive {APTX_NAMES[APTX[args.cmd]]} mode")
    report_set(d.set(SET_APTX, APTX[args.cmd]))


def cmd_hfp(d, args):
    print(f"Turning HFP {args.state} (the dongle will reconnect and re-appear as a new audio device)")
    report_set(d.set(SET_HFP, HFP_VALUES[args.state]))


def cmd_auto_hfp(d, args):
    print(f"Turning automatic HFP switching {args.state}")
    report_set(d.set(SET_AUTO_HFP, AUTO_HFP_VALUES[args.state]))


def cmd_scan(d, args):
    print("Disconnecting the current headset and scanning"
          + (f" for {args.seconds:g}s" if args.seconds else "") + "...")
    print("Put your headset into pairing mode now.")
    d.drain()
    d.send(CMD_BT, [0x04, 0x01])
    if not args.seconds:
        return
    found = set()
    for cmd, p in d.listen(args.seconds):
        r = parse_scan(p) if cmd == CMD_BT else None
        if r and r[0] not in found:
            found.add(r[0])
            print(f"  {fmt_addr(r[0])}  {r[2]:4d} dBm  {r[1]}")
    if found:
        print("\nPair with:  btw5-switch pair <address>")
    else:
        print("No devices found.")


def resolve_headset(d, target):
    """Turn an address or the name of a paired headset into an address."""
    try:
        return bytes(parse_addr(target))
    except argparse.ArgumentTypeError:
        pass
    slots = [s for s in d.slots() if s[1] != ZERO_ADDR]
    exact = [s for s in slots if s[2].lower() == target.lower()]
    matches = exact or [s for s in slots if target.lower() in s[2].lower()]
    if len(matches) == 1:
        return matches[0][1]
    if not matches:
        sys.exit(f"'{target}' is neither an address nor the name of a paired headset "
                 "(see 'btw5-switch paired').")
    sys.exit(f"'{target}' matches several paired headsets: "
             + ", ".join(headset_label(s[1], s[2]) for s in matches))


def cmd_pair(d, args):
    addr = resolve_headset(d, args.headset)
    print(f"Connecting to {fmt_addr(addr)} ...")
    d.drain()
    d.send(CMD_BT, [0x04, 0x02] + list(addr))
    for cmd, p in d.listen(args.timeout):
        conn = parse_conn(p) if cmd == CMD_BT else None
        if conn and conn[1] == addr:
            if conn[0] == 0x0b:
                print(f"Connected to {headset_label(conn[1], conn[2])}.")
                return
            if conn[0] == 0x0a:
                print("Headset disconnected.")
    print("No connection confirmed yet (it may still connect; check 'btw5-switch status' "
          "or try again with the headset in pairing mode).")


def cmd_paired(d, args):
    slots = d.slots()
    if not slots:
        sys.exit("Couldn't read the pairing slots.")
    conn = d.connection()
    current = conn[1] if conn and conn[0] == 0x0b else None
    for slot, addr, name in slots:
        if addr == ZERO_ADDR:
            print(f"  {slot}  (empty)")
        else:
            mark = "  [connected]" if addr == current else ""
            print(f"  {slot}  {fmt_addr(addr)}  {name}{mark}")


SETTING_NAMES = {GET_APTX: "aptX Adaptive mode", 0x08: "aptX Adaptive mode (active)",
                 GET_HFP: "HFP", GET_AUTO_HFP: "auto HFP"}


def describe_event(c, p):
    """Human-readable line for an unsolicited input report, or None to show it raw."""
    if c != CMD_BT or not p:
        return None
    conn = parse_conn(p)
    if conn:
        return f"headset {CONN_STATES.get(conn[0], f'state 0x{conn[0]:02x}')}: {headset_label(conn[1], conn[2])}"
    r = parse_scan(p)
    if r:
        return f"scan result: {fmt_addr(r[0])} {r[2]} dBm {r[1]}"
    if p[0] == 0x09 and len(p) >= 3 and p[1] in SETTING_NAMES:
        key, v = p[1], p[2]
        if key in (GET_APTX, 0x08):
            val = APTX_NAMES.get(v, f"0x{v:02x}")
        elif key == GET_HFP:
            val = onoff(v, HFP_VALUES["on"])
        else:
            val = onoff(v, AUTO_HFP_VALUES["on"])
        return f"{SETTING_NAMES[key]}: {val}"
    return None


def cmd_monitor(d, args):
    print("Watching dongle events (Ctrl-C to stop)...", file=sys.stderr)
    try:
        while True:
            r = d.recv(1000)
            if r is None:
                continue
            c, p = r
            line = describe_event(c, p) or (f"cmd {c:02x}: {hexs(p)}" if c is not None else hexs(p))
            print(f"{time.strftime('%H:%M:%S')}  {line}", flush=True)
    except KeyboardInterrupt:
        pass
    except usb.core.USBError as e:
        if e.errno == 19:
            sys.exit("The dongle disconnected (it re-enumerates after HFP changes and 'forget').")
        raise


def cmd_disconnect(d, args):
    args.seconds = 0
    cmd_scan(d, args)


def cmd_forget(d, args):
    if not args.yes:
        if not sys.stdin.isatty() or input("Forget ALL paired headsets? [y/N] ").strip().lower() != "y":
            sys.exit("Aborted.")
    print("Forgetting all paired devices (the dongle will reconnect)")
    d.send(CMD_BT, [0x07, 0xff])


def cmd_raw(d, args):
    cmd, payload = args.bytes[0], args.bytes[1:]
    d.drain()
    d.send(cmd, payload)
    for c, p in d.listen(args.listen):
        if c is None:
            print(f"<< {hexs(p)}")
        else:
            print(f"<< cmd {c:02x}: {hexs(p)}")


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(
        prog="btw5-switch",
        description="Control the Creative BT-W5 Bluetooth audio dongle.",
        epilog="Volume is a standard USB Audio control: use your desktop's volume "
               "settings, wpctl or alsamixer.")
    ap.add_argument("-v", "--verbose", action="store_true", help="hex-dump USB traffic to stderr")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    sub.add_parser("status", help="show connected headset, aptX, HFP and auto-HFP settings").set_defaults(fn=cmd_status)
    sub.add_parser("info", help="show firmware version and serial number").set_defaults(fn=cmd_info)
    sub.add_parser("ll", help="aptX Adaptive Low Latency mode").set_defaults(fn=cmd_aptx)
    sub.add_parser("hq", help="aptX Adaptive High Quality mode").set_defaults(fn=cmd_aptx)

    p = sub.add_parser("hfp", help="enable/disable HFP (headset microphone)")
    p.add_argument("state", choices=["on", "off"]); p.set_defaults(fn=cmd_hfp)
    p = sub.add_parser("auto-hfp", help="enable/disable automatic HFP switching")
    p.add_argument("state", choices=["on", "off"]); p.set_defaults(fn=cmd_auto_hfp)

    p = sub.add_parser("scan", help="disconnect headset and list nearby headsets")
    p.add_argument("seconds", nargs="?", type=float, default=15.0); p.set_defaults(fn=cmd_scan)
    sub.add_parser("disconnect", help="disconnect the current headset").set_defaults(fn=cmd_disconnect)
    p = sub.add_parser("pair", help="connect to a headset: an address from 'scan' or a name from 'paired'")
    p.add_argument("headset", help="address (68:00:1a:bc:5c:eb) or paired headset name")
    p.add_argument("--timeout", type=float, default=15.0); p.set_defaults(fn=cmd_pair)
    sub.add_parser("paired", help="list the remembered headsets (4 slots)").set_defaults(fn=cmd_paired)
    sub.add_parser("monitor", help="print connection and setting events as they happen").set_defaults(fn=cmd_monitor)
    p = sub.add_parser("forget", help="forget all paired headsets")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    p.set_defaults(fn=cmd_forget)

    p = sub.add_parser("raw", help="send a raw command, e.g. 'raw 6b 09 03' (for protocol exploration)")
    p.add_argument("bytes", nargs="+", type=lambda s: int(s, 16))
    p.add_argument("--listen", type=float, default=2.0, help="seconds to print replies")
    p.set_defaults(fn=cmd_raw)

    args = ap.parse_args()
    VERBOSE = args.verbose
    # exit cleanly on SIGTERM so the HID driver gets reattached
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    with BTW5() as d:
        args.fn(d, args)


if __name__ == "__main__":
    main()
