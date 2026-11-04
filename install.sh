#!/bin/sh
# Install btw5-switch system-wide on distributions other than Arch (Arch: use makepkg -si).
#
#   sudo ./install.sh              program, window and tray (PyQt6), device access rule,
#                                  app menu entries, shell completions
#   sudo ./install.sh --no-gui     without the window and tray (no PyQt6 download)
#   sudo ./install.sh --uninstall  remove everything again
#
# The program goes into its own environment under /opt/btw5-switch (via pipx),
# with the command in /usr/local/bin so app launchers and services find it.
set -e

PIPX_HOME=/opt/btw5-switch/pipx
PIPX_BIN_DIR=/usr/local/bin
PIPX_MAN_DIR=/usr/local/share/man
export PIPX_HOME PIPX_BIN_DIR PIPX_MAN_DIR

src=$(cd "$(dirname "$0")" && pwd)
mode=install
extras="[gui]"
for arg in "$@"; do
    case $arg in
        --uninstall) mode=uninstall ;;
        --no-gui) extras= ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" != 0 ]; then
    echo "Please run with sudo:  sudo $0 $*" >&2
    exit 1
fi

reload_udev() {
    # (no udev in containers; harmless to skip)
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=usb --attr-match=idVendor=041e 2>/dev/null || true
}

if [ "$mode" = uninstall ]; then
    if command -v pipx >/dev/null; then
        pipx uninstall btw5-switch || true
    fi
    rm -rf /opt/btw5-switch
    "$src/install-files.sh" --uninstall /
    reload_udev
    echo "btw5-switch removed."
    echo "Also delete ~/.config/autostart/btw5-switch-tray.desktop if you set the tray to start at login."
    exit 0
fi

if ! command -v pipx >/dev/null; then
    echo "pipx is needed. Install it first:" >&2
    echo "  Debian/Ubuntu/Mint:  sudo apt install pipx libusb-1.0-0" >&2
    echo "  Fedora:              sudo dnf install pipx libusb1" >&2
    echo "  openSUSE:            sudo zypper install python3-pipx libusb-1_0-0" >&2
    exit 1
fi

# Build from a temporary copy so no root-owned build files end up in your checkout.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp -r "$src/btw5" "$src/pyproject.toml" "$src/README.md" "$src/LICENSE" "$tmp/"
find "$tmp" -name __pycache__ -prune -exec rm -rf {} +

echo ">>> Installing the program (this downloads PyUSB${extras:+ and PyQt6})..."
pipx install --force "$tmp$extras"

echo ">>> Installing the device access rule, app menu entries and shell completions..."
"$src/install-files.sh" /
reload_udev

echo
echo "Done. Unplug the BT-W5 and plug it back in, then open \"BT-W5 Switch\" from your"
echo "application menu, or run:  btw5-switch status"
