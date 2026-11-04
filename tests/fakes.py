"""A scripted stand-in for the dongle."""

import time

from btw5 import protocol as P

HEADSET = bytes.fromhex("ba52e90980c3")
NAME = b"MOMENTUM 4"


def report(cmd, payload):
    payload = bytes(payload)
    return bytes([P.REPORT_ID, P.MAGIC, cmd, len(payload)]) + payload


def ack(cmd, status, sub):
    return report(P.CMD_ACK, [cmd, status, sub])


def conn_frame(state, addr=HEADSET, name=NAME):
    return report(P.CMD_BT, bytes([P.BT_CONN, state]) + addr + bytes([32]) + name.ljust(32, b"\0"))


def scan_frame(addr, name, rssi):
    return report(P.CMD_BT, bytes([0x05]) + addr + bytes([len(name)]) + name
                  + rssi.to_bytes(2, "little", signed=True) + b"\0")


def slot_frame(slot, addr=bytes(6), name=b"", valid=True):
    return report(P.CMD_BT, bytes([P.BT_SLOT, slot, 0x80 if valid else 0]) + addr + bytes([31])
                  + name.ljust(31, b"\0") + bytes(4))


class FakeDongle:
    """Transport that answers like a BT-W5 with one paired, connected headset."""

    def __init__(self, connected=True, aptx=0x40, hfp=0x02, auto=0x00, connect_status=0x00,
                 scan_frames=()):
        self.connected = connected
        self.settings = {P.GET_APTX: aptx, P.GET_APTX_ACTIVE: aptx, P.GET_HFP: hfp, P.GET_AUTO_HFP: auto}
        self.connect_status = connect_status
        self.scan_frames = list(scan_frames)  # reports sent after a scan starts
        self.queue = []
        self.sent = []
        self.opened = False

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False

    def write(self, data):
        cmd, p = P.split_report(data)
        self.sent.append((cmd, bytes(p)))
        q = self.queue
        if cmd == P.CMD_BT and p[0] == P.BT_GET:
            key = p[1]
            if key == P.GET_APTX and not self.connected:
                q.append(ack(P.CMD_BT, 0x80, P.BT_GET))
            elif key in self.settings:
                q.append(report(P.CMD_BT, [P.BT_GET, key, self.settings[key]]))
            else:
                q.append(ack(P.CMD_BT, 0x81, P.BT_GET))
        elif cmd == P.CMD_BT and p[0] == P.BT_SET:
            key, val = p[1], p[2]
            q.append(ack(P.CMD_BT, 0x00, P.BT_SET))
            if key == P.SET_APTX:
                self.settings[P.GET_APTX] = self.settings[P.GET_APTX_ACTIVE] = val
                q.append(report(P.CMD_BT, [P.BT_GET, P.GET_APTX_ACTIVE, val]))
            elif key == P.SET_AUTO_HFP:
                self.settings[P.GET_AUTO_HFP] = val
                q.append(report(P.CMD_BT, [P.BT_GET, P.GET_AUTO_HFP, val]))
        elif cmd == P.CMD_BT and p[0] == P.BT_CONN:
            if self.connected:
                q += [conn_frame(P.CONN_CONNECTING), conn_frame(P.CONN_CONNECTED)]
        elif cmd == P.CMD_BT and p[0] == P.BT_SLOT:
            slot = p[1]
            q.append(ack(P.CMD_BT, 0x00, P.BT_SLOT))
            if slot == 0:
                q.append(slot_frame(0, HEADSET, NAME))
            else:
                q.append(slot_frame(slot, valid=slot < P.PAIR_SLOTS))
        elif cmd == P.CMD_BT and p[0] == P.BT_SCAN and p[1] == P.SCAN_START:
            q.append(ack(P.CMD_BT, 0x00, P.BT_SCAN))
            if self.connected:
                q.append(conn_frame(P.CONN_DISCONNECTED))
            self.connected = False
            q += self.scan_frames
        elif cmd == P.CMD_BT and p[0] == P.BT_SCAN and p[1] == P.SCAN_CONNECT:
            q.append(ack(P.CMD_BT, self.connect_status, P.BT_SCAN))
            if self.connect_status == 0:
                self.connected = True
                q += [conn_frame(P.CONN_CONNECTING), conn_frame(P.CONN_CONNECTED)]
        elif cmd == P.CMD_INFO:
            field = p[0]
            text = {P.INFO_FIRMWARE: b"\x101.2.230511.1220", P.INFO_SERIAL: b"YDSA0180415000934F"}
            q.append(report(P.CMD_INFO, bytes([field]) + text.get(field, b"")))
        else:
            q.append(ack(cmd, 0x81, p[0] if p else 0))

    def read(self, timeout_ms):
        if self.queue:
            return self.queue.pop(0).ljust(P.REPORT_LEN, b"\0")
        time.sleep(min(timeout_ms, 5) / 1000)
        return None
