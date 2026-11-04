"""Talking to the BT-W5: a USB transport plus the request/reply logic on top."""

import errno
import sys
import time

from . import protocol as P

VENDOR_ID = 0x041e   # Creative Technology
PRODUCT_ID = 0x3130  # BT-W5
HID_INTERFACE = 0
EP_IN = 0x81

REJECTED = "rejected"  # get() result when the dongle refuses a query


class BTW5Error(Exception):
    """Base class for errors worth showing to the user as-is."""


class NotFound(BTW5Error):
    def __init__(self):
        super().__init__("Creative BT-W5 not found. Is it plugged in? (check with: lsusb -d 041e:3130)")


class PermissionDenied(BTW5Error):
    def __init__(self):
        super().__init__("Permission denied accessing the BT-W5.\n"
                         "Run with sudo, or install the udev rule (see README) and re-plug the dongle.")


class Busy(BTW5Error):
    def __init__(self):
        super().__init__("The BT-W5 is in use by another program "
                         "(is 'btw5-switch monitor' running?).")


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
    return _usb().core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID) is not None


def wait_for_device(timeout=None, poll=1.0):
    """Block until the dongle is plugged in. Returns False on timeout."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while not is_present():
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(poll)
    return True


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
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.dev is None:
            raise NotFound()
        deadline = time.monotonic() + self.busy_wait
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
                if e.errno == errno.ENODEV:
                    raise Gone() from None
                if e.errno != errno.EBUSY:
                    raise
                if time.monotonic() >= deadline:
                    raise Busy() from None
                time.sleep(0.2)  # another short-lived btw5-switch (tray, status bar) has it

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
            if e.errno in (errno.ENODEV, errno.EIO):
                raise Gone() from None
            raise

    def read(self, timeout_ms):
        """Next input report, or None on timeout."""
        try:
            return bytes(self.dev.read(EP_IN, P.REPORT_LEN, timeout=timeout_ms))
        except self.usb.core.USBError as e:
            if e.errno == errno.ETIMEDOUT or (e.errno is None and "timed out" in str(e).lower()):
                return None
            if e.errno in (errno.ENODEV, errno.EIO, errno.EPIPE):
                raise Gone() from None
            raise


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
            print(f"<< {P.hexs(raw.rstrip(b'\0'))}", file=self.log)
        return P.split_report(raw)

    def drain(self):
        while self.recv(30) is not None:
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

    def command(self, cmd, payload, timeout=1.5):
        """Send a command and return its ack status (0 = accepted), or None if no ack came."""
        def is_my_ack(c, p):
            a = P.parse_ack(c, p)
            return a is not None and a[0] == cmd and (a[2] is None or not payload or a[2] == payload[0])
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
        return bytes(p[2:]) if p[0] == P.BT_GET else REJECTED

    def get(self, key):
        """Setting value, REJECTED if the dongle refused, or None on no reply."""
        r = self.get_raw(key)
        if r in (None, REJECTED):
            return r
        return r[0] if r else 0

    def set(self, key, value):
        """Ack status (0 = accepted), or None if no ack arrived."""
        return self.command(P.CMD_BT, [P.BT_SET, key, value])

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

    def forget_all(self):
        self.send(P.CMD_BT, [P.BT_FORGET, 0xff])


def read_status(d):
    """Snapshot of everything 'status' shows, as a plain dict (JSON friendly)."""
    conn = d.connection()
    aptx = d.get(P.GET_APTX)
    hfp = d.get(P.GET_HFP)
    auto = d.get(P.GET_AUTO_HFP)

    def flag(v, on):
        return None if v in (None, REJECTED) else v == on

    headset = None
    if conn and conn[0] == P.CONN_CONNECTED:
        headset = {"name": conn[2], "address": P.fmt_addr(conn[1])}
    return {
        "connection": P.conn_state_name(conn[0]) if conn else "not connected",
        "headset": headset,
        "last_headset": {"name": conn[2], "address": P.fmt_addr(conn[1])} if conn else None,
        "aptx_mode": P.APTX_SHORT.get(aptx) if aptx not in (None, REJECTED) else None,
        "hfp": flag(hfp, P.HFP_VALUES["on"]),
        "auto_hfp": flag(auto, P.AUTO_HFP_VALUES["on"]),
    }
