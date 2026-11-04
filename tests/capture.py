"""Minimal pcapng reader for the USBPcap capture in this repo (bt-w5.pcapng)."""

import os
import struct

CAPTURE = os.path.join(os.path.dirname(__file__), "..", "bt-w5.pcapng")


def usb_frames(path=CAPTURE):
    """Yield (direction, report) for every BT-W5 '03 5a' HID report in the capture.

    direction is '>' for host->dongle (SET_REPORT data) and '<' for dongle->host."""
    data = open(path, "rb").read()
    i = 0
    while i + 8 <= len(data):
        btype, blen = struct.unpack_from("<II", data, i)
        if btype == 6:  # Enhanced Packet Block
            caplen = struct.unpack_from("<I", data, i + 20)[0]
            pkt = data[i + 28:i + 28 + caplen]
            hdr_len = struct.unpack_from("<H", pkt, 0)[0]   # USBPcap header
            endpoint = pkt[21]
            payload = pkt[hdr_len:]
            j = payload.find(b"\x03\x5a")
            if j >= 0 and len(payload) - j >= 4:
                report = payload[j:]
                yield ("<" if endpoint & 0x80 else ">"), report[:4 + report[3]]
        i += blen
