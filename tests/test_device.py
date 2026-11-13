from btwctl import protocol as P
from btwctl.device import BTW5, REJECTED, read_status
from tests.fakes import HEADSET, FakeDongle, report


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


# --- several programs share the dongle through the broker and see each other's replies

class CrossTalk(FakeDongle):
    """Before answering, deliver a reply that belongs to some other program."""

    def __init__(self, foreign, **kw):
        super().__init__(**kw)
        self.foreign = foreign

    def write(self, data):
        self.queue.append(self.foreign)
        super().write(data)


def test_someone_elses_rejected_get_is_not_our_answer():
    from tests.fakes import ack
    with BTW5(CrossTalk(ack(P.CMD_BT, P.STATUS_REJECTED, P.BT_GET))) as d:
        assert d.get(P.GET_HFP) == 0x02


def test_our_own_rejected_get_is_still_reported():
    with BTW5(FakeDongle(connected=False)) as d:
        assert d.get(P.GET_APTX) == REJECTED


def test_someone_elses_set_ack_is_not_our_answer():
    from tests.fakes import report
    other = report(P.CMD_ACK, [P.CMD_BT, P.STATUS_REJECTED, P.BT_SET, P.SET_HFP, 0x01])
    with BTW5(CrossTalk(other)) as d:
        assert d.set(P.SET_APTX, 0x20) == P.STATUS_OK


def test_usb_errors_become_btw5_errors():
    import errno

    import pytest

    from btwctl.device import BTW5Error, Gone, usb_error

    class E(Exception):
        def __init__(self, n):
            self.errno = n
    assert isinstance(usb_error(E(errno.ENODEV)), Gone)
    assert isinstance(usb_error(E(errno.EPIPE)), Gone)
    err = usb_error(E(errno.ETIMEDOUT))
    assert isinstance(err, BTW5Error) and not isinstance(err, Gone)
    with pytest.raises(BTW5Error):
        raise err


def test_not_found_mentions_other_creative_devices(monkeypatch):
    from btwctl import device
    monkeypatch.setattr(device, "PRODUCT_ID", None)
    monkeypatch.setattr(device, "other_creative_devices", lambda: [(0x3237, "Creative BT-W4")])
    msg = str(device.NotFound(look_for_others=True))
    assert msg.startswith("No Creative BT-W5 or BT-W6 found")
    assert "041e:3237  Creative BT-W4" in msg and "--device 041e:XXXX dump" in msg
    assert "Found other" not in str(device.NotFound())


def test_use_device(monkeypatch):
    import pytest

    from btwctl import device
    from btwctl.device import BTW5Error
    monkeypatch.setattr(device, "VENDOR_ID", device.VENDOR_ID)
    monkeypatch.setattr(device, "PRODUCT_ID", device.PRODUCT_ID)
    device.use_device("041e:3132")
    assert (device.VENDOR_ID, device.PRODUCT_ID) == (0x041e, 0x3132)
    assert "No USB device 041e:3132" in str(device.NotFound(look_for_others=True))
    with pytest.raises(BTW5Error):
        device.use_device("bt-w4")



class FakeUSB:
    """Just enough of usb.core for find_dongle(): a bus with some devices on it."""

    def __init__(self, *ids):
        class Dev:
            def __init__(self, vid, pid):
                self.idVendor, self.idProduct = vid, pid

        class Core:
            @staticmethod
            def find(idVendor=None, idProduct=None, find_all=False):
                hits = [Dev(v, p) for v, p in ids
                        if (idVendor is None or v == idVendor) and (idProduct is None or p == idProduct)]
                return hits if find_all else (hits[0] if hits else None)
        self.core = Core


