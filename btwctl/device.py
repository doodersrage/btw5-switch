"""Talking to the dongle: a USB transport plus the request/reply logic on top."""

import errno
import sys
import time

from . import protocol as P

VENDOR_ID = 0x041e   # Creative Technology

# Dongles known to speak this protocol: product id -> (model, tested by the author)
MODELS = {
    0x3130: ("BT-W5", True),
    0x3132: ("BT-W6", False),   # aptX switching confirmed by a user; the rest is the same app family
}
BTW6_PID = 0x3132

# None = use whichever known model is plugged in; set by --device / BTW5_DEVICE
PRODUCT_ID = None


def use_device(spec):
    """Talk to a specific USB ID ('041e:3132'), e.g. to try a dongle that isn't in MODELS."""
    global VENDOR_ID, PRODUCT_ID
    try:
        vid, pid = (int(x, 16) for x in spec.split(":"))
    except ValueError:
        raise BTW5Error(f"--device must look like 041e:3130, not '{spec}'") from None
    VENDOR_ID, PRODUCT_ID = vid, pid


def auto_detect():
    """True unless a specific device was chosen with --device."""
    return PRODUCT_ID is None


def model_name(pid, vid=0x041e):
    if vid == 0x041e and pid in MODELS:
        return f"Creative {MODELS[pid][0]}"
    return f"USB device {vid:04x}:{pid:04x}"


def find_dongle():
    """The usb.core.Device to talk to, or None."""
    usb = _usb()
    if not auto_detect():
        return usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    for pid in MODELS:
        dev = usb.core.find(idVendor=0x041e, idProduct=pid)
        if dev is not None:
            return dev
    return None


def dongle_info():
    """{'model', 'usb_id', 'tested'} of the dongle in use, or None. Doesn't need to open it."""
    try:
        dev = find_dongle()
    except BTW5Error:
        return None
    if dev is None:
        return None
    known = MODELS.get(dev.idProduct) if dev.idVendor == 0x041e else None
    return {"model": model_name(dev.idProduct, dev.idVendor),
            "usb_id": f"{dev.idVendor:04x}:{dev.idProduct:04x}",
            "tested": bool(known and known[1])}


def other_creative_devices():
    """[(product id, name)] of plugged-in Creative USB devices we don't know as dongles."""
    try:
        usb = _usb()
        devs = list(usb.core.find(find_all=True, idVendor=0x041e))
    except Exception:
        return []
    out = []
    for d in devs:
        if d.idProduct in MODELS or d.idProduct == PRODUCT_ID:
            continue
        try:
            name = d.product
        except Exception:  # no permission to read strings, or the device didn't answer
            name = None
        out.append((d.idProduct, name or "unknown Creative device"))
    return out


HID_INTERFACE = 0
EP_IN = 0x81

REJECTED = "rejected"  # get() result when the dongle refuses a query


class BTW5Error(Exception):
    """Base class for errors worth showing to the user as-is."""


class NotFound(BTW5Error):
    def __init__(self, look_for_others=False):
        """look_for_others: mention other Creative USB devices (only after a real USB lookup)."""
        if not auto_detect():
            super().__init__(f"No USB device {VENDOR_ID:04x}:{PRODUCT_ID:04x} found.")
            return
        msg = ("No Creative BT-W5 or BT-W6 found. Is it plugged in? "
               "(check with: lsusb | grep -i creative)")
        others = other_creative_devices() if look_for_others else []
        if others:
            msg += "\n\nFound other Creative devices:\n" + "\n".join(
                f"  041e:{pid:04x}  {name}" for pid, name in others)
            msg += ("\nIf one of them is a BT-W series Bluetooth dongle, it may speak the same protocol."
                    "\nTry it with:   btwctl --device 041e:XXXX status"
                    "\nand please open an issue with the output of:   btwctl --device 041e:XXXX dump"
                    "\nhttps://github.com/doodersrage/btwctl/issues")
        super().__init__(msg)


class PermissionDenied(BTW5Error):
    def __init__(self):
        super().__init__("Permission denied accessing the dongle.\n"
                         "Run with sudo, or install the udev rule (see README) and re-plug the dongle.")


class Busy(BTW5Error):
    def __init__(self):
        super().__init__("The dongle is in use by another program "
                         "(is 'btwctl monitor' running?).")


class Gone(BTW5Error):
    """The dongle disappeared (unplugged, or re-enumerating after HFP/forget)."""
    def __init__(self):
        super().__init__("The dongle disconnected (it re-enumerates after HFP changes and 'forget').")


