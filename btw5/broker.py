"""Share the dongle between processes.

Only one process can claim the BT-W5's HID interface. A long-running owner
('btw5-switch monitor') runs a Broker that serves a Unix socket; other
btw5-switch processes find the socket and talk through it with
SocketTransport instead of failing with 'busy'.

Wire format: SOCK_SEQPACKET, one 65-byte HID report per message. Clients send
output reports; the broker forwards every input report to every client.
"""

import errno
import os
import select
import socket

from . import protocol as P
from .device import Gone, NotFound, UsbTransport


def socket_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/btw5-switch-{os.getuid()}"
    os.makedirs(base, mode=0o700, exist_ok=True)
    return os.path.join(base, "btw5-switch.sock")


class SocketTransport:
    """Transport that goes through a running Broker."""

    def __init__(self, path=None):
        self.path = path or socket_path()
        self.sock = None

    def open(self):
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
    if not os.path.exists(path):
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
    return SocketTransport() if broker_available() else UsbTransport(busy_wait=busy_wait)


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
        if broker_available(self.path):
            raise OSError(errno.EADDRINUSE, f"another btw5-switch is already sharing the dongle ({self.path})")
        try:
            os.unlink(self.path)  # stale socket from a crashed run
        except FileNotFoundError:
            pass
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.server.bind(self.path)
        os.chmod(self.path, 0o600)
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
                    self.clients.append(c)
                except BlockingIOError:
                    pass
                continue
            try:
                data = s.recv(P.REPORT_LEN)
            except OSError:
                data = b""
            if not data:
                self._drop(s)
            else:
                self.t.write(data)

    def broadcast(self, raw):
        for c in list(self.clients):
            try:
                c.send(raw)
            except OSError:
                self._drop(c)

    def run(self, on_report=None):
        try:
            while True:
                self.poll_clients()
                raw = self.t.read(20)
                if raw is not None:
                    self.broadcast(raw)
                    if on_report:
                        on_report(raw)
        except Gone:
            # Kick clients so their requests fail fast instead of timing out.
            for c in list(self.clients):
                self._drop(c)
            raise

