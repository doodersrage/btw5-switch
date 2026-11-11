"""System tray icon for the BT-W5 (PyQt6 or PySide6).

All dongle I/O happens on a worker thread; each action opens the dongle,
does its job and lets go, so the CLI keeps working while the tray runs.
"""

import sys

from . import protocol as P
from .device import BTW5Error, NotFound
from .i18n import tr
from .qt import QtCore, QtGui, QtWidgets, Worker, run_app, snapshot, theme_icon

QAction, QActionGroup = QtGui.QAction, QtGui.QActionGroup
QMenu, QSystemTrayIcon, QTimer = QtWidgets.QMenu, QtWidgets.QSystemTrayIcon, QtCore.QTimer


class Tray:
    def __init__(self, app, interval):
        self.app = app
        self.worker = Worker()
        self.state = None
        self.quitting = False
        self.window = None

        self.icon = QSystemTrayIcon(theme_icon())
        self.icon.setToolTip(tr("BT-W Control"))
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

    def refresh(self):
        def job():
            from . import service
            st = snapshot()
            st["service"] = service.is_active()
            return st
        self.worker.run(job, self.on_state, quiet=True)

    def act(self, what, fn, refresh_after_ms=500):
        def done(r):
            if isinstance(r, Exception):
                self.icon.showMessage(tr("BT-W Control"), tr("{action} failed: {error}").format(action=what, error=r),
                                      QSystemTrayIcon.MessageIcon.Warning, 5000)
            elif isinstance(r, int) and r not in (P.STATUS_OK,):
                self.icon.showMessage(tr("BT-W Control"), tr("The dongle rejected: {action} (status 0x{status:02x})").format(action=what, status=r),
                                      QSystemTrayIcon.MessageIcon.Warning, 5000)
            QTimer.singleShot(refresh_after_ms, self.refresh)
        self.worker.with_device(fn, done)

    def config(self):
        from .config import Config
        return Config()

    def set_audio_follow(self, on):
        cfg = self.config()
        cfg.audio_follow = on
        cfg.save()

    def set_game_mode(self, on):
        cfg = self.config()
        cfg.game_mode = on
        cfg.save()
        from . import service
        if on and service.is_active() is False:
            self.icon.showMessage(tr("BT-W Control"), tr("Game mode needs the background service. Turn it on in the "
                                  "BT-W Control window."), QSystemTrayIcon.MessageIcon.Information, 8000)

    def open_window(self):
        from .window import Window
        if self.window is None:
            self.window = Window(self.worker, standalone=False)
            self.window.state_changed.connect(self.on_state)
        self.window.present()

    # --- UI ----------------------------------------------------------------

    def on_activated(self, reason):
        # left click: toggle aptX mode (like the status bar module)
        if reason == QSystemTrayIcon.ActivationReason.Trigger and self.state and self.state.get("aptx_mode"):
            self.set_aptx("hq" if self.state["aptx_mode"] == "LL" else "ll")

    def set_aptx(self, mode):
        self.act(tr("aptX Low Latency") if mode == "ll" else tr("aptX High Quality"), lambda d: d.set(P.SET_APTX, P.APTX[mode]))

    def on_state(self, r):
        if isinstance(r, Exception):
            self.state = None
            msg = tr("No BT-W dongle plugged in") if isinstance(r, NotFound) else str(r)
            if isinstance(r, BTW5Error) and not isinstance(r, NotFound) and "in use" in msg:
                return  # someone else had it for a moment; keep the last state
            self.icon.setToolTip(msg)
            self.build_menu()
            return
        prev, self.state = self.state, r
        h = r["headset"]
        # The background service already notifies about (dis)connects; don't say it twice.
        if prev is not None and not r.get("service"):
            ph = prev["headset"]
            if h and (not ph or ph["address"] != h["address"]):
                self.icon.showMessage(tr("BT-W Control"), tr("Connected: {headset}").format(headset=h["name"] or h["address"]),
                                      theme_icon(), 4000)
            elif ph and not h:
                self.icon.showMessage(tr("BT-W Control"), tr("Disconnected: {headset}").format(headset=ph["name"] or ph["address"]),
                                      theme_icon(), 4000)
        tip = [h["name"] or h["address"] if h else tr("No headset connected")]
        if r["aptx_mode"]:
            tip.append(tr("aptX Adaptive Low Latency") if r["aptx_mode"] == "LL" else tr("aptX Adaptive High Quality"))
        tip.append(tr("Microphone (HFP): {hfp}, automatic switching: {auto}").format(
            hfp=tr("on") if r["hfp"] else tr("off"), auto=tr("on") if r["auto_hfp"] else tr("off")))
        self.icon.setToolTip("BT-W Control\n" + "\n".join(tip))
        self.build_menu()

    def build_menu(self):
        m = self.menu
        m.clear()
        m.addAction(theme_icon("configure", "preferences-system"), tr("Open BT-W Control…")).triggered.connect(
            self.open_window)
        m.addSeparator()
        st = self.state
        if st is None:
            m.addAction(self.icon.toolTip()).setEnabled(False)
        else:
            h = st["headset"]
            m.addAction(f"🎧 {h['name'] or h['address']}" if h else tr("No headset connected")).setEnabled(False)
            m.addSeparator()

            group = QActionGroup(m)
            for mode, label in (("ll", tr("aptX Low Latency")), ("hq", tr("aptX High Quality"))):
                a = QAction(label, m, checkable=True)
                a.setChecked(st["aptx_mode"] == mode.upper())
                a.setEnabled(st["aptx_mode"] is not None)
                a.triggered.connect(lambda _=False, mode=mode: self.set_aptx(mode))
                group.addAction(a)
                m.addAction(a)
            m.addSeparator()

            a = QAction(tr("Headset microphone (HFP)"), m, checkable=True)
            a.setChecked(bool(st["hfp"]))
            a.setEnabled(st["hfp"] is not None)
            a.triggered.connect(lambda on: self.act(
                tr("HFP"), lambda d: d.set(P.SET_HFP, P.HFP_VALUES["on" if on else "off"]), 4000))
            m.addAction(a)
            a = QAction(tr("Switch to HFP automatically"), m, checkable=True)
            a.setChecked(bool(st["auto_hfp"]))
            a.setEnabled(st["auto_hfp"] is not None)
            a.triggered.connect(lambda on: self.act(
                tr("auto HFP"), lambda d: d.set(P.SET_AUTO_HFP, P.AUTO_HFP_VALUES["on" if on else "off"])))
            m.addAction(a)
            m.addSeparator()

            a = QAction(tr("Sound follows the headset"), m, checkable=True)
            a.setChecked(self.config().audio_follow)
            a.triggered.connect(self.set_audio_follow)
            m.addAction(a)
            a = QAction(tr("Low Latency while gaming"), m, checkable=True)
            a.setChecked(self.config().game_mode)
            a.setToolTip(tr("Applied by the background service"))
            a.triggered.connect(self.set_game_mode)
            m.addAction(a)
            m.addSeparator()

            sub = m.addMenu(tr("Connect to"))
            for dev in st["paired"]:
                label = dev["name"] or P.fmt_addr(dev["address"])
                a = sub.addAction(label)
                current = h is not None and h["address"] == P.fmt_addr(dev["address"])
                a.setEnabled(not current)
                a.triggered.connect(lambda _=False, addr=dev["address"], label=label:
                                    self.act(tr("Connect to {headset}").format(headset=label),
                                             lambda d: d.connect(addr), 5000))
            sub.setEnabled(bool(st["paired"]))
            a = m.addAction(tr("Disconnect"))
            a.setEnabled(h is not None)
            a.triggered.connect(lambda: self.act(tr("disconnect"), lambda d: d.start_scan(), 2000))
        m.addSeparator()
        m.addAction(tr("Refresh")).triggered.connect(self.refresh)
        m.addAction(tr("Quit")).triggered.connect(self.quit)

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        self.timer.stop()
        if self.window:
            self.window.shutdown()
        self.worker.shutdown()
        self.app.quit()


def main(interval=15):
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("btwctl")
    app.setDesktopFileName("btwctl")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        sys.exit("No system tray available (try 'btwctl gui' for a window).")
    tray = Tray(app, interval)
    run_app(app, tray.quit)


if __name__ == "__main__":
    main()