def _usb():
    try:
        import usb.core
        import usb.util
    except ImportError:
        raise BTW5Error("PyUSB not found. Install it with:\n"
                        "  Arch Linux:    sudo pacman -S python-pyusb\n"
                        "  Debian/Ubuntu: sudo apt install python3-usb")
    return usb


def is_present():
    return find_dongle() is not None


def wait_for_device(timeout=None, poll=1.0):
    """Block until the dongle is plugged in. Returns False on timeout."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while not is_present():
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(poll)
    return True


def usb_error(e):
    """Map a PyUSB error to a BTW5Error the CLI, broker and tray know how to handle."""
    if e.errno in (errno.ENODEV, errno.EIO, errno.EPIPE, errno.ESHUTDOWN):
        return Gone()
    return BTW5Error(f"USB error talking to the BT-W5: {e}")


class UsbTransport:
    """Claims HID interface 0 with PyUSB.

    The kernel can't do anything useful with this interface (its report
    descriptor doesn't declare the input report the dongle answers with, so
    hidraw never sees replies), but we still give it back afterwards."""

    def __init__(self, busy_wait=3.0):
        self.usb = _usb()
        self.busy_wait = busy_wait
        self.dev = None
        self.reattach = False

    def open(self):
        usb = self.usb
        self.dev = find_dongle()
        if self.dev is None:
            raise NotFound(look_for_others=True)
        deadline = time.monotonic() + self.busy_wait
        try:
            while True:
                try:
                    if self.dev.is_kernel_driver_active(HID_INTERFACE):
                        self.dev.detach_kernel_driver(HID_INTERFACE)
                        self.reattach = True
                    usb.util.claim_interface(self.dev, HID_INTERFACE)
                    return
                except usb.core.USBError as e:
                    if e.errno == errno.EACCES:
                        raise PermissionDenied() from None
                    if e.errno != errno.EBUSY:
                        raise usb_error(e) from None
                    if time.monotonic() >= deadline:
                        raise Busy() from None
                    time.sleep(0.2)  # another short-lived btwctl (tray, status bar) has it
        except BaseException:
            self.close()  # don't leave the interface detached from its driver
            raise

    def close(self):
        if self.dev is None:
            return
        usb = self.usb
        # The dongle re-enumerates after some commands, so failures here are harmless.
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
        self.dev = None
        self.reattach = False

    def write(self, data):
        try:
            self.dev.ctrl_transfer(0x21, 0x09, wValue=0x0200 | P.REPORT_ID,
                                   wIndex=HID_INTERFACE, data_or_wLength=data)
        except self.usb.core.USBError as e:
            raise usb_error(e) from None

    def read(self, timeout_ms):
        """Next input report, or None on timeout."""
        try:
            return bytes(self.dev.read(EP_IN, P.REPORT_LEN, timeout=timeout_ms))
        except self.usb.core.USBError as e:
            if e.errno == errno.ETIMEDOUT or (e.errno is None and "timed out" in str(e).lower()):
                return None
            raise usb_error(e) from None


class BTW5:
    """High-level access to the dongle. Use as a context manager."""

    def __init__(self, transport=None, verbose=False, log=sys.stderr):
        if transport is None:
            from .broker import default_transport
            transport = default_transport()
        self.t = transport
        self.verbose = verbose
        self.log = log

    def __enter__(self):
        self.t.open()
        return self

    def __exit__(self, *exc):
        self.t.close()
        return False

    # --- low level -------------------------------------------------------

    def send(self, cmd, payload):
        data = P.build_report(cmd, payload)
        if self.verbose:
            print(f">> {P.hexs(data[:4 + len(payload)])}", file=self.log)
        self.t.write(data)

    def recv(self, timeout_ms):
        """(cmd, payload) of the next input report, or None on timeout."""
        raw = self.t.read(timeout_ms)
        if raw is None:
            return None
        if self.verbose:
            print("<< " + P.hexs(raw.rstrip(b"\0")), file=self.log)
        return P.split_report(raw)

    def drain(self, max_seconds=0.2):
        """Discard stale reports. Bounded in time: with the dongle shared, other
        programs' traffic can keep arriving, and replies are matched anyway."""
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline and self.recv(30) is not None:
            pass

    def listen(self, seconds):
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            r = self.recv(max(1, int(left * 1000)))
            if r:
                yield r

    def request(self, cmd, payload, match, timeout=1.5):
        """Send a command and return the payload of the first reply where match(cmd, payload)."""
        self.drain()
        self.send(cmd, payload)
        for c, p in self.listen(timeout):
            if c is not None and match(c, p):
                return p
        return None

    def command(self, cmd, payload, timeout=1.5, echo=1):
        """Send a command and return its ack status (0 = accepted), or None if no ack came.

        Acks repeat the start of the request payload: 'set' acks echo all of
        '0a <key> <val>', most others only the first byte. echo says how many
        bytes to compare, so replies to other processes sharing the dongle
        (see broker.py) aren't mistaken for ours."""
        want = bytes(payload[:echo])

        def is_my_ack(c, p):
            a = P.parse_ack(c, p)
            if a is None or a[0] != cmd:
                return False
            if a[2] is None or not want:
                return True
            got = bytes(p[2:2 + len(want)]).ljust(len(want), b"\0")
            if got == want:
                return True
            # A rejection may echo only the first byte (rest zero); one that
            # echoes something else is a reply to another program's request.
            return a[1] != P.STATUS_OK and got[:1] == want[:1] and not any(got[1:])
        p = self.request(cmd, payload, is_my_ack, timeout)
        return None if p is None else p[1]

    # --- settings --------------------------------------------------------

    def get_raw(self, key):
        """Reply bytes after '09 <key>' (some keys return more than one), REJECTED, or None."""
        def match(c, p):
            if c == P.CMD_BT and len(p) >= 2 and p[0] == P.BT_GET and p[1] == key:
                return True
            a = P.parse_ack(c, p)
            return a is not None and a[0] == P.CMD_BT and a[1] != P.STATUS_OK and a[2] == P.BT_GET
        p = self.request(P.CMD_BT, [P.BT_GET, key], match)
        if p is None:
            return None
        if p[0] == P.BT_GET:
            return bytes(p[2:])
        # The rejection doesn't say which key it was for, and with several
        # programs sharing the dongle it may be someone else's. If our value
        # still turns up shortly, that's the real answer.
        for c, q in self.listen(0.15):
            if c == P.CMD_BT and len(q) >= 2 and q[0] == P.BT_GET and q[1] == key:
                return bytes(q[2:])
        return REJECTED

    def get(self, key):
        """Setting value, REJECTED if the dongle refused, or None on no reply."""
        r = self.get_raw(key)
        if r in (None, REJECTED):
            return r
        return r[0] if r else 0

    def set(self, key, value):
        """Ack status (0 = accepted), or None if no ack arrived."""
        return self.command(P.CMD_BT, [P.BT_SET, key, value], echo=3)

    # --- aptX mode ---------------------------------------------------------
    # Both models set the mode with '0a 03 <val>'. The BT-W5 reads it back with
    # '09 03'; the BT-W6 (firmware 1.0.240906.0910) rejects that, so there it is
    # read from key 08 ('40 00' HQ, '20 00' LL). Writing key 08 is acked by the
    # BT-W6 but changes nothing. See issue #2.

    def is_btw6(self):
        if not hasattr(self, "_btw6"):
            info = dongle_info()
            self._btw6 = bool(info) and info["usb_id"] == f"041e:{BTW6_PID:04x}"
        return self._btw6

    def get_aptx(self):
        """aptX mode value (0x20/0x40), REJECTED, or None on no reply."""
        return self.get(P.GET_APTX_ACTIVE if self.is_btw6() else P.GET_APTX)

    def set_aptx(self, value):
        """Ack status (0 = accepted), or None if no ack arrived."""
        return self.set(P.SET_APTX, value)

    def info(self, field, extra=()):
        p = self.request(P.CMD_INFO, [field, *extra],
                         lambda c, p: c == P.CMD_INFO and len(p) >= 1 and p[0] == field)
        return None if p is None else bytes(p[1:])

    def info_str(self, field):
        b = self.info(field)
        if b is None:
            return None
        return b.split(b"\0")[0].lstrip(bytes(range(0x20))).decode("ascii", "replace")

    # --- Bluetooth -------------------------------------------------------

    def connection(self, timeout=1.0):
        """(state, addr, name) of the headset link, or None if the dongle didn't say.

        The dongle answers with one or more 6b 06 frames (e.g. connecting, then
        connected); the last one is the current state. With no headset at all
        it doesn't answer."""
        self.drain()
        self.send(P.CMD_BT, [P.BT_CONN])
        last = None
        for c, p in self.listen(timeout):
            conn = P.parse_conn(p) if c == P.CMD_BT else None
            if conn:
                last = conn
                if conn[0] in (P.CONN_DISCONNECTED, P.CONN_CONNECTED, P.CONN_NOT_CONNECTED):
                    break
        return last

    def slots(self):
        """Pairing slots as a list of (slot, addr, name); empty slots have addr ZERO_ADDR."""
        out = []
        for i in range(P.PAIR_SLOTS):
            p = self.request(P.CMD_BT, [P.BT_SLOT, i],
                             lambda c, p: c == P.CMD_BT and len(p) >= 2 and p[0] == P.BT_SLOT and p[1] == i)
            s = P.parse_slot(p) if p is not None else None
            if s is None or not s[1]:
                break
            out.append((s[0], s[2], s[3]))
        return out

    def start_scan(self):
        """Disconnect the headset and start scanning. Returns the ack status."""
        return self.command(P.CMD_BT, [P.BT_SCAN, P.SCAN_START])

    def connect(self, addr):
        """Ask the dongle to connect to addr. Returns the ack status."""
        return self.command(P.CMD_BT, [P.BT_SCAN, P.SCAN_CONNECT, *addr])

    def scan_events(self, seconds, should_stop=None):
        """Start a scan and yield what it finds for up to `seconds`:

            ("found", addr, name, rssi)   for each scan report (repeats with fresh RSSI)
            ("connected", addr, name)     if the dongle connects to a headset by itself
                                          (it often does with one in pairing mode); ends the scan

        The caller should check start_scan's ack first; this calls it and
        raises BTW5Error if the dongle refuses. should_stop() can end it early."""
        status = self.start_scan()
        if status not in (P.STATUS_OK, None):
            raise BTW5Error(f"The dongle refused to scan (status 0x{status:02x}).")
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            if should_stop and should_stop():
                return
            r = self.recv(max(1, int(min(left, 0.2) * 1000)))  # stay responsive to should_stop
            if r is None:
                continue
            cmd, p = r
            if cmd != P.CMD_BT:
                continue
            r = P.parse_scan(p)
            if r:
                yield ("found", *r)
                continue
            conn = P.parse_conn(p)
            if conn and conn[0] == P.CONN_CONNECTED:
                yield ("connected", conn[1], conn[2])
                return

    def connect_and_wait(self, addr, timeout=15.0):
        """Connect to addr and wait for the result.

        Returns (status, name): status is "connected", "rejected" (the dongle
        can't reach the headset), "disconnected" or "timeout"."""
        status = self.connect(addr)
        if status == P.STATUS_REJECTED:
            return "rejected", None
        if status not in (P.STATUS_OK, None):
            raise BTW5Error(f"The dongle rejected the connect request (status 0x{status:02x}).")
        result = "timeout", None
        for cmd, p in self.listen(timeout):
            conn = P.parse_conn(p) if cmd == P.CMD_BT else None
            if conn and conn[1] == bytes(addr):
                if conn[0] == P.CONN_CONNECTED:
                    return "connected", conn[2]
                if conn[0] == P.CONN_DISCONNECTED:
                    result = "disconnected", conn[2]
        return result

    def forget_all(self):
        self.send(P.CMD_BT, [P.BT_FORGET, 0xff])


