"""btwctl command line interface."""

import argparse
import errno
import json
import os
import shutil
import signal
import subprocess
import sys
import time

from . import __version__
from . import protocol as P
from .device import (BTW5, REJECTED, BTW5Error, Busy, Gone, NotFound, PermissionDenied,
                     read_dump, read_status, wait_for_device)


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

    print(f"Headset            : {headset}")
    print(f"aptX Adaptive mode : {mode}")
    print(f"HFP (headset mic)  : {onoff_unknown(st['hfp'])}")
    print(f"Auto HFP switching : {onoff_unknown(st['auto_hfp'])}")


def cmd_info(d, args):
    from .device import dongle_info
    dongle = dongle_info()
    info = {"model": dongle and dongle["model"], "usb_id": dongle and dongle["usb_id"],
            "firmware": d.info_str(P.INFO_FIRMWARE), "serial": d.info_str(P.INFO_SERIAL)}
    if args.json:
        return emit_json(info)
    if dongle:
        note = "" if dongle["tested"] else "  (reported to work by users; please report what works)"
        print(f"Model    : {dongle['model']} ({dongle['usb_id']}){note}")
    print(f"Firmware : {info['firmware'] or 'unknown'}")
    print(f"Serial   : {info['serial'] or 'unknown'}")


def cmd_dump(d, args):
    """Every readable register, for protocol exploration and bug reports."""
    out = read_dump(d)
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
        cur = d.get_aptx()
        if cur in (None, REJECTED):
            sys.exit("Can't read the current aptX mode (no aptX Adaptive headset connected?).")
        mode = "hq" if cur == P.APTX["ll"] else "ll"
    print(f"Enabling aptX Adaptive {P.APTX_NAMES[P.APTX[mode]]} mode")
    report_set(d.set_aptx(P.APTX[mode]))


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
    if not args.seconds:
        report_set(d.start_scan(), "scan")
        if args.json:
            emit_json({"found": [], "connected": None})
        return
    found = {}
    connected = None
    for ev in d.scan_events(args.seconds):
        if ev[0] == "found":
            _, addr, name, rssi = ev
            if addr not in found and not args.json:
                print(f"  {P.fmt_addr(addr)}  {rssi:4d} dBm  {name}")
            found[addr] = {"address": P.fmt_addr(addr), "name": name,
                           "rssi": max(rssi, found.get(addr, {}).get("rssi", -999))}
        else:
            _, addr, name = ev
            connected = {"address": P.fmt_addr(addr), "name": name}
            if not args.json:
                print(f"The dongle connected to {P.headset_label(addr, name)} by itself.")
    if args.json:
        return emit_json({"found": list(found.values()), "connected": connected})
    if connected:
        return
    if found:
        print("\nConnect with:  btwctl pair <address>   (while the headset is still in pairing mode)")
    else:
        print("No devices found. (The dongle sometimes connects to a headset in pairing mode by "
              "itself; check 'btwctl status'.)")


def resolve_headset(d, target):
    """Turn an address or the name of a paired headset into an address."""
    try:
        return P.parse_addr(target)
    except ValueError:
        pass
    slots = d.slots()
    if not slots:
        sys.exit("Couldn't read the pairing slots to look up that name; try again or use the address.")
    slots = [s for s in slots if s[1] != P.ZERO_ADDR]
    exact = [s for s in slots if s[2].lower() == target.lower()]
    matches = exact or [s for s in slots if target.lower() in s[2].lower()]
    if len(matches) == 1:
        return matches[0][1]
    if not matches:
        sys.exit(f"'{target}' is neither an address nor the name of a paired headset "
                 "(see 'btwctl paired').")
    sys.exit(f"'{target}' matches several paired headsets: "
             + ", ".join(P.headset_label(a, n) for _, a, n in matches))


def cmd_pair(d, args):
    addr = resolve_headset(d, args.headset)
    print(f"Connecting to {P.fmt_addr(addr)} ...")
    result, name = d.connect_and_wait(addr, args.timeout)
    if result == "connected":
        print(f"Connected to {P.headset_label(addr, name)}.")
        return
    if result == "rejected":
        sys.exit("The dongle refused to connect (status 0x80): it can't reach the headset.\n"
                 "Make sure the headset is on and not connected to another device, or put it\n"
                 "in pairing mode, run 'btwctl scan', then pair while it's still listed.")
    if result == "disconnected":
        print("Headset disconnected.")
    sys.exit("No connection confirmed (check 'btwctl status', or try again with the headset "
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


def hex_byte(s):
    try:
        v = int(s, 16)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{s}' is not a hex byte") from None
    if not 0 <= v <= 0xff:
        raise argparse.ArgumentTypeError(f"'{s}' is out of range (00-ff)")
    return v


MAX_PAYLOAD = P.REPORT_LEN - 4


def cmd_raw(d, args):
    cmd, payload = args.bytes[0], args.bytes[1:]
    if len(payload) > MAX_PAYLOAD:
        sys.exit(f"At most {MAX_PAYLOAD} payload bytes fit in a report.")
    d.drain()
    d.send(cmd, payload)
    for c, p in d.listen(args.listen):
        print(f"<< {P.hexs(p)}" if c is None else f"<< cmd {c:02x}: {P.hexs(p)}")


# --- long-running / integrations --------------------------------------------

def notify(summary, body=""):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "BT-W Control", "-i", "audio-headphones", summary, body],
                       check=False)


