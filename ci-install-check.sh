#!/bin/sh
# Used by CI after following the README's install steps: is everything where it should be?
set -ex
# where the command and the Python with the program should be (defaults: install.sh)
BIN=${EXPECT_BIN:-/usr/local/bin/btwctl}
PY=${PYTHON:-/opt/btwctl/pipx/venvs/btwctl/bin/python}
# (Fedora merged /usr/local/sbin into bin, so compare resolved paths)
test "$(readlink -f "$(command -v btwctl)")" = "$(readlink -f "$BIN")"
btwctl --version
btw5-switch --version          # the name before 0.7 still works
# no dongle in CI: must fail with the friendly message, not a traceback
out=$(btwctl status 2>&1) && exit 1
echo "$out" | grep -q "No Creative BT-W5 or BT-W6 found"
test -f /usr/lib/udev/rules.d/70-btwctl.rules
test -f /usr/share/applications/btwctl.desktop
test -f /usr/share/applications/btwctl-tray.desktop
test -f /usr/lib/systemd/user/btwctl-monitor.service
test -f /usr/lib/systemd/user/btw5-switch-monitor.service
if command -v desktop-file-validate >/dev/null; then
    desktop-file-validate /usr/share/applications/btwctl.desktop /usr/share/applications/btwctl-tray.desktop
fi
# the window starts (offscreen) with the installed PyQt6
QT_QPA_PLATFORM=offscreen "$PY" -c "
from btwctl.qt import QtWidgets, BINDING
app = QtWidgets.QApplication([])
from btwctl.window import Window
w = Window(); w.show()
print('window ok:', BINDING)
w.shutdown()"
