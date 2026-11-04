"""Drive the window against the fake dongle (offscreen, no display needed)."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    import PyQt6.QtWidgets  # noqa: F401
except ImportError:
    pytest.importorskip("PySide6.QtWidgets")

from btwctl import protocol as P  # noqa: E402
from btwctl import device  # noqa: E402
from btwctl.qt import QtWidgets  # noqa: E402
from btwctl.window import Window  # noqa: E402
from tests.fakes import FakeDongle, conn_frame, scan_frame  # noqa: E402

X3 = bytes.fromhex("68001abc5ceb")
YES = QtWidgets.QMessageBox.StandardButton.Yes


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the real config or systemd from tests."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("btwctl.service.is_active", lambda: False)
    monkeypatch.setattr("btwctl.service.enable", lambda: (True, "Background service started."))


@pytest.fixture
def make(app, monkeypatch):
    """make(fake) -> a shown Window talking to that fake dongle."""
    windows = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: YES)

    def make(fake):
        monkeypatch.setattr("btwctl.broker.default_transport", lambda *a, **k: fake)
        w = Window()
        w.show()
        windows.append(w)
        wait(app, lambda: not w.worker.busy and (w.state is not None or w.title.text() == "No dongle found"))
        return w
    yield make
    for w in windows:
        w.shutdown()
        w.deleteLater()


def wait(app, cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the window")


def test_shows_status(make):
    w = make(FakeDongle())
    assert w.title.text() == "MOMENTUM 4"
    assert "High Quality" in w.subtitle.text()
    assert w.hq.isChecked() and not w.hfp.isChecked()
    assert w.slots.item(0, 2).text() == "✓ Connected"
    assert [w.slots.item(r, 0).text() for r in range(1, 4)] == ["(empty)"] * 3
    assert w.firmware.text() == "1.2.230511.1220"
    assert w.disconnect_btn.isEnabled()


def test_switch_aptx(app, make):
    fake = FakeDongle()
    w = make(fake)
    w.ll.click()
    wait(app, lambda: fake.settings[P.GET_APTX] == 0x20)
    wait(app, lambda: "Low Latency" in w.subtitle.text())   # after the follow-up refresh
    assert w.ll.isChecked() and not w.hq.isChecked()


def test_scan_then_pair(app, make):
    fake = FakeDongle(scan_frames=[scan_frame(X3, b"X3", -43)])
    w = make(fake)
    w.start_scan(5)
    wait(app, lambda: w.found.rowCount() == 1)
    assert w.found.item(0, 0).text() == "X3" and "-43 dBm" in w.found.item(0, 2).text()
    w.found.cellWidget(0, 3).click()          # Pair: stops the scan, then connects
    wait(app, lambda: "Connected to New headset" in w.message())
    assert not w.scanning and w.scan_btn.text() == "Scan"
    assert (P.CMD_BT, bytes([P.BT_SCAN, P.SCAN_CONNECT]) + X3) in fake.sent


def test_scan_auto_connect(app, make):
    fake = FakeDongle(scan_frames=[conn_frame(P.CONN_CONNECTED, X3, b"X3")])
    w = make(fake)
    w.toggle_scan()   # asks to disconnect the current headset; patched to Yes
    wait(app, lambda: "connected to X3" in w.message())
    assert w.found.rowCount() == 0


def test_stop_scan_is_prompt(app, make):
    w = make(FakeDongle())
    w.start_scan(60)
    wait(app, lambda: w.scanning)
    t0 = time.monotonic()
    w.toggle_scan()   # "Stop"
    wait(app, lambda: not w.scanning)
    assert time.monotonic() - t0 < 2


def test_connect_rejected_message(app, make):
    fake = FakeDongle(connect_status=P.STATUS_REJECTED)
    w = make(fake)
    w.connect_to(X3, "X3")
    wait(app, lambda: "can't reach X3" in w.message())


def test_forget_all(app, make):
    fake = FakeDongle()
    w = make(fake)
    w.forget_btn.click()
    wait(app, lambda: (P.CMD_BT, bytes([P.BT_FORGET, 0xff])) in fake.sent)


def test_dongle_missing(make):
    class Missing(FakeDongle):
        def open(self):
            raise device.NotFound()
    w = make(Missing())
    assert w.title.text() == "No dongle found"
    assert not w.hq.isEnabled() and not w.scan_btn.isEnabled() and not w.forget_btn.isEnabled()


def test_long_messages_wrap_instead_of_being_cut_off(app, make):
    """The status bar used to cut long hints in half (seen on real hardware)."""
    w = make(FakeDongle(connect_status=P.STATUS_REJECTED))
    w.connect_to(X3, "MOMENTUM 4")
    wait(app, lambda: "can't reach" in w.message())
    label = w.banner_text
    assert label.wordWrap()
    one_line = label.fontMetrics().height()
    assert label.fontMetrics().horizontalAdvance(label.text()) > label.width()  # too long for one line...
    wait(app, lambda: label.height() >= 2 * one_line, timeout=3)                # ...so it gets more lines
    assert label.width() <= w.width()


def test_message_banner_dismiss(app, make):
    w = make(FakeDongle())
    w.say("hello", 0)
    assert w.message() == "hello"
    w.banner.findChild(QtWidgets.QToolButton).click()
    assert w.message() == ""



def test_remember_settings_for_headset(app, make):
    from btwctl.config import Config
    fake = FakeDongle()
    w = make(fake)
    assert "MOMENTUM 4" in w.remember.text() and not w.remember.isChecked()
    w.remember.click()
    prefs = Config().preferences("ba:52:e9:09:80:c3")
    assert prefs == {"aptx": "hq", "hfp": "off", "auto-hfp": "off"}
    w.ll.click()                                   # changes keep the preference in step
    wait(app, lambda: Config().preferences("ba:52:e9:09:80:c3").get("aptx") == "ll")
    wait(app, lambda: not w.worker.busy)
    w.remember.click()                             # unticking forgets them
    assert Config().preferences("ba:52:e9:09:80:c3") == {}


def test_game_mode_controls(app, make):
    from btwctl.config import Config
    w = make(FakeDongle())
    assert not w.game_mode.isChecked() and "SteamLaunch" in w.game_apps.text()
    w.game_mode.click()
    assert Config().game_mode
    w.game_apps.setText("SteamLaunch, minecraft")
    w.game_apps.editingFinished.emit()
    assert Config().game_apps == ["SteamLaunch", "minecraft"]


def test_service_button(app, make, monkeypatch):
    w = make(FakeDongle())
    assert not w.service_btn.isHidden() and "isn't running" in w.service_label.text()
    monkeypatch.setattr("btwctl.service.is_active", lambda: True)
    w.service_btn.click()
    wait(app, lambda: "service started" in w.message())
    wait(app, lambda: w.service_btn.isHidden() and "running" in w.service_label.text())


def test_audio_follow_checkbox(app, make, monkeypatch):
    from btwctl.config import Config
    monkeypatch.setattr("btwctl.audio.available", lambda: True)
    w = make(FakeDongle())
    assert not w.audio_follow.isChecked()
    w.audio_follow.click()
    assert Config().audio_follow and "follow the headset" in w.message()


def test_tray_leaves_connect_notifications_to_the_service(app, monkeypatch):
    """The tray and the background service both used to announce every (dis)connect."""
    from btwctl import tray as tray_mod
    shown = []
    t = tray_mod.Tray.__new__(tray_mod.Tray)     # just the parts on_state needs
    t.state = None
    t.icon = type("Icon", (), {"showMessage": lambda self, *a: shown.append(a[1]),
                               "setToolTip": lambda self, *a: None})()
    t.build_menu = lambda: None
    base = {"aptx_mode": "HQ", "hfp": False, "auto_hfp": False, "paired": []}
    on = dict(base, headset={"name": "MOMENTUM 4", "address": "ba:52:e9:09:80:c3"})
    off = dict(base, headset=None)
    for service_running, expect in ((True, 0), (False, 1)):
        shown.clear()
        t.state = None
        t.on_state(dict(off, service=service_running))
        t.on_state(dict(on, service=service_running))
        assert len(shown) == expect, (service_running, shown)


def test_remember_during_a_game_doesnt_store_low_latency(app, make, monkeypatch):
    from btwctl.config import Config
    monkeypatch.setattr("btwctl.automation.game_running", lambda config, proc="/proc": True)
    w = make(FakeDongle(aptx=0x20))                       # game mode forced Low Latency
    w.remember.click()
    assert "aptx" not in Config().preferences("ba:52:e9:09:80:c3")
