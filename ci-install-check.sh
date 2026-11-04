#!/bin/sh
# Used by CI after following the README's install steps: is everything where it should be?
set -ex
# where the command and the Python with the program should be (defaults: install.sh)
BIN=${EXPECT_BIN:-/usr/local/bin/btw5-switch}
PY=${PYTHON:-/opt/btw5-switch/pipx/venvs/btw5-switch/bin/python}
# (Fedora merged /usr/local/sbin into bin, so compare resolved paths)
test "$(readlink -f "$(command -v btw5-switch)")" = "$(readlink -f "$BIN")"
btw5-switch --version
# no dongle in CI: must fail with the friendly message, not a traceback
out=$(btw5-switch status 2>&1) && exit 1
echo "$out" | grep -q "Creative BT-W5 not found"
test -f /usr/lib/udev/rules.d/70-btw5-switch.rules
test -f /usr/share/applications/btw5-switch.desktop
test -f /usr/share/applications/btw5-switch-tray.desktop
test -f /usr/lib/systemd/user/btw5-switch-monitor.service
if command -v desktop-file-validate >/dev/null; then
    desktop-file-validate /usr/share/applications/btw5-switch.desktop /usr/share/applications/btw5-switch-tray.desktop
fi
# the window starts (offscreen) with the installed PyQt6
QT_QPA_PLATFORM=offscreen "$PY" -c "
from btw5.qt import QtWidgets, BINDING
app = QtWidgets.QApplication([])
from btw5.window import Window
w = Window(); w.show()
print('window ok:', BINDING)
w.shutdown()"
