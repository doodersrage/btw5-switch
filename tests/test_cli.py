import json

import pytest

from btw5 import cli, device
from btw5 import protocol as P
from tests.fakes import FakeDongle


@pytest.fixture
def dongle(monkeypatch):
    fake = FakeDongle()
    monkeypatch.setattr("btw5.broker.default_transport", lambda *a, **k: fake)
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
    monkeypatch.setattr("btw5.broker.default_transport", lambda *a, **k: fake)
    with pytest.raises(SystemExit, match="Can't read the current aptX mode"):
        cli.main(["toggle"])


def test_paired(dongle, capsys):
    out = run(capsys, "paired")
    assert "MOMENTUM 4  [connected]" in out and out.count("(empty)") == 3
    assert run(capsys, "paired", "--names") == "MOMENTUM 4\n"
    assert json.loads(run(capsys, "paired", "--json"))[0]["connected"] is True


def test_pair_by_partial_name(capsys, monkeypatch):
    fake = FakeDongle(connected=False)
    monkeypatch.setattr("btw5.broker.default_transport", lambda *a, **k: fake)
    out = run(capsys, "pair", "momentum")
    assert "Connected to MOMENTUM 4" in out
    assert (P.CMD_BT, bytes([P.BT_SCAN, P.SCAN_CONNECT]) + bytes.fromhex("ba52e90980c3")) in fake.sent


def test_pair_unknown_name(dongle):
    with pytest.raises(SystemExit, match="neither an address nor"):
        cli.main(["pair", "airpods"])


def test_pair_rejected(capsys, monkeypatch):
    fake = FakeDongle(connected=False, connect_status=0x80)
    monkeypatch.setattr("btw5.broker.default_transport", lambda *a, **k: fake)
    with pytest.raises(SystemExit, match="refused to connect"):
        cli.main(["pair", "ba:52:e9:09:80:c3"])


def test_info_json(dongle, capsys):
    assert json.loads(run(capsys, "info", "--json")) == {"firmware": "1.2.230511.1220",
                                                          "serial": "YDSA0180415000934F"}


def test_dump(dongle, capsys):
    out = run(capsys, "dump", "--json")
    d = json.loads(out)
    assert d["settings (6b 09 <key>)"]["03"] == "40"
    assert d["settings (6b 09 <key>)"]["00"] == "rejected"  # not emulated by the fake


def test_waybar(dongle, capsys):
    w = json.loads(run(capsys, "waybar"))
    assert w == {"text": "MOMENTUM 4 · HQ", "alt": "HQ", "class": "connected",
                 "tooltip": "Headset: MOMENTUM 4 (ba:52:e9:09:80:c3)\naptX Adaptive: HQ\nHFP: off\nAuto HFP: off"}


def test_waybar_absent(capsys, monkeypatch):
    class Missing(FakeDongle):
        def open(self):
            raise device.NotFound()
    monkeypatch.setattr("btw5.broker.default_transport", lambda *a, **k: Missing())
    assert json.loads(run(capsys, "waybar"))["class"] == "absent"


def test_errors_become_messages(monkeypatch):
    class Busy(FakeDongle):
        def open(self):
            raise device.Busy()
    monkeypatch.setattr("btw5.broker.default_transport", lambda *a, **k: Busy())
    with pytest.raises(SystemExit, match="in use by another program"):
        cli.main(["status"])