def cmd_monitor(args):
    from .broker import Broker, SocketTransport, broker_available, socket_path
    from .device import UsbTransport

    def say(line):
        print(f"{time.strftime('%H:%M:%S')}  {line}", flush=True)

    tracker = P.ChangeTracker()
    # One Automation for the whole run (it remembers game mode across dongle restarts);
    # "on" is set only while we own the dongle, so two monitors never both act.
    auto = {"obj": None, "on": None}

    def handle(c, p):
        if args.all:
            say(P.describe_event(c, p) or (f"cmd {c:02x}: {P.hexs(p)}" if c is not None else P.hexs(p)))
        change = tracker.feed(c, p)
        if auto["on"]:
            auto["on"].feed(c, p)
        if args.all:
            return
        if change:
            say(change[0])
            if args.notify and change[1]:
                notify(*change[1])

    def ask_state(send):
        from .device import aptx_key
        key = aptx_key()
        for cmd, payload in P.STATE_QUERIES:
            if payload == [P.BT_GET, P.GET_APTX]:
                payload = [P.BT_GET, key]
            send(P.build_report(cmd, payload))

    def start_automation(t):
        """Only the process that owns the dongle automates, so two monitors never both act."""
        if not args.automation:
            return
        from .automation import Automation
        from .config import Config

        def send(cmd, payload):
            t.write(P.build_report(cmd, payload))
        if auto["obj"] is None:
            auto["obj"] = Automation(Config(), tracker, send=send, say=say,
                                     notify=notify if args.notify else None)
        auto["obj"].send = send
        from .device import aptx_key
        auto["obj"].aptx_key = aptx_key()  # per (re)connect: the dongle may have been swapped
        auto["on"] = auto["obj"]

    def tick():
        if auto["on"]:
            auto["on"].tick()

    def own(t, path):
        """We hold the dongle: share it (if we can) and watch it until it goes away."""
        start_automation(t)
        try:
            b = Broker(t, path)
            b.__enter__()
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                return  # another monitor won the race; listen along next round
            say(f"not sharing the dongle ({e.strerror}); other btwctl commands will report it busy")
            ask_state(t.write)
            while True:
                if raw := t.read(500):
                    handle(*P.split_report(raw))
                tick()
        # (unreachable: the loop above only ends with an exception)
        try:
            if args.verbose:
                say(f"sharing the dongle on {path}")
            ask_state(t.write)
            b.run(lambda raw: handle(*P.split_report(raw)), on_idle=tick)
        finally:
            auto["on"] = None
            b.__exit__(None, None, None)

    print("Watching dongle events (Ctrl-C to stop)...", file=sys.stderr)
    away = False   # True while waiting for the dongle to come back
    while True:
        try:
            path = socket_path()
            if path and broker_available(path):
                # Someone else owns the dongle and shares it: just listen along.
                auto["on"] = None
                with BTW5(transport=SocketTransport(path), verbose=args.verbose) as d:
                    if away:
                        say("dongle back")
                        away = False
                    ask_state(d.t.write)
                    while True:
                        if r := d.recv(1000):
                            handle(*r)
            else:
                t = UsbTransport()
                t.open()
                try:
                    if away:
                        say("dongle back")
                        away = False
                    own(t, path)
                finally:
                    t.close()
        except (Busy, PermissionDenied):
            # Right after re-enumeration another program may grab the dongle
            # first, or logind may not have granted us access yet: retry.
            if not away:
                raise
            time.sleep(1)
        except (Gone, NotFound):
            if auto["obj"]:
                auto["obj"].dongle_gone()
            if not args.reconnect:
                raise
            # Keep the tracked state: HFP changes happen exactly here (the dongle
            # re-enumerates), and they should still be reported as changes.
            if not away:
                say("dongle gone, waiting for it to come back...")
                away = True
            wait_for_device()
            time.sleep(1)  # let it finish enumerating


def onoff_unknown(v):
    return "unknown" if v is None else ("on" if v else "off")


