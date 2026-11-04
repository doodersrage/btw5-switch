"""btw5-switch command line interface."""

import argparse
import json
import shutil
import signal
import subprocess
import sys
import time

from . import __version__
from . import protocol as P
from .device import BTW5, REJECTED, BTW5Error, Gone, NotFound, read_status, wait_for_device


def emit_json(obj):
    print(json.dumps(obj, indent=2))


def report_set(status, what="command"):
    if status is None:
        print("warning: no acknowledgement from the dongle", file=sys.stderr)
    elif status == P.STATUS_REJECTED:
        sys.exit(f"The dongle rejected the {what} (status 0x80). Is a compatible headset connected?")
    elif status != P.STATUS_OK:
        sys.exit(f"The dongle rejected the {what} (status 0x{status:02x}).")


# --- read-only commands ----------------------------------------------------

def cmd_status(d, args):
    st = read_status(d)
    if args.json:
        return emit_json(st)
    if st["headset"]:
        headset = f"{st['headset']['name']} ({st['headset']['address']})"
    elif st["last_headset"]:
        h = st["last_headset"]
        headset = f"none connected (last: {h['name'] or h['address']}, {st['connection']})"
    else:
        headset = "none connected"
    aptx = st["aptx_mode"]
    mode = {"LL": "Low Latency", "HQ": "High Quality"}.get(aptx, "not available (no aptX Adaptive headset connected?)")

    def onoff(v):
        return "unknown" if v is None else ("on" if v else "off")
    print(f"Headset            : {headset}")
    print(f"aptX Adaptive mode : {mode}")
    print(f"HFP (headset mic)  : {onoff(st['hfp'])}")
    print(f"Auto HFP switching : {onoff(st['auto_hfp'])}")


def cmd_info(d, args):
    info = {"firmware": d.info_str(P.INFO_FIRMWARE), "serial": d.info_str(P.INFO_SERIAL)}
    if args.json:
        return emit_json(info)
    print(f"Firmware : {info['firmware'] or 'unknown'}")
    print(f"Serial   : {info['serial'] or 'unknown'}")


def cmd_dump(d, args):
    """Every readable register, for protocol exploration and bug reports."""
    def show(v):
        if v is None:
            return None
        if v == REJECTED:
            return "rejected"
        return P.hexs(v)

    settings = {f"{k:02x}": show(d.get_raw(k)) for k in P.GET_KEYS_ALL}
    info = {"00": show(d.info(0x00)), "01 00": show(d.info(0x01, [0x00])),
            "02": d.info_str(P.INFO_FIRMWARE), "03": d.info_str(P.INFO_SERIAL)}
    conn = d.connection()
    out = {
        "settings (6b 09 <key>)": settings,
        "info (09 <field>)": info,
        "connection (6b 06)": None if conn is None else
            {"state": f"{conn[0]:02x} ({P.conn_state_name(conn[0])})",
             "address": P.fmt_addr(conn[1]), "name": conn[2]},
        "pairing slots (6b 02)": [{"slot": s, "address": P.fmt_addr(a), "name": n}
                                  for s, a, n in d.slots()],
    }
    if args.json:
        return emit_json(out)
    for section, val in out.items():
        print(section)
        if isinstance(val, dict):
            for k, v in val.items():
                print(f"  {k:8} {v}")
        elif isinstance(val, list):
            for v in val:
                print(f"  {v['slot']}  " + (f"{v['address']}  {v['name']}" if v["address"] != "00:00:00:00:00:00" else "(empty)"))
        else:
            print(f"  {val}")


def cmd_paired(d, args):
    slots = d.slots()
    if not slots:
        sys.exit("Couldn't read the pairing slots.")
    conn = d.connection()
    current = conn[1] if conn and conn[0] == P.CONN_CONNECTED else None
    used = [(s, a, n) for s, a, n in slots if a != P.ZERO_ADDR]
    if args.names:
        for _, _, n in used:
            if n:
                print(n)
        return
    if args.json:
        return emit_json([{"slot": s, "address": P.fmt_addr(a), "name": n, "connected": a == current}
                          for s, a, n in used])
    for slot, addr, name in slots:
        if addr == P.ZERO_ADDR:
            print(f"  {slot}  (empty)")
        else:
            mark = "  [connected]" if addr == current else ""
            print(f"  {slot}  {P.fmt_addr(addr)}  {name}{mark}")


