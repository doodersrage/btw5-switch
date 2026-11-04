# Changelog

## Next (not released yet)

- The tray no longer repeats the background service's "headset connected/disconnected" notifications.
- `man btwctl`.
- Software centres (KDE Discover, GNOME Software) show BT-W Control with a description and screenshot (AppStream metadata).
- Releases include a `SHA256SUMS` file to verify downloads.

## 0.8 (not released yet)

- **Sound follows the headset**: the dongle becomes the default audio output (and, with the headset microphone on, the default microphone) while a headset is connected, and the previous device comes back when it disconnects. Turn it on in the window, the tray menu or with `btwctl audio-follow on`. Uses PipeWire.
- The window and tray can be translated (gettext `.po` files); see `CONTRIBUTING-translations.md`.
- Packaging for Ubuntu PPAs (`debian/`) and Fedora COPR (`.copr/Makefile`), checked in CI; see `packaging/PUBLISHING.md`. The release `.deb` is now built with the standard Debian tools.

## 0.7 (not released yet)

- **Renamed to btwctl** (it was btw5-switch), since it now supports more than the BT-W5. The window is now "BT-W Control". Upgrading needs nothing else: the packages replace btw5-switch, the `btw5-switch` command still works, settings move over automatically, and a background service enabled under the old name keeps running (the window's "Start background service" switches it to `btwctl-monitor.service`).

- **Creative BT-W6** (`041e:3132`) is found automatically, with access without sudo. aptX switching has been reported to work by a user; the rest is expected to but hasn't been confirmed. The model is shown by `info` and in the window.
- Arch Linux package (`.pkg.tar.zst`) on the release page.

## 0.6

First release.

- **Settings window** (`btw5-switch gui`): connected headset, aptX Adaptive High Quality / Low Latency, headset microphone (HFP), automatic HFP switching, the 4 pairing slots with Connect buttons, scanning for and pairing new headsets, firmware and serial number, and "Copy diagnostics".
- **Tray icon** (`btw5-switch tray`): settings and paired headsets in its menu; left click toggles Low Latency / High Quality.
- **Per-headset settings**: remember aptX mode, microphone and auto-switching for each headset and apply them whenever it connects (`btw5-switch prefer`, or the checkbox in the window).
- **Game mode**: aptX Low Latency while a game is running, then back (`btw5-switch game-mode on`, or the window).
- **Notifications** when a headset connects or disconnects or a setting changes (`systemctl --user enable --now btw5-switch-monitor.service`).
- **Command line tool** with JSON output, shell completions (bash, zsh, fish), `--notify` for keyboard shortcuts, a waybar/polybar module and app-menu quick actions.
- Several programs can use the dongle at once (the background service shares it).
- `--device VID:PID` to try other Creative BT-W dongles.
- Packages: Arch (`.pkg.tar.zst`), Debian/Ubuntu (`.deb`) and Fedora (`.rpm`) on the release page, `makepkg -si` from a checkout, and `install.sh` for other distributions.