def read_status(d):
    """Snapshot of everything 'status' shows, as a plain dict (JSON friendly)."""
    conn = d.connection()
    aptx = d.get_aptx()
    hfp = d.get(P.GET_HFP)
    auto = d.get(P.GET_AUTO_HFP)

    def flag(v, on):
        return None if v in (None, REJECTED) else v == on

    dongle = dongle_info()
    headset = None
    if conn and conn[0] == P.CONN_CONNECTED:
        headset = {"name": conn[2], "address": P.fmt_addr(conn[1])}
    return {
        "dongle": dongle and dongle["model"],
        "connection": P.conn_state_name(conn[0]) if conn else "not connected",
        "headset": headset,
        "last_headset": {"name": conn[2], "address": P.fmt_addr(conn[1])} if conn else None,
        "aptx_mode": P.APTX_SHORT.get(aptx) if aptx not in (None, REJECTED) else None,
        "hfp": flag(hfp, P.HFP_VALUES["on"]),
        "auto_hfp": flag(auto, P.AUTO_HFP_VALUES["on"]),
    }


def read_dump(d):
    """Every readable register, as a JSON-friendly dict (for 'dump' and bug reports)."""
    def show(v):
        if v is None:
            return None
        return "rejected" if v == REJECTED else P.hexs(v)

    conn = d.connection()
    return {
        "settings (6b 09 <key>)": {f"{k:02x}": show(d.get_raw(k)) for k in P.GET_KEYS_ALL},
        "info (09 <field>)": {"00": show(d.info(0x00)), "01 00": show(d.info(0x01, [0x00])),
                              "02": d.info_str(P.INFO_FIRMWARE), "03": d.info_str(P.INFO_SERIAL)},
        "connection (6b 06)": None if conn is None else
            {"state": f"{conn[0]:02x} ({P.conn_state_name(conn[0])})",
             "address": P.fmt_addr(conn[1]), "name": conn[2]},
        "pairing slots (6b 02)": [{"slot": s, "address": P.fmt_addr(a), "name": n}
                                  for s, a, n in d.slots()],
    }
