"""System tray icon for the BT-W5 (PyQt6 or PySide6).

All dongle I/O happens on a worker thread; each action opens the dongle,
does its job and lets go, so the CLI keeps working while the tray runs.
"""

import signal
import sys
from concurrent.futures import ThreadPoolExecutor

try:
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal as Signal
    from PyQt6.QtGui import QAction, QActionGroup, QIcon
    from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
except ImportError:
    try:
        from PySide6.QtCore import QObject, QTimer, Signal
        from PySide6.QtGui import QAction, QActionGroup, QIcon
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except ImportError:
        sys.exit("The tray icon needs PyQt6 or PySide6 (Arch: sudo pacman -S python-pyqt6).")

from . import protocol as P
from .device import BTW5, BTW5Error, NotFound, read_status

ICON_NAMES = ["audio-headphones", "audio-headset", "audio-card"]


def theme_icon(*names):
    for n in names:
        icon = QIcon.fromTheme(n)
        if not icon.isNull():
            return icon
    return QIcon()


def snapshot():
    """Everything the menu shows, read in one go (runs on the worker thread)."""
    with BTW5() as d:
        st = read_status(d)
        st["paired"] = [{"address": a, "name": n} for _, a, n in d.slots() if a != P.ZERO_ADDR]
    return st


class Bridge(QObject):
    """Carries worker-thread results back to the GUI thread."""
    done = Signal(object, object)  # (callback, result-or-exception)


