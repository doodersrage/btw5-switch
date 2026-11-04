import threading
import time

import pytest

from btwctl import broker
from btwctl import protocol as P
from btwctl.device import BTW5, Gone, read_status
from tests.fakes import FakeDongle


@pytest.fixture
def sock(tmp_path):
    return str(tmp_path / "btwctl.sock")


def serve(b, seen, stop):
    """Broker loop that stops when asked (the real one runs until the dongle goes away)."""
    class Stop(Exception):
        pass
    orig = b.t.read

    def read(timeout_ms):
        if stop.is_set():
            raise Gone()
        return orig(timeout_ms)
    b.t.read = read
    try:
        b.run(seen.append)
    except Gone:
        pass


def test_clients_share_the_dongle(sock):
    fake, seen, stop = FakeDongle(), [], threading.Event()
    with broker.Broker(fake, path=sock) as b:
        assert broker.broker_available(sock)
        th = threading.Thread(target=serve, args=(b, seen, stop))
        th.start()
        try:
            with BTW5(broker.SocketTransport(sock)) as d1, BTW5(broker.SocketTransport(sock)) as d2:
                assert read_status(d1)["aptx_mode"] == "HQ"
                assert d2.set(P.SET_APTX, 0x20) == P.STATUS_OK
                assert d1.get(P.GET_APTX) == 0x20
        finally:
            stop.set()
            th.join(5)
    assert not broker.broker_available(sock)
    assert any(P.split_report(r)[0] == P.CMD_BT for r in seen)  # the owner sees all traffic


def test_second_broker_refused(sock):
    with broker.Broker(FakeDongle(), path=sock):
        with pytest.raises(OSError, match="already sharing"):
            with broker.Broker(FakeDongle(), path=sock):
                pass


def test_stale_socket_is_replaced(sock):
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    s.bind(sock)
    s.close()  # leaves the file behind, nobody listening
    assert not broker.broker_available(sock)
    with broker.Broker(FakeDongle(), path=sock):
        assert broker.broker_available(sock)


def test_socket_transport_without_broker(sock):
    from btwctl.device import NotFound
    with pytest.raises(NotFound):
        broker.SocketTransport(sock).open()


def test_client_that_stops_reading_does_not_stall_others(sock):
    """A suspended 'btwctl scan' (Ctrl-Z) used to freeze the broker for everyone."""
    import socket as so

    from tests.fakes import conn_frame

    class Chatty(FakeDongle):
        def read(self, timeout_ms):
            if self.queue:
                return super().read(timeout_ms)
            time.sleep(0.001)  # ~1000 events/s: far more than a real dongle sends
            return conn_frame(P.CONN_CONNECTED).ljust(P.REPORT_LEN, b"\0")

    fake, seen, stop = Chatty(), [], threading.Event()
    with broker.Broker(fake, path=sock) as b:
        th = threading.Thread(target=serve, args=(b, seen, stop))
        th.start()
        try:
            lazy = so.socket(so.AF_UNIX, so.SOCK_SEQPACKET)
            lazy.connect(sock)  # never reads
            deadline = time.monotonic() + 5
            while len(seen) < 2000 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert len(seen) >= 2000, "broker stalled behind a client that doesn't read"
            with BTW5(broker.SocketTransport(sock)) as d:
                assert d.get(P.GET_HFP) == 0x02
            lazy.close()
        finally:
            stop.set()
            th.join(5)


def test_unsafe_socket_dir_is_refused(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o777)
    shared.chmod(0o777)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(shared))
    assert broker.socket_path() is None
    shared.chmod(0o700)
    assert broker.socket_path() == str(shared / "btwctl.sock")


def test_no_safe_socket_dir_falls_back_to_usb(monkeypatch):
    monkeypatch.setattr(broker, "socket_path", lambda: None)
    assert isinstance(broker.default_transport(), broker.UsbTransport)