# --- settings --------------------------------------------------------------

def cmd_aptx(d, args):
    mode = args.cmd
    if mode == "toggle":
        cur = d.get(P.GET_APTX)
        if cur in (None, REJECTED):
            sys.exit("Can't read the current aptX mode (no aptX Adaptive headset connected?).")
        mode = "hq" if cur == P.APTX["ll"] else "ll"
    print(f"Enabling aptX Adaptive {P.APTX_NAMES[P.APTX[mode]]} mode")
    report_set(d.set(P.SET_APTX, P.APTX[mode]))


def cmd_hfp(d, args):
    print(f"Turning HFP {args.state} (the dongle will reconnect and re-appear as a new audio device)")
    report_set(d.set(P.SET_HFP, P.HFP_VALUES[args.state]))


def cmd_auto_hfp(d, args):
    print(f"Turning automatic HFP switching {args.state}")
    report_set(d.set(P.SET_AUTO_HFP, P.AUTO_HFP_VALUES[args.state]))


# --- Bluetooth -------------------------------------------------------------

def cmd_scan(d, args):
    if not args.json:
        print("Disconnecting the current headset and scanning"
              + (f" for {args.seconds:g}s" if args.seconds else "") + "...")
        if args.seconds:
            print("Put your headset into pairing mode now.")
    report_set(d.start_scan(), "scan")
    if not args.seconds:
        return
    found = {}
    connected = None
    for cmd, p in d.listen(args.seconds):
        if cmd != P.CMD_BT:
            continue
        r = P.parse_scan(p)
        if r:
            addr, name, rssi = r
            if addr not in found and not args.json:
                print(f"  {P.fmt_addr(addr)}  {rssi:4d} dBm  {name}")
            found[addr] = {"address": P.fmt_addr(addr), "name": name,
                           "rssi": max(rssi, found.get(addr, {}).get("rssi", -999))}
            continue
        # With a headset in pairing mode nearby, the dongle often connects to
        # it by itself and sends connection events instead of scan results.
        conn = P.parse_conn(p)
        if conn and conn[0] == P.CONN_CONNECTED:
            connected = {"address": P.fmt_addr(conn[1]), "name": conn[2]}
            if not args.json:
                print(f"The dongle connected to {P.headset_label(conn[1], conn[2])} by itself.")
            break
    if args.json:
        return emit_json({"found": list(found.values()), "connected": connected})
    if connected:
        return
    if found:
        print("\nConnect with:  btw5-switch pair <address>   (while the headset is still in pairing mode)")
    else:
        print("No devices found. (The dongle sometimes connects to a headset in pairing mode by "
              "itself; check 'btw5-switch status'.)")


def resolve_headset(d, target):
    """Turn an address or the name of a paired headset into an address."""
    try:
        return P.parse_addr(target)
    except ValueError:
        pass
    slots = [s for s in d.slots() if s[1] != P.ZERO_ADDR]
    exact = [s for s in slots if s[2].lower() == target.lower()]
    matches = exact or [s for s in slots if target.lower() in s[2].lower()]
    if len(matches) == 1:
        return matches[0][1]
    if not matches:
        sys.exit(f"'{target}' is neither an address nor the name of a paired headset "
                 "(see 'btw5-switch paired').")
    sys.exit(f"'{target}' matches several paired headsets: "
             + ", ".join(P.headset_label(a, n) for _, a, n in matches))