def waybar_state():
    """Status for a waybar/polybar custom module (JSON-able dict)."""
    try:
        with BTW5(transport=None) as d:
            st = read_status(d)
    except NotFound:
        return {"text": "", "class": "absent", "tooltip": "No BT-W dongle plugged in"}
    except BTW5Error as e:
        return {"text": "BT-W ?", "class": "error", "tooltip": str(e)}
    h = st["headset"]
    mode = st["aptx_mode"]
    text = (h["name"] or h["address"]) if h else "no headset"
    if h and mode:
        text += f" · {mode}"
    tip = [f"Headset: {h['name']} ({h['address']})" if h else "Headset: none connected",
           f"aptX Adaptive: {mode or 'n/a'}",
           f"HFP: {onoff_unknown(st['hfp'])}",
           f"Auto HFP: {onoff_unknown(st['auto_hfp'])}"]
    return {"text": text, "alt": mode or "none", "class": "connected" if h else "disconnected",
            "tooltip": "\n".join(tip)}


def cmd_waybar(args):
    while True:
        print(json.dumps(waybar_state(), ensure_ascii=False), flush=True)
        if not args.interval:
            return
        time.sleep(args.interval)


# --- automation settings (applied by the background service) ---------------------

def service_hint():
    from . import service
    if service.is_active() is False:
        print(f"\nNote: these are applied by the background service, which isn't running.\n"
              f"Start it with:  {service.ENABLE_CMD}")


def cmd_prefer(args):
    from .config import Config
    cfg = Config()
    if not args.headset:
        prefs = cfg.all_preferences()
        if args.json:
            return emit_json([{"address": a, "name": n, **p} for a, n, p in prefs])
        if not prefs:
            print("No headset preferences yet. Example:  btwctl prefer momentum --aptx hq --hfp off")
        for addr, name, p in prefs:
            print(f"  {name or addr}  ({addr}):  " + (", ".join(f"{k} {v}" for k, v in p.items()) or "nothing"))
        return

    with BTW5(verbose=args.verbose) as d:
        if args.headset == "current":
            conn = d.connection()
            if not conn or conn[0] != P.CONN_CONNECTED:
                sys.exit("No headset is connected.")
            addr, name = conn[1], conn[2]
        else:
            addr = resolve_headset(d, args.headset)
            name = next((n for _, a, n in d.slots() if a == addr), "")
        values = {"aptx": args.aptx, "hfp": args.hfp, "auto_hfp": args.auto_hfp}
        if args.current:
            st = read_status(d)
            if not st["headset"] or st["headset"]["address"] != P.fmt_addr(addr):
                sys.exit("--current needs that headset to be connected.")
            from .automation import game_running
            from .config import Config as _Config
            # while game mode forces Low Latency, that isn't the headset's real preference
            gaming = game_running(_Config())
            if gaming:
                print("A game is running with game mode on, so the aptX mode isn't remembered.")
            values = {"aptx": None if gaming else (st["aptx_mode"] or "").lower() or None,
                      "hfp": None if st["hfp"] is None else ("on" if st["hfp"] else "off"),
                      "auto_hfp": None if st["auto_hfp"] is None else ("on" if st["auto_hfp"] else "off")}

    label = name or P.fmt_addr(addr)
    if args.clear:
        cfg.clear_preferences(addr)
        cfg.save()
        print(f"Cleared the preferences for {label}.")
        return
    if not any(v is not None for v in values.values()):
        p = cfg.preferences(addr)
        print(f"{label}: " + (", ".join(f"{k} {v}" for k, v in p.items()) if p else "no preferences"))
        return
    cfg.set_preferences(addr, name=name, **values)
    cfg.save()
    p = cfg.preferences(addr)
    print(f"Whenever {label} connects: " + ", ".join(f"{k} {v}" for k, v in p.items()))
    service_hint()


def cmd_game_mode(args):
    from .automation import running_games
    from .config import Config
    cfg = Config()
    if args.action in ("on", "off"):
        cfg.game_mode = args.action == "on"
    elif args.action == "add":
        if not args.pattern:
            sys.exit("Usage: btwctl game-mode add <part of a game's command line>")
        cfg.game_apps = cfg.game_apps + [args.pattern]
    elif args.action == "remove":
        apps = cfg.game_apps
        if args.pattern not in apps:
            sys.exit(f"'{args.pattern}' isn't in the list: {', '.join(apps)}")
        cfg.game_apps = [a for a in apps if a != args.pattern]
    if args.action != "status":
        cfg.save()
    running = running_games(cfg.game_apps)
    print(f"Game mode    : {'on' if cfg.game_mode else 'off'} "
          "(aptX Low Latency while a game runs, then back)")
    print(f"Games        : processes whose command line contains {', '.join(cfg.game_apps)}")
    print(f"Running now  : {', '.join(sorted(running)) or 'none'}")
    if cfg.game_mode:
        service_hint()


