"""Share the dongle between processes.

Only one process can claim the BT-W5's HID interface. A long-running owner
('btwctl monitor') runs a Broker that serves a Unix socket; other
btwctl processes find the socket and talk through it with
SocketTransport instead of failing with 'busy'.

Wire format: SOCK_SEQPACKET, one 65-byte HID report per message. Clients send
output reports; the broker forwards every input report to every client.
"""

import errno
import os
import select
import socket
import stat

from . import protocol as P
from .device import BTW5Error, Gone, NotFound, UsbTransport


def _private_dir(path):
    """True if path is a real directory owned by us and closed to others."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077


def socket_path():
    """Where the broker socket lives, or None if there's no safe place for it."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base:
        # e.g. under sudo. Don't trust a directory someone else may have created.
        base = f"/tmp/btwctl-{os.getuid()}"
        try:
            os.mkdir(base, 0o700)
        except FileExistsError:
            pass
        except OSError:
            return None
    if not _private_dir(base):
        return None
    return os.path.join(base, "btwctl.sock")


def legacy_socket_path():
    """Where btwctl's predecessor, btw5-switch 0.6, shared the dongle (same protocol)."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    return os.path.join(base, "btw5-switch.sock") if base and _private_dir(base) else None


class SocketTransport:
    """Transport that goes through a running Broker."""

    def __init__(self, path=None):
        self.path = path or socket_path()
        self.sock = None

    def open(self):
        if not self.path:
            raise NotFound()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            s.connect(self.path)
        except OSError:
            s.close()
            raise NotFound() from None
        self.sock = s

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def write(self, data):
        try:
            self.sock.send(bytes(data))
        except OSError:
            raise Gone() from None

    def read(self, timeout_ms):
        r, _, _ = select.select([self.sock], [], [], timeout_ms / 1000)
        if not r:
            return None
        try:
            data = self.sock.recv(P.REPORT_LEN)
        except OSError:
            raise Gone() from None
        if not data:
            raise Gone()
        return data


def broker_available(path=None):
    path = path or socket_path()
    if not path or not os.path.exists(path):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def default_transport(busy_wait=3.0):
    """Use a running broker if there is one, otherwise the USB device directly."""
    from . import device
    path = socket_path()
    # the broker shares the auto-detected dongle; an explicit --device is always opened directly
    if device.auto_detect():
        # (the legacy socket: an old btw5-switch service still running after an upgrade)
        for p in (path, legacy_socket_path()):
            if p and broker_available(p):
                return SocketTransport(p)
    return UsbTransport(busy_wait=busy_wait)


class Broker:
    """Owns a transport and shares it over a Unix socket.

    run(on_report) blocks, calling on_report(raw) for every input report,
    until the dongle goes away (raises Gone) or the process is interrupted."""

    def __init__(self, transport, path=None):
        self.t = transport
        self.path = path or socket_path()
        self.server = None
        self.clients = []

    def __enter__(self):
        if not self.path:
            raise OSError(errno.EACCES, "no private directory for the broker socket")
        if broker_available(self.path):
            raise OSError(errno.EADDRINUSE, f"another btwctl is already sharing the dongle ({self.path})")
        try:
            os.unlink(self.path)  # stale socket from a crashed run
        except FileNotFoundError:
            pass
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        old = os.umask(0o177)  # create the socket 0600 (no chmod race)
        try:
            self.server.bind(self.path)
        finally:
            os.umask(old)
        self.server.listen(8)
        self.server.setblocking(False)
        return self

    def __exit__(self, *exc):
        for c in self.clients:
            c.close()
        self.clients = []
        if self.server:
            self.server.close()
            self.server = None
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
        return False

    def _drop(self, c):
        self.clients.remove(c)
        c.close()

    def poll_clients(self, timeout=0.0):
        r, _, _ = select.select([self.server, *self.clients], [], [], timeout)
        for s in r:
            if s is self.server:
                try:
                    c, _ = self.server.accept()
                except BlockingIOError:
                    continue
                c.setblocking(False)  # a client that stops reading must not stall everyone
                self.clients.append(c)
                continue
            try:
                data = s.recv(P.REPORT_LEN)
            except BlockingIOError:
                continue
            except OSError:
                data = b""
            if not data:
                self._drop(s)
                continue
            try:
                self.t.write(data)
            except Gone:
                raise
            except BTW5Error:
                self._drop(s)  # that client's request upset the dongle; keep serving the rest

    def broadcast(self, raw):
        for c in list(self.clients):
            try:
                c.send(raw)
            except OSError:  # incl. BlockingIOError: its queue is full, it isn't reading
                self._drop(c)

    def run(self, on_report=None, on_idle=None):
        try:
            while True:
                self.poll_clients()
                raw = self.t.read(20)
                if raw is not None:
                    self.broadcast(raw)
                    if on_report:
                        on_report(raw)
                if on_idle:
                    on_idle()
        except Gone:
            # Kick clients so their requests fail fast instead of timing out.
            for c in list(self.clients):
                self._drop(c)
            raise

