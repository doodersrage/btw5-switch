"""The BT-W Control window: status, audio settings, paired headsets, pairing, device info.

Runs standalone ('btwctl gui') or opened from the tray icon, sharing its
worker. All dongle I/O goes through the Worker thread (see qt.py).
"""

import json
import sys
import threading

from . import protocol as P
from . import service
from .config import Config
from .device import Busy, NotFound, read_dump
from .i18n import tr
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
        self.config = Config()
        self.info_loaded = False
        self.scanning = False
        self.closed = False
        self.stop_scan = threading.Event()
        self.scan_rows = {}   # address bytes -> table row

        self.setWindowTitle(tr("BT-W Control"))
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
        self.title = W.QLabel(tr("BT-W Control"))
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.title.setFont(font)
        self.subtitle = W.QLabel(tr("Reading the dongle…"))
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.refresh_btn = W.QToolButton()
        self.refresh_btn.setIcon(theme_icon("view-refresh"))
        self.refresh_btn.setToolTip(tr("Refresh"))
        self.refresh_btn.clicked.connect(lambda: self.refresh())
        head.addWidget(self.refresh_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        # messages: a banner that wraps, so long hints aren't cut off like in a status bar
        self.banner = W.QFrame()
        self.banner.setObjectName("banner")
        row = W.QHBoxLayout(self.banner)
        row.setContentsMargins(10, 6, 4, 6)
        self.banner_text = W.QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.banner_text, 1)
        close = W.QToolButton()
        close.setIcon(theme_icon("window-close", "dialog-close"))
        close.setAutoRaise(True)
        close.setToolTip(tr("Dismiss"))
        close.clicked.connect(self.banner.hide)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.banner.hide()
        root.addWidget(self.banner)

        # the sections scroll (header and messages stay put), so it fits small screens
        content = W.QWidget()
        sections = W.QVBoxLayout(content)
        sections.setContentsMargins(0, 0, 0, 0)
        sections.setSpacing(12)
        self.scroll = W.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)
        self.banner_timer = QtCore.QTimer(self)
        self.banner_timer.setSingleShot(True)
        self.banner_timer.timeout.connect(self.banner.hide)

        # audio settings
        self.audio = W.QGroupBox(tr("Audio"))
        form = W.QFormLayout(self.audio)
        modes = W.QHBoxLayout()
        self.hq = W.QRadioButton(tr("High Quality"))
        self.ll = W.QRadioButton(tr("Low Latency"))
        self.hq.setToolTip(tr("Best sound; more delay"))
        self.ll.setToolTip(tr("Less delay for games and video; slightly lower quality"))
        self.hq.clicked.connect(lambda: self.set_aptx("hq"))
        self.ll.clicked.connect(lambda: self.set_aptx("ll"))
        modes.addWidget(self.hq)
        modes.addWidget(self.ll)
        modes.addStretch()
        form.addRow(tr("aptX Adaptive:"), modes)
        self.aptx_hint = hint(tr("Needs a connected headset that supports aptX Adaptive."))
        form.addRow("", self.aptx_hint)
        self.hfp = W.QCheckBox(tr("Headset microphone (HFP)"))
        self.hfp.clicked.connect(self.set_hfp)
        form.addRow(self.hfp)
        form.addRow(hint(tr("Changing this makes the dongle reconnect, so audio drops for a few seconds.")))
        self.auto_hfp = W.QCheckBox(tr("Switch to the microphone automatically when an app uses it"))
        self.auto_hfp.clicked.connect(self.set_auto_hfp)
        form.addRow(self.auto_hfp)
        self.remember = W.QCheckBox(tr("Use these settings whenever this headset connects"))
        self.remember.setToolTip(tr("Saved per headset and applied by the background service"))
        self.remember.clicked.connect(self.set_remember)
        form.addRow(self.remember)
        sections.addWidget(self.audio)

        # automation
        self.automation = W.QGroupBox(tr("Automation"))
        form = W.QFormLayout(self.automation)
        self.game_mode = W.QCheckBox(tr("Use Low Latency while a game is running, then switch back"))
        self.game_mode.clicked.connect(self.set_game_mode)
        form.addRow(self.game_mode)
        self.game_apps = W.QLineEdit()
        self.game_apps.setToolTip(tr("A process counts as a game if its command line contains one of these "
                                  "(comma-separated, case doesn't matter)"))
        self.game_apps.editingFinished.connect(self.set_game_apps)
        form.addRow(tr("Games:"), self.game_apps)
        self.audio_follow = W.QCheckBox(tr("Switch sound to the dongle while a headset is connected, and back after"))
        self.audio_follow.setToolTip(tr("Sets the default output (and, with the headset microphone on, the default "
                                     "microphone) through PipeWire"))
        self.audio_follow.clicked.connect(self.set_audio_follow)
        form.addRow(self.audio_follow)
        row = W.QHBoxLayout()
        self.service_label = W.QLabel()
        self.service_label.setWordWrap(True)
        self.service_btn = W.QPushButton(tr("Start background service"))
        self.service_btn.setToolTip(f"Runs: {service.ENABLE_CMD}")
        self.service_btn.clicked.connect(self.start_service)
        row.addWidget(self.service_label, 1)
        row.addWidget(self.service_btn)
        form.addRow(row)
        sections.addWidget(self.automation)

        # paired headsets
        self.paired = W.QGroupBox(tr("Paired headsets"))
        box = W.QVBoxLayout(self.paired)
        self.slots = self.make_table([tr("Headset"), tr("Address"), ""], P.PAIR_SLOTS)
        self.fit_rows(self.slots, P.PAIR_SLOTS)
        box.addWidget(self.slots)
        row = W.QHBoxLayout()
        self.disconnect_btn = W.QPushButton(tr("Disconnect"))
        self.disconnect_btn.clicked.connect(self.disconnect)
        self.forget_btn = W.QPushButton(theme_icon("edit-delete"), tr("Forget all…"))
        self.forget_btn.clicked.connect(self.forget_all)
        row.addWidget(self.disconnect_btn)
        row.addStretch()
        row.addWidget(self.forget_btn)
        box.addLayout(row)
        sections.addWidget(self.paired)

        # pairing a new headset
        self.pairing = W.QGroupBox(tr("Pair a new headset"))
        box = W.QVBoxLayout(self.pairing)
        box.addWidget(hint(tr("Put the headset in pairing mode, then scan. Scanning disconnects the "
                           "current headset. If the dongle finds a headset in pairing mode it "
                           "often connects to it right away.")))
        row = W.QHBoxLayout()
        self.scan_btn = W.QPushButton(theme_icon("edit-find", "system-search"), tr("Scan"))
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
        self.found = self.make_table([tr("Headset"), tr("Address"), tr("Signal"), ""], 0)
        self.fit_rows(self.found, 4)
        self.found.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        box.addWidget(self.found)
        sections.addWidget(self.pairing)

        # device info
        self.device = W.QGroupBox(tr("Dongle"))
        form = W.QFormLayout(self.device)
        form.setFieldGrowthPolicy(W.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.model = W.QLabel("–")
        self.model_note = W.QLabel(
            tr("Reported to work by users but not tested here. Please "
            "<a href='https://github.com/doodersrage/btwctl/issues/new/choose'>report</a> what works."))
        self.model_note.setWordWrap(True)
        self.model_note.setSizePolicy(W.QSizePolicy.Policy.Expanding, W.QSizePolicy.Policy.Preferred)
        self.model_note.setOpenExternalLinks(True)
        self.model_note.hide()
        self.firmware = W.QLabel("–")
        self.serial = W.QLabel("–")
        for label in (self.model, self.firmware, self.serial):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(tr("Model:"), self.model)
        form.addRow("", self.model_note)
        form.addRow(tr("Firmware:"), self.firmware)
        form.addRow(tr("Serial number:"), self.serial)
        self.diag_btn = W.QPushButton(theme_icon("edit-copy"), tr("Copy diagnostics"))
        self.diag_btn.setToolTip(tr("Copy every readable register as JSON, for bug reports"))
        self.diag_btn.clicked.connect(self.copy_diagnostics)
        row = W.QHBoxLayout()
        row.addWidget(self.diag_btn)
        row.addStretch()
        form.addRow("", row)
        sections.addWidget(self.device)
        sections.addStretch(1)

        self.scan_timer = QtCore.QElapsedTimer()
        self.scan_tick = QtCore.QTimer(self)
        self.scan_tick.timeout.connect(self.update_scan_progress)
        self.fit_to_screen()

    def fit_to_screen(self):
        """Open showing everything if it fits, else at most 90% of the screen height."""
        content = self.scroll.widget()
        want_h = (self.sizeHint().height() - self.scroll.sizeHint().height()
                  + content.sizeHint().height() + 8)
        want_w = max(560, content.sizeHint().width() + self.scroll.verticalScrollBar().sizeHint().width() + 24)
        screen = self.screen() or W.QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        if avail is not None:
            want_h = min(want_h, int(avail.height() * 0.9))
        self.resize(want_w, want_h)

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
        def job():
            st = snapshot(with_info=with_info)
            st["service"] = service.is_active()
            return st
        self.worker.run(job, self.on_state)

    def auto_refresh(self):
        if self.isVisible() and not self.worker.busy:
            self.refresh()

    def on_state(self, r):
        if self.closed:
            return
        if isinstance(r, Busy):
            self.say(tr("The dongle is busy in another program; retrying…"))
            return
        if isinstance(r, Exception):
            self.state = None
            if isinstance(r, NotFound):
                self.title.setText(tr("No dongle found"))
                self.subtitle.setText(tr("Plug in the dongle (it also disappears briefly after HFP changes)."))
            else:
                self.title.setText(tr("BT-W Control"))
                self.subtitle.setText(str(r))
            self.update_enabled()
            return

        self.state = r
        if "firmware" in r:
            self.info_loaded = True
            self.firmware.setText(r["firmware"] or "unknown")
            dongle = r.get("dongle_info")
            if dongle:
                self.model.setText(f"{dongle['model']} ({dongle['usb_id']})")
                self.model_note.setVisible(not dongle["tested"])
            self.serial.setText(r["serial"] or "unknown")

        h = r["headset"]
        if h:
            self.title.setText(h["name"] or h["address"])
            mode = {"LL": tr("aptX Adaptive Low Latency"), "HQ": tr("aptX Adaptive High Quality")}.get(
                r["aptx_mode"], tr("no aptX Adaptive"))
            self.subtitle.setText(tr("Connected · {mode} · {address}").format(mode=mode, address=h["address"]))
        else:
            self.title.setText(tr("No headset connected"))
            last = r["last_headset"]
            self.subtitle.setText(tr("Last: {headset} ({state})").format(
                headset=last["name"] or last["address"], state=r["connection"])
                                  if last else tr("Connect a paired headset or pair a new one below."))

        self.hq.setChecked(r["aptx_mode"] == "HQ")
        self.ll.setChecked(r["aptx_mode"] == "LL")
        self.aptx_hint.setVisible(r["aptx_mode"] is None)
        self.hfp.setChecked(bool(r["hfp"]))
        self.auto_hfp.setChecked(bool(r["auto_hfp"]))
        self.fill_slots(r)
        self.fill_automation(r)
        self.update_enabled()
        self.state_changed.emit(r)

    def fill_slots(self, r):
        current = r["headset"]["address"] if r["headset"] else None
        self.slots.setRowCount(len(r["slots"]))
        for row, s in enumerate(r["slots"]):
            empty = s["address"] == P.ZERO_ADDR
            addr = P.fmt_addr(s["address"])
            name = W.QTableWidgetItem(tr("(empty)") if empty else (s["name"] or addr))
            if empty:
                name.setForeground(self.palette().color(QtGui.QPalette.ColorRole.PlaceholderText))
            self.slots.setItem(row, 0, name)
            self.slots.setItem(row, 1, W.QTableWidgetItem("" if empty else addr))
            if empty:
                self.slots.removeCellWidget(row, 2)
                self.slots.setItem(row, 2, W.QTableWidgetItem(""))
            elif addr == current:
                self.slots.removeCellWidget(row, 2)
                self.slots.setItem(row, 2, W.QTableWidgetItem(tr("✓ Connected")))
            else:
                self.slots.setItem(row, 2, W.QTableWidgetItem(""))
                btn = W.QPushButton(tr("Connect"))
                btn.clicked.connect(lambda _=False, a=s["address"], n=s["name"] or addr: self.connect_to(a, n))
                self.slots.setCellWidget(row, 2, btn)

    def fill_automation(self, r):
        self.config.reload_if_changed()
        h = r["headset"]
        if h:
            self.remember.setText(tr("Use these settings whenever {headset} connects").format(
                headset=h["name"] or h["address"]))
            self.remember.setChecked(bool(self.config.preferences(h["address"])))
        else:
            self.remember.setText(tr("Use these settings whenever this headset connects"))
            self.remember.setChecked(False)
        self.game_mode.setChecked(self.config.game_mode)
        self.audio_follow.setChecked(self.config.audio_follow)
        if not self.game_apps.hasFocus():
            self.game_apps.setText(", ".join(self.config.game_apps))
        running = r.get("service")
        self.service_btn.setVisible(running is False)
        if running:
            self.service_label.setText(tr("✓ Background service running: it applies these automatically."))
        elif running is False:
            self.service_label.setText(tr("The background service isn't running. Headset settings and "
                                       "game mode only work while it runs."))
        else:
            self.service_label.setText(tr("Headset settings and game mode are applied by "
                                       "'btwctl monitor'; keep it running."))

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
        self.remember.setEnabled(idle and h)
        self.refresh_btn.setEnabled(idle)
        # while scanning the button is "Stop"; the Pair buttons stop the scan and connect
        self.scan_btn.setEnabled(self.scanning or (idle and ok))
        self.scan_secs.setEnabled(not self.scanning)
        self.found.setEnabled(self.scanning or (idle and ok))

    # Breeze colours; "info" follows the theme's highlight colour
    BANNER_COLORS = {"ok": (39, 174, 96), "warn": (246, 116, 0), "error": (218, 68, 83)}

    def say(self, text, ms=6000, kind="info"):
        """Show a message in the banner; it hides after ms (0 = until replaced or dismissed)."""
        if kind in self.BANNER_COLORS:
            r, g, b = self.BANNER_COLORS[kind]
        else:
            c = self.palette().color(QtGui.QPalette.ColorRole.Highlight)
            r, g, b = c.red(), c.green(), c.blue()
        self.banner.setStyleSheet(
            f"#banner {{ background: rgba({r},{g},{b},40); border: 1px solid rgb({r},{g},{b});"
            " border-radius: 4px; }")
        self.banner_text.setText(text)
        self.banner.show()
        if ms:
            self.banner_timer.start(ms)
        else:
            self.banner_timer.stop()

    def clear_message(self):
        self.banner_timer.stop()
        self.banner.hide()

    def message(self):
        """The message currently shown ("" if none)."""
        return "" if self.banner.isHidden() else self.banner_text.text()

    # --- actions -----------------------------------------------------------------

    def act(self, doing, fn, done=None, refresh_after=(400,)):
        """Run fn(d) on the worker; report failures; refresh afterwards."""
        self.say(doing + "…", 0)

        def finished(r):
            if self.closed:
                return
            if isinstance(r, Exception):
                self.say(tr("{action} failed: {error}").format(action=doing, error=r), 12000, "error")
            elif done:
                done(r)
            elif isinstance(r, int) and r != P.STATUS_OK:
                self.say(tr("The dongle refused (status 0x{status:02x}). Is a compatible headset connected?")
                         .format(status=r),
                         12000, "warn")
            else:
                self.clear_message()
            for ms in refresh_after:
                QtCore.QTimer.singleShot(ms, self.refresh)
        self.worker.with_device(fn, finished)
        self.update_enabled()

    # --- remembered settings and game mode (config file; the background service applies them)

    def save_preference(self, **values):
        """If 'remember' is on for the connected headset, keep its preferences in step."""
        h = self.state and self.state["headset"]
        if h and self.remember.isChecked():
            self.config.reload_if_changed()
            self.config.set_preferences(h["address"], name=h["name"], **values)
            self.config.save()

    def set_remember(self, on):
        h = self.state and self.state["headset"]
        if not h:
            return
        self.config.reload_if_changed()
        label = h["name"] or h["address"]
        if on:
            from .automation import game_running
            st = self.state
            # while game mode forces Low Latency, that isn't the headset's real preference
            gaming = game_running(self.config)
            self.config.set_preferences(
                h["address"], name=h["name"],
                aptx=None if gaming else (st["aptx_mode"] or "").lower() or None,
                hfp=None if st["hfp"] is None else ("on" if st["hfp"] else "off"),
                auto_hfp=None if st["auto_hfp"] is None else ("on" if st["auto_hfp"] else "off"))
            self.say(tr("These settings will be applied whenever {headset} connects.").format(headset=label),
                     kind="ok")
        else:
            self.config.clear_preferences(h["address"])
            self.say(tr("Settings for {headset} forgotten.").format(headset=label))
        self.config.save()

    def set_game_mode(self, on):
        self.config.reload_if_changed()
        self.config.game_mode = on
        self.config.save()
        self.say(tr("Game mode on: Low Latency while a game runs.") if on else tr("Game mode off."),
                 kind="ok" if on else "info")

    def set_audio_follow(self, on):
        from . import audio
        self.config.reload_if_changed()
        self.config.audio_follow = on
        self.config.save()
        if on and not audio.available():
            self.say(tr("This needs PipeWire (pw-dump and wpctl aren't installed)."), 15000, "warn")
        else:
            self.say(tr("Sound will follow the headset.") if on else tr("Sound won't switch automatically."),
                     kind="ok" if on else "info")

    def set_game_apps(self):
        apps = [a.strip() for a in self.game_apps.text().split(",") if a.strip()]
        self.config.reload_if_changed()
        if apps != self.config.game_apps:
            self.config.game_apps = apps
            self.config.save()
            self.say(tr("Game list saved."), kind="ok")

    def start_service(self):
        def done(r):
            ok, msg = r if not isinstance(r, Exception) else (False, str(r))
            self.say(msg, 10000 if ok else 20000, "ok" if ok else "error")
            QtCore.QTimer.singleShot(1500, self.refresh)
        self.worker.run(service.enable, done)

    def set_aptx(self, mode):
        self.save_preference(aptx=mode)
        self.act(tr("Switching to aptX Low Latency") if mode == "ll" else tr("Switching to aptX High Quality"),
                 lambda d: d.set(P.SET_APTX, P.APTX[mode]))

    def set_hfp(self, on):
        self.save_preference(hfp="on" if on else "off")
        # the dongle re-enumerates: refresh once it's back
        self.act(tr("Turning the headset microphone on") if on else tr("Turning the headset microphone off"),
                 lambda d: d.set(P.SET_HFP, P.HFP_VALUES["on" if on else "off"]),
                 refresh_after=(4000, 8000, 12000))

    def set_auto_hfp(self, on):
        self.save_preference(auto_hfp="on" if on else "off")
        self.act(tr("Turning automatic microphone switching on") if on
                 else tr("Turning automatic microphone switching off"),
                 lambda d: d.set(P.SET_AUTO_HFP, P.AUTO_HFP_VALUES["on" if on else "off"]))

    def connect_to(self, addr, label):
        self.stop_scan.set()   # if a scan is running, end it so the connect can go next

        def done(r):
            result, name = r
            text = {
                "connected": tr("Connected to {headset}."),
                "rejected": tr("The dongle can't reach {headset}. Make sure it's on and not connected "
                              "to another device, or put it in pairing mode and scan."),
                "disconnected": tr("{headset} disconnected again."),
                "timeout": tr("No connection confirmed for {headset} yet."),
            }[result].format(headset=name or label if result == "connected" else label)
            if result == "connected":
                self.say(text, 8000, "ok")
            else:
                self.say(text, 20000, "warn")
        self.act(tr("Connecting to {headset}").format(headset=label), lambda d: d.connect_and_wait(addr, CONNECT_TIMEOUT), done)

    def disconnect(self):
        self.act(tr("Disconnecting"), lambda d: d.start_scan(),
                 lambda r: self.say(tr("Disconnected. (The dongle also starts looking for headsets.)")),
                 refresh_after=(1500,))

    def forget_all(self):
        answer = W.QMessageBox.question(
            self, tr("Forget all headsets?"),
            tr("The dongle will forget every paired headset and reconnect on USB.\n"
            "You'll have to pair your headsets again."),
            W.QMessageBox.StandardButton.Yes | W.QMessageBox.StandardButton.Cancel,
            W.QMessageBox.StandardButton.Cancel)
        if answer == W.QMessageBox.StandardButton.Yes:
            self.act(tr("Forgetting all headsets"), lambda d: d.forget_all(),
                     lambda r: self.say(tr("All headsets forgotten."), kind="ok"), refresh_after=(4000, 8000))

    def copy_diagnostics(self):
        def done(out):
            W.QApplication.clipboard().setText(json.dumps(out, indent=2))
            self.say(tr("Diagnostics copied to the clipboard."), kind="ok")
        self.act(tr("Reading every register"), read_dump, done, refresh_after=())

    # --- scanning ----------------------------------------------------------------

    def toggle_scan(self):
        if self.scanning:
            self.stop_scan.set()
            return
        h = self.state and self.state["headset"]
        if h:
            answer = W.QMessageBox.question(
                self, tr("Disconnect headset?"),
                tr("Scanning disconnects {headset}. Continue?").format(headset=h["name"] or h["address"]),
                W.QMessageBox.StandardButton.Yes | W.QMessageBox.StandardButton.Cancel)
            if answer != W.QMessageBox.StandardButton.Yes:
                return
        self.start_scan(self.scan_secs.value())

    def start_scan(self, seconds):
        self.scanning = True
        self.stop_scan.clear()
        self.scan_rows.clear()
        self.found.setRowCount(0)
        self.scan_btn.setText(tr("Stop"))
        self.scan_seconds = seconds
        self.scan_timer.start()
        self.scan_tick.start(100)
        self.say(tr("Scanning… put the headset in pairing mode."), 0)
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
            self.scan_btn.setText(tr("Scan"))
            if self.closed:
                return
            if isinstance(r, Exception):
                self.say(tr("Scan failed: {error}").format(error=r), 12000, "error")
            elif "conn" in auto:
                _, addr, name = auto["conn"]
                self.say(tr("The dongle connected to {headset} by itself.").format(
                    headset=P.headset_label(addr, name)), 12000, "ok")
            elif self.scan_rows:
                self.say(tr("Click Pair while the headset is still in pairing mode."), 12000)
            else:
                self.say(tr("No headsets found. Is the headset in pairing mode?"), 12000, "warn")
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
            btn = W.QPushButton(tr("Pair"))
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
    app.setApplicationName("btwctl")
    app.setApplicationDisplayName(tr("BT-W Control"))
    app.setDesktopFileName("btwctl")
    w = Window()
    w.show()

    def cleanup():
        w.shutdown()
        app.quit()
    run_app(app, cleanup)


if __name__ == "__main__":
    main()
