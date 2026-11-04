import threading

import pytest

from btw5 import broker
from btw5 import protocol as P
from btw5.device import BTW5, Gone, read_status
from tests.fakes import FakeDongle


@pytest.fixture
def sock(tmp_path):
    return str(tmp_path / "btw5.sock")


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
    from btw5.device import NotFound
    with pytest.raises(NotFound):
        broker.SocketTransport(sock).open()
