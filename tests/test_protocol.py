import pytest

from btwctl import protocol as P
from tests.capture import usb_frames


def frames(direction):
    return [P.split_report(r.ljust(P.REPORT_LEN, b"\0")) for d, r in usb_frames() if d == direction]


# --- against the Wireshark capture --------------------------------------------

def test_capture_has_frames():
    assert len(frames(">")) > 50 and len(frames("<")) > 50


def test_requests_round_trip_through_build_report():
    for d, raw in usb_frames():
        if d == ">":
            cmd, payload = P.split_report(raw.ljust(P.REPORT_LEN, b"\0"))
            assert P.build_report(cmd, payload)[:len(raw)] == raw


def test_scan_results_in_capture():
    results = [P.parse_scan(p) for c, p in frames("<") if c == P.CMD_BT and p[0] == 0x05]
    assert len(results) >= 10
    for addr, name, rssi in results:
        assert P.fmt_addr(addr) == "68:00:1a:bc:5c:eb"
        assert name == "X3"
        assert -100 < rssi <= 0


def test_connection_events_in_capture():
    # (short '6b 02 06 07' frames are a different, undecoded event)
    events = [P.parse_conn(p) for c, p in frames("<") if c == P.CMD_BT and p[0] == P.BT_CONN and len(p) > 2]
    assert {e[0] for e in events} == {0x06, 0x08, 0x0a, 0x0b}
    assert all(e[2] == "X3" and P.fmt_addr(e[1]) == "68:00:1a:bc:5c:eb" for e in events)


def test_pairing_slot_in_capture():
    slots = [P.parse_slot(p) for c, p in frames("<") if c == P.CMD_BT and p[0] == P.BT_SLOT and len(p) > 10]
    assert slots and all(s == (0, True, bytes.fromhex("68001abc5ceb"), "X3") for s in slots)


def test_every_ack_in_capture_parses():
    acks = [P.parse_ack(c, p) for c, p in frames("<") if c == P.CMD_ACK]
    assert acks and all(a is not None for a in acks)
    statuses = {a[1] for a in acks}
    assert statuses == {P.STATUS_OK, P.STATUS_UNKNOWN_CMD}


def test_every_dongle_frame_is_understood_or_known_unknown():
    """Catch new frame types if the capture is ever extended."""
    known_unknown = {b"\x01\x01",   # reply to '6b 01'
                     b"\x06\x07"}   # sent after scan start / forget
    for c, p in frames("<"):
        if c == P.CMD_BT:
            assert (P.describe_event(c, p) or p[0] == P.BT_SLOT or bytes(p) in known_unknown
                    or p[0] == P.BT_GET), P.hexs(p)  # setting replies, incl. not-yet-understood keys


# --- unit tests ----------------------------------------------------------------

def test_parse_addr():
    assert P.parse_addr("68:00:1a:bc:5c:eb") == bytes.fromhex("68001abc5ceb")
    assert P.parse_addr("68-00-1A-BC-5C-EB") == bytes.fromhex("68001abc5ceb")
    for bad in ("68:00:1a:bc:5c", "momentum", "68:00:1a:bc:5c:zz", "680:0:1a:bc:5c:eb"):
        with pytest.raises(ValueError):
            P.parse_addr(bad)


def test_split_report_handles_foreign_reports():
    assert P.split_report(b"\x01\x02") == (None, b"\x01\x02")
    assert P.split_report(b"\x03\x6b\x01\x02")[0] is None  # not a 03 5a frame


def test_scan_with_long_name():
    p = bytes([0x05]) + bytes(6) + bytes([10]) + b"MOMENTUM 4" + (-61).to_bytes(2, "little", signed=True)
    assert P.parse_scan(p) == (bytes(6), "MOMENTUM 4", -61)
    assert P.parse_scan(p[:-1]) is None  # truncated


def test_setting_text():
    assert P.setting_text(P.GET_APTX, 0x20) == ("aptX Adaptive mode", "Low Latency")
    assert P.setting_text(P.GET_HFP, 0x01) == ("HFP", "on")
    assert P.setting_text(P.GET_HFP, 0x02) == ("HFP", "off")
    assert P.setting_text(P.GET_AUTO_HFP, 0x01) == ("auto HFP", "on")
    assert P.setting_text(0x05, 0x03) is None


def conn(state, name=b"X3", addr=bytes.fromhex("68001abc5ceb")):
    return bytes([P.BT_CONN, state]) + addr + bytes([32]) + name.ljust(32, b"\0")


def test_change_tracker_reports_only_changes():
    t = P.ChangeTracker()
    # baseline: printed, no notification
    assert t.feed(P.CMD_BT, conn(0x0b)) == ("headset connected: X3 (68:00:1a:bc:5c:eb)", None)
    assert t.feed(P.CMD_BT, bytes([P.BT_GET, P.GET_APTX, 0x40]))[1] is None
    # replies to someone else's status query: nothing new
    assert t.feed(P.CMD_BT, conn(0x08)) is None
    assert t.feed(P.CMD_BT, conn(0x0b)) is None
    assert t.feed(P.CMD_BT, bytes([P.BT_GET, P.GET_APTX, 0x40])) is None
    # real changes notify
    assert t.feed(P.CMD_BT, bytes([P.BT_GET, P.GET_APTX, 0x20]))[1] == ("aptX Adaptive mode: Low Latency", "")
    assert t.feed(P.CMD_BT, conn(0x0a))[1] == ("Headset disconnected", "X3 (68:00:1a:bc:5c:eb)")
    # the 'active' mode echo is shown but never notifies
    assert t.feed(P.CMD_BT, bytes([P.BT_GET, P.GET_APTX_ACTIVE, 0x40]))[1] is None
    assert t.feed(P.CMD_BT, bytes([P.BT_GET, P.GET_APTX_ACTIVE, 0x20]))[1] is None
    # acks and unrelated commands are ignored
    assert t.feed(P.CMD_ACK, bytes([0x6b, 0, 0x0a])) is None
    assert t.feed(P.CMD_INFO, b"\x02abc") is None


def test_prefixed_text():
    # BT-W6 key 09 (device name) and 0a, as captured from the Creative app
    assert P.prefixed_text(bytes.fromhex("0842542d57362030 38".replace(" ", ""))) == "BT-W6 08"
    assert P.prefixed_text(bytes.fromhex("06303030303030")) == "000000"
    # values that aren't text
    for v in ("40 00", "f5 03", "07 40", "3c b9 8a 08 00 02", "01", "", "02 41 42 43"):
        assert P.prefixed_text(bytes.fromhex(v.replace(" ", ""))) is None, v
