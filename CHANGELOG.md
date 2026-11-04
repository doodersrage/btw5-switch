# Changelog

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
