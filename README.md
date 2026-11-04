# btw5-switch - control the Creative BT-W5 Bluetooth audio dongle on Linux

[![tests](https://github.com/doodersrage/btw5-switch/actions/workflows/test.yml/badge.svg)](https://github.com/doodersrage/btw5-switch/actions/workflows/test.yml)

Creative only makes its BT-W5 app for Windows and macOS. This does the same on Linux:
switch aptX Adaptive between High Quality and Low Latency, turn the headset microphone (HFP) on or off,
pair new headsets and switch between paired ones, and get a notification when a headset connects.

It comes with a settings window, a system tray icon, a command line tool and a status bar module.

![The BT-W5 Switch window](docs/window.png)

## What you need

- A Creative BT-W5 USB dongle (USB ID `041e:3130`). Check with `lsusb -d 041e:3130`.
- Linux with Python 3.9 or newer.
- For the window and tray icon: PyQt6 or PySide6. The install steps below include it.

Sound works without this tool: the BT-W5 is a standard USB sound card, and volume is controlled from your desktop as usual.
This tool is for the dongle's settings and pairing.

## Install

### Arch Linux and derivatives (Manjaro, EndeavourOS, CachyOS)

```sh
sudo pacman -S --needed base-devel git python-pyqt6
git clone https://github.com/doodersrage/btw5-switch
cd btw5-switch
makepkg -si
```

Then **unplug the dongle and plug it back in**, so your user gets access to it.

### Debian, Ubuntu, Linux Mint, Pop!_OS

```sh
sudo apt install git pipx libusb-1.0-0
git clone https://github.com/doodersrage/btw5-switch
cd btw5-switch
sudo ./install.sh
```

### Fedora

```sh
sudo dnf install git pipx libusb1
git clone https://github.com/doodersrage/btw5-switch
cd btw5-switch
sudo ./install.sh
```

### Other distributions

Install `git`, `pipx` and libusb 1.0 with your package manager, then clone and run `sudo ./install.sh` as above.

`install.sh` puts the program in `/opt/btw5-switch` (with the `btw5-switch` command in `/usr/local/bin`) and adds the device access rule, app menu entries and shell completions.
It downloads PyUSB and PyQt6 for the window; add `--no-gui` if you only want the command line tool.

Then **unplug the dongle and plug it back in**, so your user gets access to it.

## Getting started

Open **BT-W5 Switch** from your application menu, or run `btw5-switch gui`.
You'll see the connected headset, its aptX mode and your paired headsets. Everything else is a click away.

From a terminal, `btw5-switch status` shows the same:

```
$ btw5-switch status
Headset            : MOMENTUM 4 (00:1b:66:a1:b2:c3)
aptX Adaptive mode : High Quality
HFP (headset mic)  : off
Auto HFP switching : off
```

If that prints an error instead, see [Troubleshooting](#troubleshooting).

### Pairing a new headset

1. Put the headset in pairing mode (usually: hold the power or Bluetooth button until the light flashes).
2. In the window, click **Scan** (or run `btw5-switch scan`). This disconnects the current headset.
3. Click **Pair** next to your headset (or run `btw5-switch pair <address>`) while it's still in pairing mode.

Often the dongle doesn't even list the headset but connects to it straight away; the window then says so.
The dongle remembers up to 4 headsets. To switch to another one later, click **Connect** next to it, or run `btw5-switch pair <name>`.

### Tray icon

The tray icon shows the headset and settings when you hover over it. Left click switches between Low Latency and High Quality; its menu has the other settings, your paired headsets, and **Open BT-W5 Switch…**.

Start it with **BT-W5 Switch (tray icon)** from the application menu, or `btw5-switch tray`. To start it automatically when you log in:

```sh
mkdir -p ~/.config/autostart
cp /usr/share/applications/btw5-switch-tray.desktop ~/.config/autostart/
```

- **KDE Plasma** may put new icons behind the ▲ arrow in the panel. To keep it visible: right-click the arrow → *Configure System Tray…* → *Entries* → set "btw5-switch" to *Always shown*.
- **GNOME** has no tray of its own. Install the *AppIndicator and KStatusNotifierItem Support* extension (Ubuntu has it already), or use the window instead.

### Notifications

```sh
systemctl --user enable --now btw5-switch-monitor.service
```

shows a desktop notification whenever a headset connects or disconnects or a setting changes.

## Command line

```
btw5-switch status              # connected headset, aptX mode, HFP and auto-HFP settings
btw5-switch ll | hq             # aptX Adaptive Low Latency / High Quality
btw5-switch toggle              # switch between the two
btw5-switch hfp on|off          # headset microphone (HFP); audio drops for a few seconds
btw5-switch auto-hfp on|off     # switch to the microphone automatically when an app uses it
btw5-switch paired              # list the 4 pairing slots
btw5-switch pair momentum       # connect to a paired headset by (part of) its name
btw5-switch scan [seconds]      # disconnect the headset and list nearby headsets (default 15 s)
btw5-switch pair 68:00:1a:bc:5c:eb   # connect to an address shown by 'scan'
btw5-switch disconnect          # disconnect the current headset
btw5-switch forget              # forget all paired headsets (asks first; -y to skip)
btw5-switch info                # firmware version and serial number
btw5-switch gui                 # settings window
btw5-switch tray                # system tray icon
btw5-switch monitor [--notify]  # print (and notify about) headset and setting changes
btw5-switch waybar              # status as JSON for a status bar
btw5-switch dump                # every readable register (for bug reports)
btw5-switch -v <command>        # also show the raw USB traffic
```

`status`, `info`, `paired`, `scan` and `dump` take `--json`. Tab completion works in bash, zsh and fish, including headset names for `pair`.

### Status bar

`btw5-switch waybar` prints `{"text": "MOMENTUM 4 · HQ", "class": "connected", "tooltip": ...}` for a waybar or polybar module.
See [`contrib/waybar/`](contrib/waybar/) for a waybar module that toggles the aptX mode on click, and [`contrib/polybar.ini`](contrib/polybar.ini) for polybar.

## Troubleshooting

**"Creative BT-W5 not found"**
: The dongle isn't plugged in, or is restarting: it disappears for a few seconds after changing HFP or forgetting headsets. Check with `lsusb -d 041e:3130`.

**"Permission denied accessing the BT-W5"**
: Unplug the dongle and plug it back in. If that doesn't help, the access rule isn't installed: check that `/usr/lib/udev/rules.d/70-btw5-switch.rules` exists (if not, run `makepkg -si` or `sudo ./install.sh` again), then run `sudo udevadm control --reload-rules` and re-plug.
The rule gives access to whoever is logged in at the computer; over SSH, use `sudo btw5-switch ...`.

**"The BT-W5 is in use by another program"**
: Another program holds the dongle. Usually that's Creative's app in a Windows VM with USB passthrough, or an old `btw5-switch` that's stuck (`pkill -f btw5-switch`).

**aptX Adaptive mode shows "not available"**
: The connected headset doesn't use aptX Adaptive (for example it only supports SBC or AAC), or no headset is connected. The mode switch only works with aptX Adaptive headsets.

**"The dongle refused to connect" (status 0x80)**
: The dongle can't reach the headset. Turn the headset on, make sure it isn't connected to your phone or another computer, or put it in pairing mode and scan again.

**The window or tray says it needs PyQt6 or PySide6**
: Arch: `sudo pacman -S python-pyqt6`. Other distributions: run `sudo ./install.sh` again (without `--no-gui`).

**No tray icon**
: See the KDE and GNOME notes under [Tray icon](#tray-icon).

**Something else**
: Run the command with `-v` (for example `btw5-switch -v status`) and [open an issue](https://github.com/doodersrage/btw5-switch/issues) with the output and the output of `btw5-switch dump`. In the window, **Copy diagnostics** copies the same information.

## Updating and uninstalling

Update: in the `btw5-switch` folder, run `git pull`, then `makepkg -si` (Arch) or `sudo ./install.sh` (everything else).

Uninstall: `sudo pacman -R btw5-switch` (Arch) or, in the `btw5-switch` folder, `sudo ./install.sh --uninstall`.

Either way, also remove `~/.config/autostart/btw5-switch-tray.desktop` if you set up the tray to start at login, and run `systemctl --user disable --now btw5-switch-monitor.service` if you enabled notifications.

## How it works

### Creative BT-W5

The Creative BT-W5 is a USB Bluetooth audio dongle.
It is *not* a normal Bluetooth dongle, i.e. it cannot be used for connecting to non-audio devices.
Instead, it acts as an USB audio device towards the computer and as Bluetooth host towards wireless headsets.

### Sharing the dongle

Only one program can open the dongle's control interface at a time.
While `btw5-switch monitor` runs (for example as the notification service), it shares the dongle over a Unix socket (`$XDG_RUNTIME_DIR/btw5-switch.sock`), and all other `btw5-switch` commands, the window, the tray and the status bar module go through it automatically.
Short-lived commands that find the dongle busy wait up to 3 seconds for it.

### Protocol

The BT-W5 appears as a USB sound card (interfaces 2-4, no driver needed) plus two HID interfaces.
Configuration goes through HID interface 0. The tool sends output report 3 as a `SET_REPORT` control transfer, and replies come back as input report 3 on interrupt endpoint `0x81`.
The interface's report descriptor doesn't declare input report 3, so the kernel drops the replies and `/dev/hidraw` can't be used; the tool talks to the interface directly with PyUSB.
The communication between the Creative app on Windows and the dongle was logged with [Wireshark](https://www.wireshark.org/) and [USBPcap](https://github.com/desowin/usbpcap) and annotated: [bt-w5.pcapng](bt-w5.pcapng).

Every report is `03 5a <cmd> <len> <payload>`, zero-padded to 65 bytes.

| Command (cmd payload) | Meaning | Reply |
|---|---|---|
| `6b 0a 03 20` / `6b 0a 03 40` | aptX Adaptive LL / HQ | ack `02 0a 6b 00 0a 03 <val>`, `6b 09 08 <val>` |
| `6b 0a 01 01` / `6b 0a 01 02` | HFP on / off | ack, then USB re-enumeration |
| `6b 0a 02 01` / `6b 0a 02 00` | auto-HFP on / off | ack, `6b 09 07 <val>` |
| `6b 09 <key>` | read setting: `03` aptX, `06` HFP, `07` auto-HFP, `08` active aptX mode | `6b 09 <key> <val>` |
| `6b 04 01` | disconnect and scan | `6b 05 <addr6> <nlen> <name> <rssi int16 LE>` per device, or `6b 06` connection events if it connects to a headset in pairing mode by itself |
| `6b 04 02 <addr6>` | connect to address | ack (status `80` if the headset can't be reached), then `6b 06 08`/`0b` |
| `6b 06` | current connection | `6b 06 <state> <addr6> 20 <name[32]>`; no reply right after a scan disconnect |
| `6b 02 <slot>` | read pairing slot 0-3 | `6b 02 <slot> 80 <addr6> 1f <name[31]>` (`00` instead of `80` past the last slot) |
| `6b 07 ff` | forget all pairings | USB re-enumeration |
| `09 02` / `09 03` | firmware version / serial | `09 <field> <ascii>` |

Acks look like `02 0a <cmd> <status> <echo>`, where the echo repeats the start of the request (all of `0a <key> <val>` for settings). Status `00` means OK, `80` means rejected right now (for example `6b 09 03` with no aptX Adaptive headset connected), `81` means unknown command.

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
python -m pytest            # protocol parsers against the capture, a fake dongle for the CLI and window
./btw5-switch.py status     # run from the checkout without installing
python docs/screenshot.py docs/window.png   # regenerate the README screenshot
```

Code layout: `btw5/protocol.py` (frames and parsers), `btw5/device.py` (USB access, requests), `btw5/broker.py` (sharing the dongle), `btw5/cli.py`, `btw5/window.py` and `btw5/tray.py` (sharing `btw5/qt.py`).
`aur/` holds the release PKGBUILD.

## Status

Tested on real hardware (firmware 1.2.230511.1220, Sennheiser MOMENTUM 4): `status`, `info`, `dump`, `paired`, aptX switching, `hfp`, `auto-hfp`, `disconnect`, `monitor` (including following the dongle through HFP re-enumeration and sharing it with other commands), `waybar`, the tray and the settings window.
Not yet confirmed on hardware: `scan` actually listing a headset, `pair` completing a connection (so far the dongle refused because the headset wasn't reachable), and `forget`.
The install steps for Ubuntu and Fedora are run automatically on every change (see the tests badge).

Based on [RaphaelWimmer/btw5-switch](https://github.com/RaphaelWimmer/btw5-switch). Pull requests welcome.

## License

The code is released under the [MIT License](LICENSE).
The USB capture (`bt-w5.pcapng`), its screenshot (`btw5-wireshark.png`) and the original protocol notes are Raphael Wimmer's work from the upstream repository, which doesn't state a license yet.