class Tray:
    def __init__(self, app, interval):
        self.app = app
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.bridge = Bridge()
        self.bridge.done.connect(lambda cb, r: cb(r))
        self.state = None
        self.quitting = False

        self.icon = QSystemTrayIcon(theme_icon(*ICON_NAMES))
        self.icon.setToolTip("BT-W5")
        self.menu = QMenu()
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self.on_activated)
        self.build_menu()
        self.icon.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(interval * 1000))
        self.refresh()

    # --- background jobs ---------------------------------------------------

    def run(self, fn, callback):
        def job():
            try:
                r = fn()
            except Exception as e:  # shown in the tooltip, never fatal
                r = e
            self.bridge.done.emit(callback, r)
        self.pool.submit(job)

    def refresh(self):
        self.run(snapshot, self.on_state)

    def act(self, what, fn, refresh_after_ms=500):
        def done(r):
            if isinstance(r, Exception):
                self.icon.showMessage("BT-W5", f"{what} failed: {r}",
                                      QSystemTrayIcon.MessageIcon.Warning, 5000)
            elif isinstance(r, int) and r not in (P.STATUS_OK,):
                self.icon.showMessage("BT-W5", f"The dongle rejected: {what} (status 0x{r:02x})",
                                      QSystemTrayIcon.MessageIcon.Warning, 5000)
            QTimer.singleShot(refresh_after_ms, self.refresh)

        def job():
            with BTW5() as d:
                return fn(d)
        self.run(job, done)

    # --- UI ----------------------------------------------------------------

    def on_activated(self, reason):
        # left click: toggle aptX mode (like the status bar module)
        if reason == QSystemTrayIcon.ActivationReason.Trigger and self.state and self.state.get("aptx_mode"):
            self.set_aptx("hq" if self.state["aptx_mode"] == "LL" else "ll")

    def set_aptx(self, mode):
        self.act(f"aptX {mode.upper()}", lambda d: d.set(P.SET_APTX, P.APTX[mode]))

    def on_state(self, r):
        if isinstance(r, Exception):
            self.state = None
            msg = "BT-W5 not plugged in" if isinstance(r, NotFound) else str(r)
            if isinstance(r, BTW5Error) and not isinstance(r, NotFound) and "in use" in msg:
                return  # someone else had it for a moment; keep the last state
            self.icon.setToolTip(msg)
            self.build_menu()
            return
        prev, self.state = self.state, r
        h = r["headset"]
        if prev is not None:
            ph = prev["headset"]
            if h and (not ph or ph["address"] != h["address"]):
                self.icon.showMessage("BT-W5", f"Connected: {h['name'] or h['address']}",
                                      theme_icon(*ICON_NAMES), 4000)
            elif ph and not h:
                self.icon.showMessage("BT-W5", f"Disconnected: {ph['name'] or ph['address']}",
                                      theme_icon(*ICON_NAMES), 4000)
        tip = [f"{h['name'] or h['address']}" if h else "No headset connected"]
        if r["aptx_mode"]:
            tip.append(f"aptX Adaptive {r['aptx_mode']}")
        tip.append(f"HFP {'on' if r['hfp'] else 'off'}, auto HFP {'on' if r['auto_hfp'] else 'off'}")
        self.icon.setToolTip("BT-W5\n" + "\n".join(tip))
        self.build_menu()

    def build_menu(self):
        m = self.menu
        m.clear()
        st = self.state
        if st is None:
            m.addAction(self.icon.toolTip()).setEnabled(False)
        else:
            h = st["headset"]
            m.addAction(f"🎧 {h['name'] or h['address']}" if h else "No headset connected").setEnabled(False)
            m.addSeparator()

            group = QActionGroup(m)
            for mode, label in (("ll", "aptX Low Latency"), ("hq", "aptX High Quality")):
                a = QAction(label, m, checkable=True)
                a.setChecked(st["aptx_mode"] == mode.upper())
                a.setEnabled(st["aptx_mode"] is not None)
                a.triggered.connect(lambda _=False, mode=mode: self.set_aptx(mode))
                group.addAction(a)
                m.addAction(a)
            m.addSeparator()

            a = QAction("Headset microphone (HFP)", m, checkable=True)
            a.setChecked(bool(st["hfp"]))
            a.setEnabled(st["hfp"] is not None)
            a.triggered.connect(lambda on: self.act(
                "HFP", lambda d: d.set(P.SET_HFP, P.HFP_VALUES["on" if on else "off"]), 4000))
            m.addAction(a)
            a = QAction("Switch to HFP automatically", m, checkable=True)
            a.setChecked(bool(st["auto_hfp"]))
            a.setEnabled(st["auto_hfp"] is not None)
            a.triggered.connect(lambda on: self.act(
                "auto HFP", lambda d: d.set(P.SET_AUTO_HFP, P.AUTO_HFP_VALUES["on" if on else "off"])))
            m.addAction(a)
            m.addSeparator()

            sub = m.addMenu("Connect to")
            for dev in st["paired"]:
                label = dev["name"] or P.fmt_addr(dev["address"])
                a = sub.addAction(label)
                current = h is not None and h["address"] == P.fmt_addr(dev["address"])
                a.setEnabled(not current)
                a.triggered.connect(lambda _=False, addr=dev["address"], label=label:
                                    self.act(f"connect to {label}", lambda d: d.connect(addr), 5000))
            sub.setEnabled(bool(st["paired"]))
            a = m.addAction("Disconnect")
            a.setEnabled(h is not None)
            a.triggered.connect(lambda: self.act("disconnect", lambda d: d.start_scan(), 2000))
        m.addSeparator()
        m.addAction("Refresh").triggered.connect(self.refresh)
        m.addAction("Quit").triggered.connect(self.quit)

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        self.timer.stop()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.app.quit()


def main(interval=15):
    app = QApplication(sys.argv[:1])
    app.setApplicationName("btw5-switch")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        sys.exit("No system tray available.")
    tray = Tray(app, interval)

    # Qt's event loop doesn't let Python run signal handlers on its own; a
    # short heartbeat timer does, so SIGTERM/Ctrl-C quit cleanly (and promptly).
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: tray.quit())
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(300)
    code = app.exec()
    tray.quit()  # also after the event loop ended some other way; waits for a running job
    sys.exit(code)


if __name__ == "__main__":
    main()
