# btw5-switch - control the Creative BT-W5 Bluetooth audio dongle on Linux

Switch aptX Adaptive modes, toggle HFP (headset microphone), scan for and pair headsets, and read device info, all without Creative's Windows/macOS app.

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
btw5-switch status              # aptX mode, HFP and auto-HFP settings
btw5-switch info                # firmware version and serial number
btw5-switch ll                  # aptX Adaptive Low Latency
btw5-switch hq                  # aptX Adaptive High Quality
btw5-switch hfp on|off          # headset microphone (HFP) support
btw5-switch auto-hfp on|off     # automatically switch to HFP when the mic is used
btw5-switch scan [seconds]      # disconnect the headset and list nearby headsets (default 15 s)
btw5-switch pair 68:00:1a:bc:5c:eb   # connect to an address shown by 'scan'
btw5-switch disconnect          # disconnect the current headset
btw5-switch forget              # forget all paired headsets (asks first; -y to skip)
btw5-switch -v <command>        # also hex-dump the USB traffic
btw5-switch raw 6b 09 03        # send a raw command and print replies (protocol exploration)
```

Typical pairing flow: put the headset in pairing mode, run `btw5-switch scan`, then `btw5-switch pair <address>`.

Notes:
- `hfp` and `forget` make the dongle re-enumerate on USB, so it briefly disappears and comes back as a new audio device.
- Volume is a standard USB Audio control, so use your desktop's volume slider, `wpctl` or `alsamixer`.
- If a headset in pairing mode is nearby, the dongle often connects to it by itself during `scan`. The tool then reports the connection instead of a list.

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
| `6b 04 01` | disconnect and scan | `6b 05 <addr6> <namelen> <name> <rssi int16 LE>` per device, or a connection event if it connects by itself |
| `6b 02 00` | connected headset | `6b 02 00 80 <addr6> 1f <name>` |
| `6b 04 02 <addr6>` | connect to address | `6b 06 08/0b <addr6> ...` on connect |
| `6b 07 ff` | forget all pairings | USB re-enumeration |
| `09 02` / `09 03` | firmware version / serial | `09 <field> <ascii>` |

Acks look like `02 0a 6b <status> <sub>`. Status `00` means OK. `80` means rejected, for example `6b 09 03` (read aptX mode) when no aptX Adaptive headset is connected.

Headset connection events arrive as `6b 06 <state> <addr6> 20 <name>`: `06` connecting, `08` connected, `0b` audio ready, `0a` disconnected.

![](./btw5-wireshark.png)

## Status

All commands were derived from the capture and checked against a replay of it, but haven't all been tested on real hardware yet. aptX LL/HQ switching is unchanged from the original script.
If something doesn't behave as expected, run it with `-v` and open an issue with the output.

Based on [RaphaelWimmer/btw5-switch](https://github.com/RaphaelWimmer/btw5-switch). Pull requests welcome.