def cmd_pair(d, args):
    addr = resolve_headset(d, args.headset)
    print(f"Connecting to {P.fmt_addr(addr)} ...")
    status = d.connect(addr)
    if status == P.STATUS_REJECTED:
        sys.exit("The dongle refused to connect (status 0x80): it can't reach the headset.\n"
                 "Make sure the headset is on and not connected to another device, or put it\n"
                 "in pairing mode, run 'btw5-switch scan', then pair while it's still listed.")
    report_set(status, "connect request")
    for cmd, p in d.listen(args.timeout):
        conn = P.parse_conn(p) if cmd == P.CMD_BT else None
        if conn and conn[1] == addr:
            if conn[0] == P.CONN_CONNECTED:
                print(f"Connected to {P.headset_label(conn[1], conn[2])}.")
                return
            if conn[0] == P.CONN_DISCONNECTED:
                print("Headset disconnected.")
    sys.exit("No connection confirmed (check 'btw5-switch status', or try again with the headset "
             "in pairing mode).")


def cmd_disconnect(d, args):
    print("Disconnecting the current headset (this also starts a scan)")
    report_set(d.start_scan(), "disconnect")


def cmd_forget(d, args):
    if not args.yes:
        if not sys.stdin.isatty() or input("Forget ALL paired headsets? [y/N] ").strip().lower() != "y":
            sys.exit("Aborted.")
    print("Forgetting all paired devices (the dongle will reconnect)")
    d.forget_all()


def cmd_raw(d, args):
    cmd, payload = args.bytes[0], args.bytes[1:]
    d.drain()
    d.send(cmd, payload)
    for c, p in d.listen(args.listen):
        print(f"<< {P.hexs(p)}" if c is None else f"<< cmd {c:02x}: {P.hexs(p)}")


# --- long-running / integrations --------------------------------------------

def notify(summary, body=""):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "BT-W5", "-i", "audio-headphones", summary, body],
                       check=False)


def cmd_monitor(args):
    from .broker import Broker, SocketTransport, broker_available, socket_path
    from .device import UsbTransport

    def say(line):
        print(f"{time.strftime('%H:%M:%S')}  {line}", flush=True)

    tracker = P.ChangeTracker()

    def handle(c, p):
        if args.all:
            say(P.describe_event(c, p) or (f"cmd {c:02x}: {P.hexs(p)}" if c is not None else P.hexs(p)))
            return
        change = tracker.feed(c, p)
        if change:
            say(change[0])
            if args.notify and change[1]:
                notify(*change[1])

    def ask_state(send):
        for cmd, payload in P.STATE_QUERIES:
            send(P.build_report(cmd, payload))

    print("Watching dongle events (Ctrl-C to stop)...", file=sys.stderr)
    while True:
        try:
            if broker_available():
                # Someone else owns the dongle and shares it: just listen along.
                with BTW5(transport=SocketTransport(), verbose=args.verbose) as d:
                    ask_state(d.t.write)
                    while True:
                        if r := d.recv(1000):
                            handle(*r)
            else:
                t = UsbTransport(busy_wait=0)
                t.open()
                try:
                    with Broker(t) as b:
                        if args.verbose:
                            say(f"sharing the dongle on {socket_path()}")
                        ask_state(t.write)
                        b.run(lambda raw: handle(*P.split_report(raw)))
                finally:
                    t.close()
        except (Gone, NotFound):
            if not args.reconnect:
                raise Gone() from None
            say("dongle gone, waiting for it to come back...")
            tracker = P.ChangeTracker()
            wait_for_device()
            time.sleep(1)  # let it finish enumerating
            say("dongle back")


def waybar_state():
    """Status for a waybar/polybar custom module (JSON-able dict)."""
    try:
        with BTW5(transport=None) as d:
            st = read_status(d)
    except NotFound:
        return {"text": "", "class": "absent", "tooltip": "BT-W5 not plugged in"}
    except BTW5Error as e:
        return {"text": "BT-W5 ?", "class": "error", "tooltip": str(e)}
    h = st["headset"]
    mode = st["aptx_mode"]
    text = (h["name"] or h["address"]) if h else "no headset"
    if h and mode:
        text += f" · {mode}"
    tip = [f"Headset: {h['name']} ({h['address']})" if h else "Headset: none connected",
           f"aptX Adaptive: {mode or 'n/a'}",
           f"HFP: {'on' if st['hfp'] else 'off'}",
           f"Auto HFP: {'on' if st['auto_hfp'] else 'off'}"]
    return {"text": text, "alt": mode or "none", "class": "connected" if h else "disconnected",
            "tooltip": "\n".join(tip)}


