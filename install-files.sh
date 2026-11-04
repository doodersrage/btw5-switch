#!/bin/sh
# Install everything except the Python package itself into a staging root.
# Used by both PKGBUILDs; also handy for other distros:  sudo ./install-files.sh /
set -e
root=${1:?usage: install-files.sh DESTDIR}
src=$(dirname "$0")
install -Dm644 "$src/70-btw5-switch.rules"              "$root/usr/lib/udev/rules.d/70-btw5-switch.rules"
install -Dm644 "$src/completions/btw5-switch.bash"      "$root/usr/share/bash-completion/completions/btw5-switch"
install -Dm644 "$src/completions/_btw5-switch"          "$root/usr/share/zsh/site-functions/_btw5-switch"
install -Dm644 "$src/completions/btw5-switch.fish"      "$root/usr/share/fish/vendor_completions.d/btw5-switch.fish"
install -Dm644 "$src/contrib/btw5-switch-tray.desktop"  "$root/usr/share/applications/btw5-switch-tray.desktop"
install -Dm644 "$src/contrib/btw5-switch-monitor.service" "$root/usr/lib/systemd/user/btw5-switch-monitor.service"
install -Dm644 -t "$root/usr/share/doc/btw5-switch"            "$src/README.md" "$src/contrib/polybar.ini"
install -Dm644 "$src/LICENSE"                           "$root/usr/share/licenses/btw5-switch/LICENSE"
install -Dm644 -t "$root/usr/share/doc/btw5-switch/waybar"     "$src/contrib/waybar/config.jsonc" "$src/contrib/waybar/style.css"
