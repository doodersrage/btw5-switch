"""Qt plumbing shared by the tray icon and the window (PyQt6 or PySide6)."""

import signal
import sys
from concurrent.futures import ThreadPoolExecutor

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import pyqtSignal as Signal
    BINDING = "PyQt6"
except ImportError:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        from PySide6.QtCore import Signal
        BINDING = "PySide6"
    except ImportError:
        sys.exit("The GUI needs PyQt6 or PySide6 (Arch: sudo pacman -S python-pyqt6).")

from . import protocol as P
from .device import BTW5, read_status

ICON_NAMES = ["audio-headphones", "audio-headset", "audio-card"]


def theme_icon(*names):
    for n in names or ICON_NAMES:
        icon = QtGui.QIcon.fromTheme(n)
        if not icon.isNull():
            return icon
    return QtGui.QIcon()


def snapshot(with_info=False):
    """Status, pairing slots (and optionally firmware/serial), read in one go.

    Runs on the worker thread."""
    with BTW5() as d:
        st = read_status(d)
        st["slots"] = [{"slot": s, "address": a, "name": n} for s, a, n in d.slots()]
        st["paired"] = [x for x in st["slots"] if x["address"] != P.ZERO_ADDR]
        if with_info:
            st["firmware"] = d.info_str(P.INFO_FIRMWARE)
            st["serial"] = d.info_str(P.INFO_SERIAL)
    return st


class Worker(QtCore.QObject):
    """Runs dongle jobs one at a time on a background thread.

    Each job opens the dongle, does its work and lets go, so the CLI (and the
    other GUI) keep working. Results and progress come back on the GUI thread."""

    _done = Signal(object, object)       # (callback, result or exception)
    _progress = Signal(object, object)   # (callback, item)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pending = 0
        self.stopped = False
        self._done.connect(self._finish)
        self._progress.connect(lambda cb, item: cb(item))

    def _finish(self, cb, result):
        self.pending -= 1
        if self.pending == 0:
            self.busy_changed.emit(False)
        if cb:
            cb(result)

    @property
    def busy(self):
        return self.pending > 0

    def run(self, fn, on_done=None, on_progress=None):
        """fn() runs on the worker thread; with on_progress it's called as
        fn(report) and every report(item) reaches on_progress(item).

        Ignored after shutdown() (e.g. a delayed refresh firing as the app quits):
        an exception escaping a Qt callback would abort the whole process."""
        if self.stopped:
            return
        self.pending += 1
        if self.pending == 1:
            self.busy_changed.emit(True)

        def job():
            try:
                if on_progress:
                    r = fn(lambda item: self._progress.emit(on_progress, item))
                else:
                    r = fn()
            except Exception as e:  # shown to the user, never fatal
                r = e
            self._done.emit(on_done, r)
        self.pool.submit(job)

    def with_device(self, fn, on_done=None, on_progress=None):
        """Like run(), but fn gets an open BTW5 (and report, with on_progress)."""
        def job(*report):
            with BTW5() as d:
                return fn(d, *report)
        self.run(job, on_done, on_progress)

    def shutdown(self):
        self.stopped = True
        self.pool.shutdown(wait=True, cancel_futures=True)


def run_app(app, cleanup):
    """Run the event loop; SIGTERM/Ctrl-C and normal exits all go through cleanup()."""
    # Qt's event loop doesn't let Python run signal handlers on its own; a
    # short heartbeat timer does, so SIGTERM/Ctrl-C quit cleanly and promptly.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: cleanup())
    heartbeat = QtCore.QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(300)
    code = app.exec()
    cleanup()  # also when the loop ended some other way; waits for a running job
    sys.exit(code)
