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
                            ->  6b 0d 05 <addr6> .. .. .. <rssi int16le>  (per found device)
        04 02 <addr6>       connect/pair to address
        07 ff               forget all paired devices
    cmd 0x09  device info
        02                  firmware version
        03                  serial number
    acks:  5a 02 0a <cmd> 00 <first payload byte>
"""

import argparse
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

VERBOSE = False


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
                         lambda c, p: c == CMD_BT and len(p) >= 2 and p[0] == 0x09 and p[1] == key)
        return None if p is None else (p[2] if len(p) > 2 else 0)

    def set(self, key, value):
        ack = self.request(CMD_BT, [0x0a, key, value],
                           lambda c, p: c == 0x02 and len(p) >= 3 and p[0] == CMD_BT and p[2] == 0x0a)
        return ack is not None

    def info(self, field):
        p = self.request(CMD_INFO, [field],
                         lambda c, p: c == CMD_INFO and len(p) >= 1 and p[0] == field)
        if p is None:
            return None
        s = p[1:].split(b"\0")[0]
        return s.lstrip(bytes(range(0x20))).decode("ascii", "replace")


# --- commands ------------------------------------------------------------

def onoff(v, on=1):
    return "unknown" if v is None else ("on" if v == on else "off")


def cmd_status(d, args):
    aptx = d.get(GET_APTX)
    hfp = d.get(GET_HFP)
    auto = d.get(GET_AUTO_HFP)
    if aptx in APTX_NAMES:
        mode = APTX_NAMES[aptx]
    elif aptx is None:
        mode = "unknown (no reply)"
    else:
        mode = f"not reported (0x{aptx:02x}; no aptX Adaptive headset connected?)"
    print(f"aptX Adaptive mode : {mode}")
    print(f"HFP (headset mic)  : {onoff(hfp, HFP_VALUES['on'])}")
    print(f"Auto HFP switching : {onoff(auto, AUTO_HFP_VALUES['on'])}")


def cmd_info(d, args):
    print(f"Firmware : {d.info(0x02) or 'unknown'}")
    print(f"Serial   : {d.info(0x03) or 'unknown'}")


def cmd_aptx(d, args):
    print(f"Enabling aptX Adaptive {APTX_NAMES[APTX[args.cmd]]} mode")
    if not d.set(SET_APTX, APTX[args.cmd]):
        print("warning: no acknowledgement from the dongle", file=sys.stderr)


def cmd_hfp(d, args):
    print(f"Turning HFP {args.state} (the dongle will reconnect and re-appear as a new audio device)")
    d.set(SET_HFP, HFP_VALUES[args.state])


def cmd_auto_hfp(d, args):
    print(f"Turning automatic HFP switching {args.state}")
    if not d.set(SET_AUTO_HFP, AUTO_HFP_VALUES[args.state]):
        print("warning: no acknowledgement from the dongle", file=sys.stderr)


def cmd_scan(d, args):
    print("Disconnecting the current headset and scanning"
          + (f" for {args.seconds:g}s" if args.seconds else "") + "...")
    print("Put your headset into pairing mode now.")
    d.drain()
    d.send(CMD_BT, [0x04, 0x01])
    if not args.seconds:
        return
    found = {}
    for cmd, p in d.listen(args.seconds):
        if cmd == CMD_BT and len(p) >= 12 and p[0] == 0x05:
            addr = fmt_addr(p[1:7])
            rssi = int.from_bytes(p[10:12], "little", signed=True)
            if addr not in found:
                print(f"  found {addr}  rssi {rssi} dBm")
            found[addr] = max(found.get(addr, -999), rssi)
    if found:
        print("\nPair with:  btw5-switch pair <address>")
    else:
        print("No devices found.")


def cmd_pair(d, args):
    addr = args.address
    print(f"Pairing with {fmt_addr(addr)} ...")
    d.drain()
    d.send(CMD_BT, [0x04, 0x02] + addr)
    for cmd, p in d.listen(args.timeout):
        if cmd == CMD_BT and len(p) >= 8 and p[0] == 0x06 and list(p[2:8]) == addr:
            if p[1] in (0x08, 0x0b):
                print("Connected.")
                return
            if p[1] == 0x0a:
                print("Headset disconnected.")
    print("No connection confirmed yet (it may still connect; check 'btw5-switch status' "
          "or try again with the headset in pairing mode).")


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

    sub.add_parser("status", help="show aptX, HFP and auto-HFP settings").set_defaults(fn=cmd_status)
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
    p = sub.add_parser("pair", help="pair/connect to a headset address from 'scan'")
    p.add_argument("address", type=parse_addr)
    p.add_argument("--timeout", type=float, default=15.0); p.set_defaults(fn=cmd_pair)
    p = sub.add_parser("forget", help="forget all paired headsets")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    p.set_defaults(fn=cmd_forget)

    p = sub.add_parser("raw", help="send a raw command, e.g. 'raw 6b 09 03' (for protocol exploration)")
    p.add_argument("bytes", nargs="+", type=lambda s: int(s, 16))
    p.add_argument("--listen", type=float, default=2.0, help="seconds to print replies")
    p.set_defaults(fn=cmd_raw)

    args = ap.parse_args()
    VERBOSE = args.verbose
    with BTW5() as d:
        args.fn(d, args)


if __name__ == "__main__":
    main()
