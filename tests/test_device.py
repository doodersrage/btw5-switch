from btw5 import protocol as P
from btw5.device import BTW5, REJECTED, read_status
from tests.fakes import HEADSET, FakeDongle


def test_get_and_set():
    with BTW5(FakeDongle()) as d:
        assert d.get(P.GET_APTX) == 0x40
        assert d.set(P.SET_APTX, 0x20) == P.STATUS_OK
        assert d.get(P.GET_APTX) == 0x20


def test_get_rejected_and_unknown():
    with BTW5(FakeDongle(connected=False)) as d:
        assert d.get(P.GET_APTX) == REJECTED
        assert d.get_raw(0x0e) == REJECTED


def test_connection_and_slots():
    with BTW5(FakeDongle()) as d:
        assert d.connection() == (P.CONN_CONNECTED, HEADSET, "MOMENTUM 4")
        slots = d.slots()
    assert len(slots) == P.PAIR_SLOTS
    assert slots[0] == (0, HEADSET, "MOMENTUM 4")
    assert all(a == P.ZERO_ADDR for _, a, _ in slots[1:])


def test_no_connection_reply():
    with BTW5(FakeDongle(connected=False)) as d:
        assert d.connection(timeout=0.2) is None


def test_read_status():
    with BTW5(FakeDongle()) as d:
        st = read_status(d)
    assert st["headset"] == {"name": "MOMENTUM 4", "address": "ba:52:e9:09:80:c3"}
    assert st["aptx_mode"] == "HQ" and st["hfp"] is False and st["auto_hfp"] is False


def test_read_status_disconnected():
    with BTW5(FakeDongle(connected=False)) as d:
        st = read_status(d)
    assert st["headset"] is None and st["aptx_mode"] is None and st["connection"] == "not connected"


def test_connect_rejected():
    with BTW5(FakeDongle(connect_status=0x80)) as d:
        assert d.connect(HEADSET) == P.STATUS_REJECTED


def test_verbose_logs_traffic(capsys):
    import sys
    with BTW5(FakeDongle(), verbose=True, log=sys.stdout) as d:
        d.get(P.GET_HFP)
    out = capsys.readouterr().out
    assert ">> 03 5a 6b 02 09 06" in out and "<< 03 5a 6b 03 09 06 02" in out
