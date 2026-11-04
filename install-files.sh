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
70-btwctl.rules                     usr/lib/udev/rules.d/70-btwctl.rules
completions/btwctl.bash             usr/share/bash-completion/completions/btwctl
completions/btwctl.bash             usr/share/bash-completion/completions/btw5-switch
completions/_btwctl                 usr/share/zsh/site-functions/_btwctl
completions/btwctl.fish             usr/share/fish/vendor_completions.d/btwctl.fish
completions/btw5-switch.fish        usr/share/fish/vendor_completions.d/btw5-switch.fish
contrib/btwctl.desktop              usr/share/applications/btwctl.desktop
contrib/btwctl-tray.desktop         usr/share/applications/btwctl-tray.desktop
contrib/btwctl-monitor.service      usr/lib/systemd/user/btwctl-monitor.service
contrib/btw5-switch-monitor.service usr/lib/systemd/user/btw5-switch-monitor.service
README.md                         usr/share/doc/btwctl/README.md
contrib/polybar.ini               usr/share/doc/btwctl/polybar.ini
contrib/waybar/config.jsonc       usr/share/doc/btwctl/waybar/config.jsonc
contrib/waybar/style.css          usr/share/doc/btwctl/waybar/style.css
LICENSE                           usr/share/licenses/btwctl/LICENSE
"

# Left behind by install.sh from before the rename (btw5-switch 0.6); removed either way
# (the packages replace them themselves).
legacy="
usr/lib/udev/rules.d/70-btw5-switch.rules
usr/share/zsh/site-functions/_btw5-switch
usr/share/applications/btw5-switch.desktop
usr/share/applications/btw5-switch-tray.desktop
usr/share/doc/btw5-switch/README.md
usr/share/doc/btw5-switch/polybar.ini
usr/share/doc/btw5-switch/waybar/config.jsonc
usr/share/doc/btw5-switch/waybar/style.css
usr/share/licenses/btw5-switch/LICENSE
"
if [ "$root" = / ]; then
    for f in $legacy; do rm -f "/$f"; done
    rmdir /usr/share/doc/btw5-switch/waybar /usr/share/doc/btw5-switch /usr/share/licenses/btw5-switch 2>/dev/null || true
fi

echo "$files" | while read -r from to; do
    [ -n "$from" ] || continue
    if [ -n "$uninstall" ]; then
        rm -f "$root/$to"
    else
        install -Dm644 "$src/$from" "$root/$to"
    fi
done
if [ -n "$uninstall" ]; then
    rmdir "$root/usr/share/doc/btwctl/waybar" "$root/usr/share/doc/btwctl" \
          "$root/usr/share/licenses/btwctl" 2>/dev/null || true
fi