def cmd_audio_follow(args):
    from . import audio
    from .config import Config
    cfg = Config()
    if args.action in ("on", "off"):
        cfg.audio_follow = args.action == "on"
        cfg.save()
    print(f"Audio follows the headset : {'on' if cfg.audio_follow else 'off'} "
          "(sound and, with HFP, the mic switch to the dongle while a headset is connected)")
    if not audio.available():
        print("Needs PipeWire's pw-dump and wpctl, which aren't installed.")
        return
    s = audio.snapshot()
    if s:
        def name(kind, current):
            n = next((n for n in s[kind] if n["name"] == current), None)
            return (n["description"] + ("  (the dongle)" if n["dongle"] else "")) if n else "none"
        print(f"Default output now        : {name('sinks', s['default_sink'])}")
        print(f"Default microphone now    : {name('sources', s['default_source'])}")
    if cfg.audio_follow:
        service_hint()


def cmd_gui(args):
    from .window import main as window_main
    window_main()


def cmd_tray(args):
    from .tray import main as tray_main
    tray_main(interval=args.interval)


# --- argument parsing --------------------------------------------------------

def run_notifying(args):
    """Run a command, showing its output (or error) as a desktop notification."""
    import contextlib
    import io
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            main_body(args)
    except SystemExit as e:
        if e.code not in (None, 0):
            notify("BT-W Control", str(e.code) if isinstance(e.code, str) else "Failed")
            raise
    text = out.getvalue().strip()
    print(text)
    if text:
        lines = text.splitlines()
        notify(lines[0], "\n".join(lines[1:]))


def build_parser():
    ap = argparse.ArgumentParser(
        prog="btwctl",
        description="Control the Creative BT-W5 and BT-W6 Bluetooth audio dongles.",
        epilog="Volume is a standard USB Audio control: use your desktop's volume "
               "settings, wpctl or alsamixer.")
    ap.add_argument("-v", "--verbose", action="store_true", help="hex-dump USB traffic to stderr")
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--device", dest="usb_id", metavar="VID:PID", default=os.environ.get("BTW5_DEVICE"),
                    help="USB ID of the dongle (default 041e:3130, the BT-W5; also BTW5_DEVICE)")
    ap.add_argument("--notify", dest="notify_result", action="store_true",
                   help="show the result as a desktop notification (for keyboard shortcuts and "
                        "launchers, where there's no terminal)")
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
    p.add_argument("--no-automation", dest="automation", action="store_false",
                   help="don't apply headset preferences or game mode (see 'prefer' and 'game-mode')")
    p.add_argument("--no-reconnect", dest="reconnect", action="store_false",
                   help="exit when the dongle disappears instead of waiting for it")
    p = add("waybar", cmd_waybar, "print status as JSON for a waybar/polybar custom module", device=False)
    p.add_argument("--interval", type=float, default=0,
                   help="keep printing every N seconds (default: print once and exit)")
    p = add("prefer", cmd_prefer, "settings to apply whenever a headset connects "
            "(list them without arguments)", True, device=False)
    p.add_argument("headset", nargs="?",
                   help="paired headset name, address, or 'current' for the connected one")
    p.add_argument("--aptx", choices=["hq", "ll"])
    p.add_argument("--hfp", choices=["on", "off"])
    p.add_argument("--auto-hfp", choices=["on", "off"])
    p.add_argument("--current", action="store_true", help="remember the headset's current settings")
    p.add_argument("--clear", action="store_true", help="forget the preferences for this headset")
    p = add("game-mode", cmd_game_mode, "use aptX Low Latency automatically while a game runs",
            device=False)
    p.add_argument("action", nargs="?", default="status", choices=["status", "on", "off", "add", "remove"])
    p.add_argument("pattern", nargs="?", help="for add/remove: part of a game's command line")
    p = add("audio-follow", cmd_audio_follow,
            "make the dongle the default audio device while a headset is connected", device=False)
    p.add_argument("action", nargs="?", default="status", choices=["status", "on", "off"])
    add("gui", cmd_gui, "open the BT-W Control window (needs PyQt6 or PySide6)", device=False)
    p = add("tray", cmd_tray, "run a system tray icon (needs PyQt6 or PySide6)", device=False)
    p.add_argument("--interval", type=float, default=15, help="seconds between status refreshes")

    p = add("raw", cmd_raw, "send a raw command, e.g. 'raw 6b 09 03' (for protocol exploration)")
    p.add_argument("bytes", nargs="+", type=hex_byte, help="hex bytes: command, then payload")
    p.add_argument("--listen", type=float, default=2.0, help="seconds to print replies")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.usb_id:
        from .device import use_device
        try:
            use_device(args.usb_id)
        except BTW5Error as e:
            sys.exit(str(e))
    if args.notify_result and args.cmd not in ("monitor", "tray", "gui", "waybar"):
        return run_notifying(args)
    main_body(args)


def main_body(args):
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