def cmd_waybar(args):
    while True:
        print(json.dumps(waybar_state(), ensure_ascii=False), flush=True)
        if not args.interval:
            return
        time.sleep(args.interval)


def cmd_tray(args):
    from .tray import main as tray_main
    tray_main(interval=args.interval)


# --- argument parsing --------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="btw5-switch",
        description="Control the Creative BT-W5 Bluetooth audio dongle.",
        epilog="Volume is a standard USB Audio control: use your desktop's volume "
               "settings, wpctl or alsamixer.")
    ap.add_argument("-v", "--verbose", action="store_true", help="hex-dump USB traffic to stderr")
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(name, fn, help, json_opt=False, device=True):
        p = sub.add_parser(name, help=help, description=help)
        p.set_defaults(fn=fn, device=device)
        if json_opt:
            p.add_argument("--json", action="store_true", help="machine-readable output")
        return p

    add("status", cmd_status, "show connected headset, aptX, HFP and auto-HFP settings", True)
    add("info", cmd_info, "show firmware version and serial number", True)
    add("dump", cmd_dump, "show every readable register (for protocol exploration and bug reports)", True)
    add("ll", cmd_aptx, "aptX Adaptive Low Latency mode")
    add("hq", cmd_aptx, "aptX Adaptive High Quality mode")
    add("toggle", cmd_aptx, "switch between aptX Adaptive Low Latency and High Quality")
    p = add("hfp", cmd_hfp, "enable/disable HFP (headset microphone)")
    p.add_argument("state", choices=["on", "off"])
    p = add("auto-hfp", cmd_auto_hfp, "enable/disable automatic HFP switching")
    p.add_argument("state", choices=["on", "off"])

    p = add("scan", cmd_scan, "disconnect headset and list nearby headsets", True)
    p.add_argument("seconds", nargs="?", type=float, default=15.0)
    add("disconnect", cmd_disconnect, "disconnect the current headset")
    p = add("pair", cmd_pair, "connect to a headset: an address from 'scan' or a name from 'paired'")
    p.add_argument("headset", help="address (68:00:1a:bc:5c:eb) or (part of) a paired headset's name")
    p.add_argument("--timeout", type=float, default=15.0)
    p = add("paired", cmd_paired, "list the remembered headsets (4 slots)", True)
    p.add_argument("--names", action="store_true", help="only print the names, one per line")
    p = add("forget", cmd_forget, "forget all paired headsets")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")

    p = add("monitor", cmd_monitor, "print connection and setting events as they happen", device=False)
    p.add_argument("--notify", action="store_true", help="also show desktop notifications for changes")
    p.add_argument("--all", action="store_true",
                   help="print every report, including replies to other programs' queries")
    p.add_argument("--no-reconnect", dest="reconnect", action="store_false",
                   help="exit when the dongle disappears instead of waiting for it")
    p = add("waybar", cmd_waybar, "print status as JSON for a waybar/polybar custom module", device=False)
    p.add_argument("--interval", type=float, default=0,
                   help="keep printing every N seconds (default: print once and exit)")
    p = add("tray", cmd_tray, "run a system tray icon (needs PyQt6 or PySide6)", device=False)
    p.add_argument("--interval", type=float, default=15, help="seconds between status refreshes")

    p = add("raw", cmd_raw, "send a raw command, e.g. 'raw 6b 09 03' (for protocol exploration)")
    p.add_argument("bytes", nargs="+", type=lambda s: int(s, 16))
    p.add_argument("--listen", type=float, default=2.0, help="seconds to print replies")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    # exit cleanly on SIGTERM so the HID driver gets reattached
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        if not args.device:
            args.fn(args)
        else:
            with BTW5(verbose=args.verbose) as d:
                args.fn(d, args)
    except BTW5Error as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        sys.exit(130)