def test_auto_detects_bt_w5_and_bt_w6(monkeypatch):
    from btwctl import device
    monkeypatch.setattr(device, "PRODUCT_ID", None)
    monkeypatch.setattr(device, "_usb", lambda: FakeUSB((0x041e, 0x3132), (0x046d, 0xc08b)))
    assert device.find_dongle().idProduct == 0x3132
    monkeypatch.setattr(device, "_usb", lambda: FakeUSB((0x041e, 0x3130)))
    assert device.find_dongle().idProduct == 0x3130
    monkeypatch.setattr(device, "_usb", lambda: FakeUSB((0x041e, 0x3237)))   # some other Creative device
    assert device.find_dongle() is None


def test_explicit_device_overrides_auto_detection(monkeypatch):
    from btwctl import device
    monkeypatch.setattr(device, "PRODUCT_ID", None)
    monkeypatch.setattr(device, "_usb", lambda: FakeUSB((0x041e, 0x3130), (0x041e, 0x3237)))
    device.use_device("041e:3237")
    assert device.find_dongle().idProduct == 0x3237
    assert not device.auto_detect()


def test_explicit_device_skips_the_broker(monkeypatch):
    from btwctl import broker, device
    monkeypatch.setattr(broker, "broker_available", lambda path=None: True)
    monkeypatch.setattr(broker, "socket_path", lambda: "/nonexistent/sock")
    monkeypatch.setattr(device, "PRODUCT_ID", None)
    assert isinstance(broker.default_transport(), broker.SocketTransport)
    monkeypatch.setattr(device, "PRODUCT_ID", 0x3237)
    assert isinstance(broker.default_transport(), device.UsbTransport)



class FakeBTW6(FakeDongle):
    """BT-W6 firmware 1.0.240906.0910 as measured: '09 03' is rejected, key 08 holds the
    mode as '<val> 00', '0a 03 <val>' sets it, and '0a 08 ..' is acked but changes nothing."""

    def write(self, data):
        cmd, p = P.split_report(data)
        if cmd == P.CMD_BT and p[:2] == bytes([P.BT_GET, P.GET_APTX]):
            self.sent.append((cmd, bytes(p)))
            self.queue.append(report(P.CMD_ACK, [P.CMD_BT, P.STATUS_REJECTED, P.BT_GET]))
        elif cmd == P.CMD_BT and p[:2] == bytes([P.BT_GET, P.GET_APTX_ACTIVE]):
            self.sent.append((cmd, bytes(p)))
            self.queue.append(report(P.CMD_BT, [P.BT_GET, P.GET_APTX_ACTIVE,
                                                self.settings[P.GET_APTX_ACTIVE], 0x00]))
        elif cmd == P.CMD_BT and p[:2] == bytes([P.BT_SET, P.GET_APTX_ACTIVE]):
            self.sent.append((cmd, bytes(p)))
            self.queue.append(report(P.CMD_ACK, [P.CMD_BT, P.STATUS_OK, *p[:3]]))
        else:
            super().write(data)


def btw6(fake, is_btw6=True):
    d = BTW5(fake)
    d._btw6 = is_btw6
    return d


def test_btw6_reads_aptx_from_key_08():
    with btw6(FakeBTW6(aptx=0x20)) as d:
        assert d.get(P.GET_APTX) == REJECTED
        assert d.get_aptx() == 0x20
        assert read_status(d)["aptx_mode"] == "LL"


def test_btw6_sets_aptx_with_key_03():
    fake = FakeBTW6(aptx=0x20)
    with btw6(fake) as d:
        assert d.set_aptx(0x40) == P.STATUS_OK
        assert d.get_aptx() == 0x40
    sets = [p for c, p in fake.sent if p[:1] == bytes([P.BT_SET])]
    assert sets == [bytes([P.BT_SET, P.SET_APTX, 0x40])]


def test_btw5_reads_key_03():
    fake = FakeDongle()
    with btw6(fake, is_btw6=False) as d:
        assert d.get_aptx() == 0x40
    assert (P.CMD_BT, bytes([P.BT_GET, P.GET_APTX])) in fake.sent
    assert (P.CMD_BT, bytes([P.BT_GET, P.GET_APTX_ACTIVE])) not in fake.sent
