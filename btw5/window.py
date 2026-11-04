"""The BT-W5 Switch window: status, audio settings, paired headsets, pairing, device info.

Runs standalone ('btw5-switch gui') or opened from the tray icon, sharing its
worker. All dongle I/O goes through the Worker thread (see qt.py).
"""

import json
import sys
import threading

from . import protocol as P
from .device import Busy, NotFound, read_dump
from .qt import QtCore, QtGui, QtWidgets, Signal, Worker, run_app, snapshot, theme_icon

Qt = QtCore.Qt
W = QtWidgets

REFRESH_MS = 10_000
CONNECT_TIMEOUT = 15.0


def signal_bars(rssi):
    """-43 dBm -> '▂▄▆█' style bars (4 = strong)."""
    n = 4 if rssi >= -55 else 3 if rssi >= -67 else 2 if rssi >= -78 else 1
    return "▂▄▆█"[:n] + "·" * (4 - n)


def hint(text):
    label = W.QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)  # greyed out, like help text
    return label


class Window(W.QMainWindow):
    state_changed = Signal(object)   # every fresh status snapshot (the tray listens)

    def __init__(self, worker=None, standalone=True):
        super().__init__()
        self.worker = worker or Worker()
        self.own_worker = worker is None
        self.standalone = standalone
        self.state = None
        self.info_loaded = False
        self.scanning = False
        self.closed = False
        self.stop_scan = threading.Event()
        self.scan_rows = {}   # address bytes -> table row

        self.setWindowTitle("BT-W5 Switch")
        self.setWindowIcon(theme_icon())
        self.build()
        self.worker.busy_changed.connect(lambda _busy: self.update_enabled())
        self.update_enabled()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.auto_refresh)
        self.timer.start(REFRESH_MS)
        self.refresh()

    # --- layout --------------------------------------------------------------

    def build(self):
        central = W.QWidget()
        root = W.QVBoxLayout(central)
        root.setSpacing(12)
        self.setCentralWidget(central)

        # header: icon, headset name and state, refresh
        head = W.QHBoxLayout()
        icon = W.QLabel()
        icon.setPixmap(theme_icon().pixmap(48, 48))
        head.addWidget(icon)
        titles = W.QVBoxLayout()
        self.title = W.QLabel("BT-W5")
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.title.setFont(font)
        self.subtitle = W.QLabel("Reading the dongle…")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.refresh_btn = W.QToolButton()
        self.refresh_btn.setIcon(theme_icon("view-refresh"))
        self.refresh_btn.setToolTip("Refresh")
        self.refresh_btn.clicked.connect(lambda: self.refresh())
        head.addWidget(self.refresh_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        # audio settings
        self.audio = W.QGroupBox("Audio")
        form = W.QFormLayout(self.audio)
        modes = W.QHBoxLayout()
        self.hq = W.QRadioButton("High Quality")
        self.ll = W.QRadioButton("Low Latency")
        self.hq.setToolTip("Best sound; more delay")
        self.ll.setToolTip("Less delay for games and video; slightly lower quality")
        self.hq.clicked.connect(lambda: self.set_aptx("hq"))
        self.ll.clicked.connect(lambda: self.set_aptx("ll"))
        modes.addWidget(self.hq)
        modes.addWidget(self.ll)
        modes.addStretch()
        form.addRow("aptX Adaptive:", modes)
        self.aptx_hint = hint("Needs a connected headset that supports aptX Adaptive.")
        form.addRow("", self.aptx_hint)
        self.hfp = W.QCheckBox("Headset microphone (HFP)")
        self.hfp.clicked.connect(self.set_hfp)
        form.addRow(self.hfp)
        form.addRow(hint("Changing this makes the dongle reconnect, so audio drops for a few seconds."))
        self.auto_hfp = W.QCheckBox("Switch to the microphone automatically when an app uses it")
        self.auto_hfp.clicked.connect(self.set_auto_hfp)
        form.addRow(self.auto_hfp)
        root.addWidget(self.audio)

        # paired headsets
        self.paired = W.QGroupBox("Paired headsets")
        box = W.QVBoxLayout(self.paired)
        self.slots = self.make_table(["Headset", "Address", ""], P.PAIR_SLOTS)
        self.fit_rows(self.slots, P.PAIR_SLOTS)
        box.addWidget(self.slots)
        row = W.QHBoxLayout()
        self.disconnect_btn = W.QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect)
        self.forget_btn = W.QPushButton(theme_icon("edit-delete"), "Forget all…")
        self.forget_btn.clicked.connect(self.forget_all)
        row.addWidget(self.disconnect_btn)
        row.addStretch()
        row.addWidget(self.forget_btn)
        box.addLayout(row)
        root.addWidget(self.paired)

        # pairing a new headset
        self.pairing = W.QGroupBox("Pair a new headset")
        box = W.QVBoxLayout(self.pairing)
        box.addWidget(hint("Put the headset in pairing mode, then scan. Scanning disconnects the "
                           "current headset. If the dongle finds a headset in pairing mode it "
                           "often connects to it right away."))
        row = W.QHBoxLayout()
        self.scan_btn = W.QPushButton(theme_icon("edit-find", "system-search"), "Scan")
        self.scan_btn.clicked.connect(self.toggle_scan)
        self.scan_secs = W.QSpinBox()
        self.scan_secs.setRange(5, 60)
        self.scan_secs.setValue(15)
        self.scan_secs.setSuffix(" s")
        self.scan_progress = W.QProgressBar()
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setRange(0, 1000)
        row.addWidget(self.scan_btn)
        row.addWidget(self.scan_secs)
        row.addWidget(self.scan_progress, 1)
        box.addLayout(row)
        self.found = self.make_table(["Headset", "Address", "Signal", ""], 0)
        self.found.setMinimumHeight(110)
        box.addWidget(self.found)
        root.addWidget(self.pairing, 1)

        # device info
        self.device = W.QGroupBox("Dongle")
        form = W.QFormLayout(self.device)
        self.firmware = W.QLabel("–")
        self.serial = W.QLabel("–")
        for label in (self.firmware, self.serial):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Firmware:", self.firmware)
        form.addRow("Serial number:", self.serial)
        self.diag_btn = W.QPushButton(theme_icon("edit-copy"), "Copy diagnostics")
        self.diag_btn.setToolTip("Copy every readable register as JSON, for bug reports")
        self.diag_btn.clicked.connect(self.copy_diagnostics)
        row = W.QHBoxLayout()
        row.addWidget(self.diag_btn)
        row.addStretch()
        form.addRow("", row)
        root.addWidget(self.device)

        self.scan_timer = QtCore.QElapsedTimer()
        self.scan_tick = QtCore.QTimer(self)
        self.scan_tick.timeout.connect(self.update_scan_progress)
        self.resize(560, 760)

    @staticmethod
    def fit_rows(table, rows):
        """Size a table to show exactly `rows` rows without scrolling."""
        table.verticalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Fixed)
        h = (table.horizontalHeader().sizeHint().height()
             + rows * table.verticalHeader().defaultSectionSize() + 2 * table.frameWidth())
        table.setFixedHeight(h)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def make_table(self, headers, rows):
        t = W.QTableWidget(rows, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionMode(W.QAbstractItemView.SelectionMode.NoSelection)
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        h = t.horizontalHeader()
        h.setSectionResizeMode(0, W.QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(headers)):
            h.setSectionResizeMode(i, W.QHeaderView.ResizeMode.ResizeToContents)
        return t

    # --- state -----------------------------------------------------------------

    def refresh(self, with_info=None):
        if self.closed:
            return
        if with_info is None:
            with_info = not self.info_loaded
        self.worker.run(lambda: snapshot(with_info=with_info), self.on_state)

    def auto_refresh(self):
        if self.isVisible() and not self.worker.busy:
            self.refresh()

    def on_state(self, r):
        if self.closed:
            return
        if isinstance(r, Busy):
            self.say("The dongle is busy in another program; retrying…")
            return
        if isinstance(r, Exception):
            self.state = None
            if isinstance(r, NotFound):
                self.title.setText("BT-W5 not found")
                self.subtitle.setText("Plug in the dongle (it also disappears briefly after HFP changes).")
            else:
                self.title.setText("BT-W5")
                self.subtitle.setText(str(r))
            self.update_enabled()
            return

        self.state = r
        if "firmware" in r:
            self.info_loaded = True
            self.firmware.setText(r["firmware"] or "unknown")
            self.serial.setText(r["serial"] or "unknown")

        h = r["headset"]
        if h:
            self.title.setText(h["name"] or h["address"])
            mode = {"LL": "aptX Adaptive Low Latency", "HQ": "aptX Adaptive High Quality"}.get(
                r["aptx_mode"], "no aptX Adaptive")
            self.subtitle.setText(f"Connected · {mode} · {h['address']}")
        else:
            self.title.setText("No headset connected")
            last = r["last_headset"]
            self.subtitle.setText(f"Last: {last['name'] or last['address']} ({r['connection']})"
                                  if last else "Connect a paired headset or pair a new one below.")

        self.hq.setChecked(r["aptx_mode"] == "HQ")
        self.ll.setChecked(r["aptx_mode"] == "LL")
        self.aptx_hint.setVisible(r["aptx_mode"] is None)
        self.hfp.setChecked(bool(r["hfp"]))
        self.auto_hfp.setChecked(bool(r["auto_hfp"]))
        self.fill_slots(r)
        self.update_enabled()
        self.state_changed.emit(r)

    def fill_slots(self, r):
        current = r["headset"]["address"] if r["headset"] else None
        self.slots.setRowCount(len(r["slots"]))
        for row, s in enumerate(r["slots"]):
            empty = s["address"] == P.ZERO_ADDR
            addr = P.fmt_addr(s["address"])
            name = W.QTableWidgetItem("(empty)" if empty else (s["name"] or addr))
            if empty:
                name.setForeground(self.palette().color(QtGui.QPalette.ColorRole.PlaceholderText))
            self.slots.setItem(row, 0, name)
            self.slots.setItem(row, 1, W.QTableWidgetItem("" if empty else addr))
            if empty:
                self.slots.removeCellWidget(row, 2)
                self.slots.setItem(row, 2, W.QTableWidgetItem(""))
            elif addr == current:
                self.slots.removeCellWidget(row, 2)
                self.slots.setItem(row, 2, W.QTableWidgetItem("✓ Connected"))
            else:
                self.slots.setItem(row, 2, W.QTableWidgetItem(""))
                btn = W.QPushButton("Connect")
                btn.clicked.connect(lambda _=False, a=s["address"], n=s["name"] or addr: self.connect_to(a, n))
                self.slots.setCellWidget(row, 2, btn)

    def update_enabled(self):
        ok = self.state is not None
        idle = not self.worker.busy
        h = ok and self.state["headset"] is not None
        aptx = ok and self.state["aptx_mode"] is not None
        for w in (self.hq, self.ll):
            w.setEnabled(idle and aptx)
        self.hfp.setEnabled(idle and ok and self.state["hfp"] is not None)
        self.auto_hfp.setEnabled(idle and ok and self.state["auto_hfp"] is not None)
        self.slots.setEnabled(idle and ok)
        self.disconnect_btn.setEnabled(idle and h)
        self.forget_btn.setEnabled(idle and ok)
        self.diag_btn.setEnabled(idle and ok)
        self.refresh_btn.setEnabled(idle)
        # while scanning the button is "Stop"; the Pair buttons stop the scan and connect
        self.scan_btn.setEnabled(self.scanning or (idle and ok))
        self.scan_secs.setEnabled(not self.scanning)
        self.found.setEnabled(self.scanning or (idle and ok))

    def say(self, text, ms=6000):
        self.statusBar().showMessage(text, ms)

    # --- actions -----------------------------------------------------------------

    def act(self, doing, fn, done=None, refresh_after=(400,)):
        """Run fn(d) on the worker; report failures; refresh afterwards."""
        self.say(doing + "…", 0)

        def finished(r):
            if self.closed:
                return
            if isinstance(r, Exception):
                self.say(f"{doing} failed: {r}", 10000)
            elif done:
                done(r)
            elif isinstance(r, int) and r != P.STATUS_OK:
                self.say(f"The dongle refused (status 0x{r:02x}). Is a compatible headset connected?", 10000)
            else:
                self.statusBar().clearMessage()
            for ms in refresh_after:
                QtCore.QTimer.singleShot(ms, self.refresh)
        self.worker.with_device(fn, finished)
        self.update_enabled()

    def set_aptx(self, mode):
        self.act(f"Switching to aptX {P.APTX_NAMES[P.APTX[mode]]}",
                 lambda d: d.set(P.SET_APTX, P.APTX[mode]))

    def set_hfp(self, on):
        # the dongle re-enumerates: refresh once it's back
        self.act(f"Turning the headset microphone {'on' if on else 'off'}",
                 lambda d: d.set(P.SET_HFP, P.HFP_VALUES["on" if on else "off"]),
                 refresh_after=(4000, 8000, 12000))

    def set_auto_hfp(self, on):
        self.act(f"Turning automatic microphone switching {'on' if on else 'off'}",
                 lambda d: d.set(P.SET_AUTO_HFP, P.AUTO_HFP_VALUES["on" if on else "off"]))

    def connect_to(self, addr, label):
        self.stop_scan.set()   # if a scan is running, end it so the connect can go next

        def done(r):
            result, name = r
            self.say({
                "connected": f"Connected to {name or label}.",
                "rejected": f"The dongle can't reach {label}. Make sure it's on and not connected "
                            "to another device, or put it in pairing mode and scan.",
                "disconnected": f"{label} disconnected again.",
                "timeout": f"No connection confirmed for {label} yet.",
            }[result], 12000)
        self.act(f"Connecting to {label}", lambda d: d.connect_and_wait(addr, CONNECT_TIMEOUT), done)

    def disconnect(self):
        self.act("Disconnecting", lambda d: d.start_scan(),
                 lambda r: self.say("Disconnected. (The dongle also starts looking for headsets.)"),
                 refresh_after=(1500,))

    def forget_all(self):
        answer = W.QMessageBox.question(
            self, "Forget all headsets?",
            "The dongle will forget every paired headset and reconnect on USB.\n"
            "You'll have to pair your headsets again.",
            W.QMessageBox.StandardButton.Yes | W.QMessageBox.StandardButton.Cancel,
            W.QMessageBox.StandardButton.Cancel)
        if answer == W.QMessageBox.StandardButton.Yes:
            self.act("Forgetting all headsets", lambda d: d.forget_all(),
                     lambda r: self.say("All headsets forgotten."), refresh_after=(4000, 8000))

    def copy_diagnostics(self):
        def done(out):
            W.QApplication.clipboard().setText(json.dumps(out, indent=2))
            self.say("Diagnostics copied to the clipboard.")
        self.act("Reading every register", read_dump, done, refresh_after=())

    # --- scanning ----------------------------------------------------------------

    def toggle_scan(self):
        if self.scanning:
            self.stop_scan.set()
            return
        h = self.state and self.state["headset"]
        if h:
            answer = W.QMessageBox.question(
                self, "Disconnect headset?",
                f"Scanning disconnects {h['name'] or h['address']}. Continue?",
                W.QMessageBox.StandardButton.Yes | W.QMessageBox.StandardButton.Cancel)
            if answer != W.QMessageBox.StandardButton.Yes:
                return
        self.start_scan(self.scan_secs.value())

    def start_scan(self, seconds):
        self.scanning = True
        self.stop_scan.clear()
        self.scan_rows.clear()
        self.found.setRowCount(0)
        self.scan_btn.setText("Stop")
        self.scan_seconds = seconds
        self.scan_timer.start()
        self.scan_tick.start(100)
        self.say("Scanning… put the headset in pairing mode.", 0)
        auto = {}

        def scan(d, report):
            for ev in d.scan_events(seconds, should_stop=self.stop_scan.is_set):
                report(ev)

        def progress(ev):
            if ev[0] == "found":
                self.add_found(*ev[1:])
            else:
                auto["conn"] = ev

        def done(r):
            self.scanning = False
            self.scan_tick.stop()
            self.scan_progress.setValue(0)
            self.scan_btn.setText("Scan")
            if self.closed:
                return
            if isinstance(r, Exception):
                self.say(f"Scan failed: {r}", 10000)
            elif "conn" in auto:
                _, addr, name = auto["conn"]
                self.say(f"The dongle connected to {P.headset_label(addr, name)} by itself.", 12000)
            elif self.scan_rows:
                self.say("Click Pair while the headset is still in pairing mode.", 12000)
            else:
                self.say("No headsets found. Is the headset in pairing mode?", 12000)
            self.refresh()
            self.update_enabled()

        self.worker.with_device(scan, done, progress)
        self.update_enabled()

    def update_scan_progress(self):
        frac = self.scan_timer.elapsed() / (self.scan_seconds * 1000)
        self.scan_progress.setValue(int(min(frac, 1) * 1000))

    def add_found(self, addr, name, rssi):
        row = self.scan_rows.get(addr)
        label = name or P.fmt_addr(addr)
        if row is None:
            row = self.scan_rows[addr] = self.found.rowCount()
            self.found.insertRow(row)
            self.found.setItem(row, 0, W.QTableWidgetItem(label))
            self.found.setItem(row, 1, W.QTableWidgetItem(P.fmt_addr(addr)))
            btn = W.QPushButton("Pair")
            btn.clicked.connect(lambda _=False, a=addr, n=label: self.connect_to(a, n))
            self.found.setCellWidget(row, 3, btn)
        sig = W.QTableWidgetItem(f"{signal_bars(rssi)}  {rssi} dBm")
        sig.setToolTip(f"{rssi} dBm")
        self.found.setItem(row, 2, sig)

    # --- window ------------------------------------------------------------------

    def present(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh()

    def closeEvent(self, event):
        if self.standalone:
            self.shutdown()
            event.accept()
        else:
            self.stop_scan.set()
            self.hide()       # the tray keeps running
            event.ignore()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.scan_tick.stop()
        self.stop_scan.set()
        if self.own_worker:
            self.worker.shutdown()


def main():
    app = W.QApplication(sys.argv[:1])
    app.setApplicationName("btw5-switch")
    app.setApplicationDisplayName("BT-W5 Switch")
    app.setDesktopFileName("btw5-switch")
    w = Window()
    w.show()

    def cleanup():
        w.shutdown()
        app.quit()
    run_app(app, cleanup)


if __name__ == "__main__":
    main()
