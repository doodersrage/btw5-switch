# btw5-switch - control the Creative BT-W5 Bluetooth audio dongle on Linux

[![tests](https://github.com/doodersrage/btw5-switch/actions/workflows/test.yml/badge.svg)](https://github.com/doodersrage/btw5-switch/actions/workflows/test.yml)

Switch aptX Adaptive modes, toggle HFP (headset microphone), scan for, pair and switch between headsets, and get notified when they connect, all without Creative's Windows/macOS app.
Comes with a command line tool, a system tray icon and a status bar module.

## Install

### Arch Linux

```sh
git clone https://github.com/doodersrage/btw5-switch
cd btw5-switch
makepkg -si
```

This installs `/usr/bin/btw5-switch`, a udev rule, shell completions (bash, zsh, fish), a tray launcher and a systemd user unit.
Re-plug the dongle once so the udev rule applies. After that, no sudo is needed.
Optional: `python-pyqt6` (or `pyside6`) for the tray icon, `libnotify` for notifications.

### Other distributions

```sh
pip install .                     # or: pip install '.[tray]' for the tray icon
sudo ./install-files.sh /         # udev rule, completions, desktop file, systemd unit
sudo udevadm control --reload-rules   # then re-plug the dongle
```

PyUSB is also packaged by most distributions (Debian/Ubuntu: `python3-usb`, Fedora: `python3-pyusb`).
Without installing anything, `./btw5-switch.py <command>` runs straight from the checkout (with `sudo` if you skipped the udev rule).

## Usage

```
btw5-switch status              # connected headset, aptX mode, HFP and auto-HFP settings
btw5-switch info                # firmware version and serial number
btw5-switch ll | hq             # aptX Adaptive Low Latency / High Quality
btw5-switch toggle              # switch between the two
btw5-switch hfp on|off          # headset microphone (HFP) support
btw5-switch auto-hfp on|off     # automatically switch to HFP when the mic is used
btw5-switch paired              # list the 4 pairing slots
btw5-switch pair momentum       # connect to a remembered headset by (part of) its name
btw5-switch scan [seconds]      # disconnect the headset and list nearby headsets (default 15 s)
btw5-switch pair 68:00:1a:bc:5c:eb   # connect to an address shown by 'scan'
btw5-switch disconnect          # disconnect the current headset
btw5-switch forget              # forget all paired headsets (asks first; -y to skip)
btw5-switch monitor [--notify]  # print headset and setting changes as they happen
btw5-switch tray                # system tray icon
btw5-switch waybar              # status as JSON for a status bar
btw5-switch dump                # every readable register (for bug reports)
btw5-switch -v <command>        # also hex-dump the USB traffic
btw5-switch raw 6b 09 03        # send a raw command and print replies (protocol exploration)
```

`status`, `info`, `paired`, `scan` and `dump` take `--json`.

Pairing a new headset: put it in pairing mode, run `btw5-switch scan`, then `btw5-switch pair <address>` while it's still in pairing mode.
Often the dongle doesn't list the headset but connects to it by itself; `scan` then says so and stops.
The dongle only connects to headsets it can reach, so if `pair` says the dongle refused (status `0x80`), turn the headset on, make sure it isn't connected to another device, or put it in pairing mode.

Notes:
- `hfp` and `forget` make the dongle re-enumerate on USB, so it briefly disappears and comes back as a new audio device.
- Volume is a standard USB Audio control, so use your desktop's volume slider, `wpctl` or `alsamixer`.

### Tray icon

`btw5-switch tray` (or "BT-W5 Switch" in your application menu) shows the headset and settings in its tooltip, and its menu switches aptX mode, HFP, auto HFP and between paired headsets.
Left click toggles Low Latency / High Quality. It needs PyQt6 or PySide6.
To start it on login, copy `/usr/share/applications/btw5-switch-tray.desktop` to `~/.config/autostart/`.

### Notifications

```sh
systemctl --user enable --now btw5-switch-monitor.service
```

runs `btw5-switch monitor --notify` in the background and shows a desktop notification when a headset connects or disconnects or a setting changes.

### Status bar

`btw5-switch waybar` prints `{"text": "MOMENTUM 4 · HQ", "class": "connected", "tooltip": ...}`.
See `contrib/waybar/` (installed to `/usr/share/doc/btw5-switch/`) for a waybar module that toggles the aptX mode on click, and `contrib/polybar.ini` for polybar.

### Sharing the dongle

Only one program can open the dongle's control interface at a time.
While `btw5-switch monitor` runs, it shares the dongle over a Unix socket (`$XDG_RUNTIME_DIR/btw5-switch.sock`) and all other `btw5-switch` commands, the tray and the status bar module go through it automatically.
Short-lived commands that find the dongle busy wait up to 3 seconds for it.

## Creative BT-W5

The Creative BT-W5 is a USB Bluetooth audio dongle.
It is *not* a normal Bluetooth dongle, i.e. it cannot be used for connecting to non-audio devices.
Instead, it acts as an USB audio device towards the computer and as Bluetooth host towards wireless headsets.

## Protocol

The BT-W5 appears as a USB sound card (interfaces 2-4, no driver needed) plus two HID interfaces.
Configuration goes through HID interface 0. The tool sends output report 3 as a `SET_REPORT` control transfer, and replies come back as input report 3 on interrupt endpoint `0x81`.
The interface's report descriptor doesn't declare input report 3, so the kernel drops the replies and `/dev/hidraw` can't be used; the tool talks to the interface directly with PyUSB.
The communication between the Creative app on Windows and the dongle was logged with [Wireshark](https://www.wireshark.org/) and [USBPcap](https://github.com/desowin/usbpcap) and annotated: [bt-w5.pcapng](bt-w5.pcapng).

Every report is `03 5a <cmd> <len> <payload>`, zero-padded to 65 bytes.

| Command (cmd payload) | Meaning | Reply |
|---|---|---|
| `6b 0a 03 20` / `6b 0a 03 40` | aptX Adaptive LL / HQ | ack `02 0a 6b 00 0a`, `6b 09 08 <val>` |
| `6b 0a 01 01` / `6b 0a 01 02` | HFP on / off | ack, then USB re-enumeration |
| `6b 0a 02 01` / `6b 0a 02 00` | auto-HFP on / off | ack, `6b 09 07 <val>` |
| `6b 09 <key>` | read setting: `03` aptX, `06` HFP, `07` auto-HFP, `08` active aptX mode | `6b 09 <key> <val>` |
| `6b 04 01` | disconnect and scan | `6b 05 <addr6> <nlen> <name> <rssi int16 LE>` per device, or `6b 06` connection events if it connects to a headset in pairing mode by itself |
| `6b 04 02 <addr6>` | connect to address | ack (status `80` if the headset can't be reached), then `6b 06 08`/`0b` |
| `6b 06` | current connection | `6b 06 <state> <addr6> 20 <name[32]>`; no reply right after a scan disconnect |
| `6b 02 <slot>` | read pairing slot 0-3 | `6b 02 <slot> 80 <addr6> 1f <name[31]>` (`00` instead of `80` past the last slot) |
| `6b 07 ff` | forget all pairings | USB re-enumeration |
| `09 02` / `09 03` | firmware version / serial | `09 <field> <ascii>` |

Acks look like `02 0a <cmd> <status> <sub>`. Status `00` means OK, `80` means rejected right now (for example `6b 09 03` with no aptX Adaptive headset connected), `81` means unknown command.

Connection states in `6b 06` frames: `06` not connected (known headset out of reach), `08` connecting, `0a` disconnected, `0b` connected.
They also arrive unprompted when the link changes, as do setting changes (`6b 09 <key> <val>`).

Seen but not yet understood (values on firmware 1.2.230511.1220; none of them changed with aptX mode, volume or connection state):
`6b 09 00` → `01`, `6b 09 01` → `04`, `6b 09 02` → `75`, `6b 09 04` → `3c b3 6e 3d 00 02`, `6b 09 05` → `03`, `6b 01` → `01 01`,
`09 00` → `2b 31 01 01 1e 04` (`041e` is Creative's vendor ID), `09 01 00` → `00 90 ad 3a 21 3c 04`, and the event `6b 06 07` after a scan starts or pairings are forgotten.
The app also sends `07 01 02`, `10`, `26 01 05`, `2c 01 02` and `39 01 02`, which this dongle rejects with status `81` (probably meant for other Creative devices).
`btw5-switch dump` prints all of these, so if you have a different firmware or headset, its output is very welcome in an issue.

![](./btw5-wireshark.png)

## Development

```sh
python -m pytest            # protocol parsers against the capture, plus a fake dongle for the CLI
./btw5-switch.py status     # run from the checkout
```

Code layout: `btw5/protocol.py` (frames and parsers), `btw5/device.py` (USB access, requests), `btw5/broker.py` (sharing the dongle), `btw5/cli.py`, `btw5/tray.py`.
`aur/` holds the release PKGBUILD.

## Status

Tested on real hardware (firmware 1.2.230511.1220, Sennheiser MOMENTUM 4): `status`, `info`, `dump`, `paired`, aptX switching, `hfp`, `auto-hfp`, `disconnect`, `monitor` (including following the dongle through HFP re-enumeration and sharing it with other commands), `waybar` and the tray.
Not yet confirmed on hardware: `scan` actually listing a headset, `pair` completing a connection (so far the dongle refused because the headset wasn't reachable), and `forget`.
If something doesn't behave as expected, run it with `-v` and open an issue with the output.

Based on [RaphaelWimmer/btw5-switch](https://github.com/RaphaelWimmer/btw5-switch). Pull requests welcome.

## License

The code is released under the [MIT License](LICENSE).
The USB capture (`bt-w5.pcapng`), its screenshot (`btw5-wireshark.png`) and the original protocol notes are Raphael Wimmer's work from the upstream repository, which doesn't state a license yet.
