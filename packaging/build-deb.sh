#!/bin/sh
# Build a Debian/Ubuntu package from this source tree:  packaging/build-deb.sh [outdir]
# Uses the distribution's python3-usb (and python3-pyqt6 for the window), no pip.
set -e
src=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-$src}
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$src/btw5/__init__.py")
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT

mkdir -p "$root/DEBIAN" "$root/usr/lib/python3/dist-packages" "$root/usr/bin"
cp -r "$src/btw5" "$root/usr/lib/python3/dist-packages/"
find "$root" -name __pycache__ -prune -exec rm -rf {} +
cat > "$root/usr/bin/btw5-switch" <<'PY'
#!/usr/bin/python3
from btw5.cli import main
main()
PY
chmod 755 "$root/usr/bin/btw5-switch"
"$src/install-files.sh" "$root"
install -Dm644 "$src/LICENSE" "$root/usr/share/doc/btw5-switch/copyright"
rm -rf "$root/usr/share/licenses"   # Debian keeps it as doc/<pkg>/copyright

size=$(du -sk "$root/usr" | cut -f1)
cat > "$root/DEBIAN/control" <<CTL
Package: btw5-switch
Version: $version-1
Section: sound
Priority: optional
Architecture: all
Depends: python3 (>= 3.9), python3-usb
Recommends: python3-pyqt6 | python3-pyside6, libnotify-bin
Installed-Size: $size
Maintainer: Robert McDowell <doodersrage@gmail.com>
Homepage: https://github.com/doodersrage/btw5-switch
Description: Control the Creative BT-W5 and BT-W6 Bluetooth audio dongles
 Switch aptX Adaptive between High Quality and Low Latency, turn the headset
 microphone (HFP) on or off, pair and switch between headsets, and get
 notified when a headset connects. Includes a settings window, a tray icon
 and a command line tool.
CTL
cat > "$root/DEBIAN/postinst" <<'SH'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=usb --attr-match=idVendor=041e 2>/dev/null || true
    echo "btw5-switch: unplug the BT-W5 and plug it back in to get access to it."
fi
SH
cat > "$root/DEBIAN/postrm" <<'SH'
#!/bin/sh
set -e
udevadm control --reload-rules 2>/dev/null || true
SH
chmod 755 "$root/DEBIAN/postinst" "$root/DEBIAN/postrm"

mkdir -p "$out"
dpkg-deb --root-owner-group --build "$root" "$out/btw5-switch_${version}-1_all.deb"
