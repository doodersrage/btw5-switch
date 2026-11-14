import json

import pytest

from btwctl import cli, device
from btwctl import protocol as P
from tests.fakes import FakeDongle


@pytest.fixture
def dongle(monkeypatch):
    fake = FakeDongle()
    monkeypatch.setattr("btwctl.broker.default_transport", lambda *a, **k: fake)
    return fake


def run(capsys, *argv):
    cli.main(list(argv))
    return capsys.readouterr().out


def test_status_text(dongle, capsys):
    out = run(capsys, "status")
    assert "MOMENTUM 4 (ba:52:e9:09:80:c3)" in out and "High Quality" in out


def test_status_json(dongle, capsys):
    st = json.loads(run(capsys, "status", "--json"))
    assert st["aptx_mode"] == "HQ" and st["headset"]["name"] == "MOMENTUM 4"


def test_toggle(dongle, capsys):
    assert "Low Latency" in run(capsys, "toggle")
    assert dongle.settings[P.GET_APTX] == 0x20
    assert "High Quality" in run(capsys, "toggle")


def test_toggle_without_aptx_headset(capsys, monkeypatch):
    fake = FakeDongle(connected=False)
    monkeypatch.setattr("btwctl.broker.default_transport", lambda *a, **k: fake)
    with pytest.raises(SystemExit, match="Can't read the current aptX mode"):
        cli.main(["toggle"])


def test_paired(dongle, capsys):
    out = run(capsys, "paired")
    assert "MOMENTUM 4  [connected]" in out and out.count("(empty)") == 3
    assert run(capsys, "paired", "--names") == "MOMENTUM 4\n"
    assert json.loads(run(capsys, "paired", "--json"))[0]["connected"] is True


def test_pair_by_partial_name(capsys, monkeypatch):
    fake = FakeDongle(connected=False)
    monkeypatch.setattr("btwctl.broker.default_transport", lambda *a, **k: fake)
    out = run(capsys, "pair", "momentum")
    assert "Connected to MOMENTUM 4" in out
    assert (P.CMD_BT, bytes([P.BT_SCAN, P.SCAN_CONNECT]) + bytes.fromhex("ba52e90980c3")) in fake.sent


def test_pair_unknown_name(dongle):
    with pytest.raises(SystemExit, match="neither an address nor"):
        cli.main(["pair", "airpods"])


def test_pair_rejected(capsys, monkeypatch):
    fake = FakeDongle(connected=False, connect_status=0x80)
    monkeypatch.setattr("btwctl.broker.default_transport", lambda *a, **k: fake)
    with pytest.raises(SystemExit, match="refused to connect"):
        cli.main(["pair", "ba:52:e9:09:80:c3"])


def test_info_json(dongle, capsys):
    assert json.loads(run(capsys, "info", "--json")) == {
        "model": "Creative BT-W5", "usb_id": "041e:3130",
        "firmware": "1.2.230511.1220", "serial": "YDSA0180415000934F"}


def test_info_marks_untested_models(dongle, capsys, monkeypatch):
    monkeypatch.setattr("btwctl.device.dongle_info",
                        lambda: {"model": "Creative BT-W6", "usb_id": "041e:3132", "tested": False})
    out = run(capsys, "info")
    assert "Model    : Creative BT-W6 (041e:3132)  (reported to work by users" in out


def test_dump(dongle, capsys):
    out = run(capsys, "dump", "--json")
    d = json.loads(out)
    assert d["settings (6b 09 <key>)"]["03"] == "40"
    assert d["settings (6b 09 <key>)"]["00"] == "rejected"  # not emulated by the fake
    assert list(d["settings (6b 09 <key>)"]) == [f"{k:02x}" for k in range(0x20)]


def test_waybar(dongle, capsys):
    w = json.loads(run(capsys, "waybar"))
    assert w == {"text": "MOMENTUM 4 · HQ", "alt": "HQ", "class": "connected",
                 "tooltip": "Headset: MOMENTUM 4 (ba:52:e9:09:80:c3)\naptX Adaptive: HQ\nHFP: off\nAuto HFP: off"}


def test_waybar_absent(capsys, monkeypatch):
    class Missing(FakeDongle):
        def open(self):
            raise device.NotFound()
    monkeypatch.setattr("btwctl.broker.default_transport", lambda *a, **k: Missing())
    assert json.loads(run(capsys, "waybar"))["class"] == "absent"


def test_errors_become_messages(monkeypatch):
    class Busy(FakeDongle):
        def open(self):
            raise device.Busy()
    monkeypatch.setattr("btwctl.broker.default_transport", lambda *a, **k: Busy())
    with pytest.raises(SystemExit, match="in use by another program"):
        cli.main(["status"])


X3 = bytes.fromhex("68001abc5ceb")


def test_scan_lists_headsets(capsys, monkeypatch):
    from tests.fakes import scan_frame
    fake = FakeDongle(scan_frames=[scan_frame(X3, b"X3", -43), scan_frame(X3, b"X3", -40)])
    monkeypatch.setattr("btwctl.broker.default_transport", lambda *a, **k: fake)
    out = run(capsys, "scan", "0.3")
    assert "68:00:1a:bc:5c:eb   -43 dBm  X3" in out and "pair <address>" in out
    res = json.loads(run(capsys, "scan", "0.3", "--json"))
    assert res == {"found": [{"address": "68:00:1a:bc:5c:eb", "name": "X3", "rssi": -40}],
                   "connected": None}


def test_scan_reports_auto_connect(capsys, monkeypatch):
    """Seen on real hardware: the dongle connects to a headset in pairing mode on its own."""
    from tests.fakes import conn_frame
    fake = FakeDongle(scan_frames=[conn_frame(P.CONN_CONNECTING, X3, b"X3"),
                                   conn_frame(P.CONN_CONNECTED, X3, b"X3")])
    monkeypatch.setattr("btwctl.broker.default_transport", lambda *a, **k: fake)
    out = run(capsys, "scan", "5")
    assert "connected to X3 (68:00:1a:bc:5c:eb) by itself" in out
    assert "No devices found" not in out


def test_raw_rejects_bad_bytes(dongle, capsys):
    for bad in (["6b", "100"], ["zz"], ["6b", "-1"]):
        with pytest.raises(SystemExit):
            cli.main(["raw", *bad])
    with pytest.raises(SystemExit, match="payload bytes"):
        cli.main(["raw", "6b"] + ["00"] * 62)


def test_scan_zero_seconds_json(dongle, capsys):
    assert json.loads(run(capsys, "scan", "0", "--json")) == {"found": [], "connected": None}


def test_man_page_documents_every_command():
    import os
    import re
    page = open(os.path.join(os.path.dirname(__file__), "..", "docs", "btwctl.1")).read()
    page = page.replace("\\-", "-")
    sub = next(a for a in cli.build_parser()._actions if a.dest == "cmd")
    missing = [c for c in sub.choices if not re.search(rf"\b{re.escape(c)}\b", page)]
    assert not missing, f"not in docs/btwctl.1: {missing}"


def test_appstream_release_matches_version():
    """Software centres show the newest <release>; it must be this version (update it when releasing)."""
    import os
    import xml.etree.ElementTree as ET
    from btwctl import __version__
    path = os.path.join(os.path.dirname(__file__), "..", "contrib", "io.github.doodersrage.btwctl.metainfo.xml")
    newest = ET.parse(path).getroot().find("releases/release").get("version")
    assert newest == __version__, f"add a <release version=\"{__version__}\"> to {os.path.basename(path)}"
