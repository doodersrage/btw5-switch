#!/bin/sh
# Install (or remove) everything except the Python package itself.
#   sudo ./install-files.sh /               install into the system
#   sudo ./install-files.sh --uninstall /   remove those files again
# Also used by the PKGBUILDs to stage files into $pkgdir.
set -e
uninstall=
if [ "$1" = "--uninstall" ]; then uninstall=1; shift; fi
root=${1:?usage: install-files.sh [--uninstall] DESTDIR}
src=$(dirname "$0")

# source  destination
files="
70-btw5-switch.rules              usr/lib/udev/rules.d/70-btw5-switch.rules
completions/btw5-switch.bash      usr/share/bash-completion/completions/btw5-switch
completions/_btw5-switch          usr/share/zsh/site-functions/_btw5-switch
completions/btw5-switch.fish      usr/share/fish/vendor_completions.d/btw5-switch.fish
contrib/btw5-switch.desktop       usr/share/applications/btw5-switch.desktop
contrib/btw5-switch-tray.desktop  usr/share/applications/btw5-switch-tray.desktop
contrib/btw5-switch-monitor.service usr/lib/systemd/user/btw5-switch-monitor.service
README.md                         usr/share/doc/btw5-switch/README.md
contrib/polybar.ini               usr/share/doc/btw5-switch/polybar.ini
contrib/waybar/config.jsonc       usr/share/doc/btw5-switch/waybar/config.jsonc
contrib/waybar/style.css          usr/share/doc/btw5-switch/waybar/style.css
LICENSE                           usr/share/licenses/btw5-switch/LICENSE
"

echo "$files" | while read -r from to; do
    [ -n "$from" ] || continue
    if [ -n "$uninstall" ]; then
        rm -f "$root/$to"
    else
        install -Dm644 "$src/$from" "$root/$to"
    fi
done
if [ -n "$uninstall" ]; then
    rmdir "$root/usr/share/doc/btw5-switch/waybar" "$root/usr/share/doc/btw5-switch" \
          "$root/usr/share/licenses/btw5-switch" 2>/dev/null || true
fi
