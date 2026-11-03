# btw5-switch - control the Creative BT-W5 Bluetooth audio dongle on Linux

Switch aptX Adaptive modes, toggle HFP (headset microphone), scan for, pair and switch between headsets, watch connection events, and read device info, all without Creative's Windows/macOS app.

## Install

### Arch Linux

```sh
git clone https://github.com/doodersrage/btw5-switch
cd btw5-switch
makepkg -si          # installs /usr/bin/btw5-switch, python-pyusb and a udev rule
```

Re-plug the dongle once so the udev rule applies. After that, no sudo is needed.

### Other distributions

1. Install Python and [PyUSB](https://github.com/pyusb/pyusb) (Debian/Ubuntu: `apt install python3-usb`, Fedora: `dnf install python3-pyusb`).
2. Run `./btw5-switch.py ...` with `sudo`, **or** allow your user access to the dongle:
   ```sh
   sudo cp 70-btw5-switch.rules /etc/udev/rules.d/
   sudo udevadm control --reload-rules
   # re-plug the dongle
   ```

## Usage

```
btw5-switch status              # connected headset, aptX mode, HFP and auto-HFP settings
btw5-switch info                # firmware version and serial number
btw5-switch ll                  # aptX Adaptive Low Latency
btw5-switch hq                  # aptX Adaptive High Quality
btw5-switch hfp on|off          # headset microphone (HFP) support
btw5-switch auto-hfp on|off     # automatically switch to HFP when the mic is used
btw5-switch scan [seconds]      # disconnect the headset and list nearby headsets (default 15 s)
btw5-switch pair 68:00:1a:bc:5c:eb   # connect to an address shown by 'scan'
btw5-switch pair momentum       # ... or to a remembered headset by (part of) its name
btw5-switch paired              # list the 4 pairing slots
btw5-switch monitor             # print connect/disconnect and setting changes live (Ctrl-C)
btw5-switch disconnect          # disconnect the current headset
btw5-switch forget              # forget all paired headsets (asks first; -y to skip)
btw5-switch -v <command>        # also hex-dump the USB traffic
btw5-switch raw 6b 09 03        # send a raw command and print replies (protocol exploration)
```

Typical pairing flow: put the headset in pairing mode, run `btw5-switch scan`, then `btw5-switch pair <address>`.
To switch between headsets you've already paired, `btw5-switch pair <name>` is enough.

Notes:
- `hfp` and `forget` make the dongle re-enumerate on USB, so it briefly disappears and comes back as a new audio device.
- Volume is a standard USB Audio control, so use your desktop's volume slider, `wpctl` or `alsamixer`.
- `monitor` holds the dongle open, so other `btw5-switch` commands report it as busy until you stop it.

## Creative BT-W5

The Creative BT-W5 is a USB Bluetooth audio dongle.
It is *not* a normal Bluetooth dongle, i.e. it cannot be used for connecting to non-audio devices.
Instead, it acts as an USB audio device towards the computer and as Bluetooth host towards wireless headsets.

## Protocol

The BT-W5 appears as a USB sound card (interfaces 2-4, no driver needed) plus two HID interfaces.
Configuration goes through HID interface 0. The tool sends output report 3 as a `SET_REPORT` control transfer, and replies come back as input report 3 on interrupt endpoint `0x81`.
The communication between the Creative app on Windows and the dongle was logged with [Wireshark](https://www.wireshark.org/) and [USBPcap](https://github.com/desowin/usbpcap) and annotated: [bt-w5.pcapng](bt-w5.pcapng).

Every report is `03 5a <cmd> <len> <payload>`, zero-padded to 65 bytes.

| Command (cmd payload) | Meaning | Reply |
|---|---|---|
| `6b 0a 03 20` / `6b 0a 03 40` | aptX Adaptive LL / HQ | ack `02 0a 6b 00 0a` |
| `6b 0a 01 01` / `6b 0a 01 02` | HFP on / off | ack, then USB re-enumeration |
| `6b 0a 02 01` / `6b 0a 02 00` | auto-HFP on / off | ack, `6b 09 07 <val>` |
| `6b 09 <key>` | read setting: `03` aptX, `06` HFP, `07` auto-HFP | `6b 09 <key> <val>` |
| `6b 04 01` | disconnect and scan | `6b 05 <addr6> <nlen> <name> <rssi int16 LE>` per device |
| `6b 04 02 <addr6>` | connect to address | `6b 06 08/0b <addr6> ...` on connect |
| `6b 06` | current connection | `6b 06 <state> <addr6> 20 <name[32]>` |
| `6b 02 <slot>` | read pairing slot 0-3 | `6b 02 <slot> 80 <addr6> 1f <name[31]>` (`00` instead of `80` past the last slot) |
| `6b 07 ff` | forget all pairings | USB re-enumeration |
| `09 02` / `09 03` | firmware version / serial | `09 <field> <ascii>` |

Acks look like `02 0a 6b <status> <sub>`. Status `00` means OK. `80` means rejected, for example `6b 09 03` (read aptX mode) when no aptX Adaptive headset is connected.

Headset connection events arrive unprompted in the same `6b 06` format (`08` connecting, `0b` connected, `0a` disconnected, `06` seen while the link drops).
Setting changes are also announced unprompted as `6b 09 <key> <val>`.

Seen but not yet understood: `6b 09 01` → `04`, `6b 09 02` → `75`, `6b 09 04` → 6 bytes, `6b 09 05` → `03`, `6b 01` → `01 01`, `09 00` (contains vendor ID `041e`), `09 01 00` (6 bytes, possibly the dongle's own Bluetooth address). The app also sends `07 01 02`, `10`, `26 01 05`, `2c 01 02` and `39 01 02`, which this dongle rejects with ack status `81`.

![](./btw5-wireshark.png)

## Status

All commands were derived from the capture. `status`, `info`, `paired` and `monitor` have been tested on real hardware (firmware 1.2.230511.1220); the others have only been checked against a replay of the capture. aptX LL/HQ switching is unchanged from the original script.
If something doesn't behave as expected, run it with `-v` and open an issue with the output.

Based on [RaphaelWimmer/btw5-switch](https://github.com/RaphaelWimmer/btw5-switch). Pull requests welcome.
