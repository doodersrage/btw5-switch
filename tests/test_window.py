"""Drive the window against the fake dongle (offscreen, no display needed)."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    import PyQt6.QtWidgets  # noqa: F401
except ImportError:
    pytest.importorskip("PySide6.QtWidgets")

from btw5 import protocol as P  # noqa: E402
from btw5 import device  # noqa: E402
from btw5.qt import QtWidgets  # noqa: E402
from btw5.window import Window  # noqa: E402
from tests.fakes import FakeDongle, conn_frame, scan_frame  # noqa: E402

X3 = bytes.fromhex("68001abc5ceb")
YES = QtWidgets.QMessageBox.StandardButton.Yes


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def make(app, monkeypatch):
    """make(fake) -> a shown Window talking to that fake dongle."""
    windows = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: YES)

    def make(fake):
        monkeypatch.setattr("btw5.broker.default_transport", lambda *a, **k: fake)
        w = Window()
        w.show()
        windows.append(w)
        wait(app, lambda: not w.worker.busy and w.state is not None or "not found" in w.title.text())
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
    wait(app, lambda: "Connected to New headset" in w.statusBar().currentMessage())
    assert not w.scanning and w.scan_btn.text() == "Scan"
    assert (P.CMD_BT, bytes([P.BT_SCAN, P.SCAN_CONNECT]) + X3) in fake.sent


def test_scan_auto_connect(app, make):
    fake = FakeDongle(scan_frames=[conn_frame(P.CONN_CONNECTED, X3, b"X3")])
    w = make(fake)
    w.toggle_scan()   # asks to disconnect the current headset; patched to Yes
    wait(app, lambda: "connected to X3" in w.statusBar().currentMessage())
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
    wait(app, lambda: "can't reach X3" in w.statusBar().currentMessage())


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
    assert w.title.text() == "BT-W5 not found"
    assert not w.hq.isEnabled() and not w.scan_btn.isEnabled() and not w.forget_btn.isEnabled()
