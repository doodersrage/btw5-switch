"""Render docs/window.png for the README from made-up data (no real addresses or serials).

    python docs/screenshot.py docs/window.png

Run it in your desktop session so the window gets your real style and icons.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from btwctl import protocol as P
from btwctl.qt import QtWidgets
from btwctl.window import Window
from tests import fakes
from tests.fakes import FakeDongle, report, slot_frame, ack

HS = bytes.fromhex("001b66a1b2c3")
OTHER = bytes.fromhex("ac800a112233")
fakes.HEADSET = HS

class Demo(FakeDongle):
    def write(self, data):
        cmd, p = P.split_report(data)
        if cmd == P.CMD_BT and p[0] == P.BT_SLOT and p[1] == 1:
            self.queue += [ack(P.CMD_BT, 0, P.BT_SLOT), slot_frame(1, OTHER, b"WH-1000XM5")]
            return
        if cmd == P.CMD_INFO and p[0] == P.INFO_SERIAL:
            self.queue.append(report(P.CMD_INFO, bytes([p[0]]) + b"YDSA0000000000000X"))
            return
        if cmd == P.CMD_BT and p[0] == P.BT_CONN:
            self.queue += [fakes.conn_frame(P.CONN_CONNECTED, HS)]
            return
        if cmd == P.CMD_BT and p[0] == P.BT_SLOT and p[1] == 0:
            self.queue += [ack(P.CMD_BT, 0, P.BT_SLOT), slot_frame(0, HS, fakes.NAME)]
            return
        super().write(data)

import tempfile
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()   # a clean config, not yours
import btwctl.service
btwctl.service.is_active = lambda: True
import btwctl.broker
btwctl.broker.default_transport = lambda *a, **k: Demo()
app = QtWidgets.QApplication([]); app.setDesktopFileName("btwctl")
w = Window(); w.show()
end = time.time() + 6
while time.time() < end and not (w.info_loaded and not w.worker.busy):
    app.processEvents(); time.sleep(0.02)
w.add_found(bytes.fromhex("68001abc5ceb"), "Tranya X3", -48)
w.add_found(bytes.fromhex("f4b688aa0412"), "JBL Tune 760NC", -71)
w.say("Click Pair while the headset is still in pairing mode.", 0)
for _ in range(20): app.processEvents(); time.sleep(0.02)
w.grab().save(sys.argv[1])
print(w.title.text(), "|", w.subtitle.text(), "|", w.serial.text())
w.shutdown()
