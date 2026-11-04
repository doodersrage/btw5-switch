"""Creative BT-W5 HID protocol: constants and frame parsers.

Reverse engineered from the traffic between Creative's Windows app and the
BT-W5 (see bt-w5.pcapng) and from probing a real dongle.

Requests are HID output report 3 on interface 0, sent as a SET_REPORT control
transfer. Replies arrive as input report 3 on interrupt endpoint 0x81:

    03 5a <cmd> <len> <payload[len]>   (zero-padded to 65 bytes)

    cmd 0x6b  settings / Bluetooth
        0a <key> <val>      set setting      (key 01 HFP, 02 auto-HFP, 03 aptX mode)
        09 <key>            get setting  ->  6b 03 09 <key> <val>
        04 01               disconnect headset and start scanning
                            ->  6b <n> 05 <addr6> <nlen> <name[nlen]> <rssi int16le> ..
        04 02 <addr6>       connect to address (rejected with status 80 unless the
                            headset is reachable, e.g. found by the current scan)
        06                  query connection -> 6b 29 06 <state> <addr6> <32> <name[32]>
                            (the same frame is also sent unprompted on (dis)connect)
        02 <slot>           read pairing slot 0-3
                            ->  6b 2d 02 <slot> <80=valid> <addr6> <31> <name[31]> ..
        07 ff               forget all paired devices
    cmd 0x09  device info
        02                  firmware version
        03                  serial number
    acks:  5a 02 0a <cmd> <status> <first payload byte>
           status 00 = OK, 80 = rejected right now, 81 = unknown command
"""

REPORT_ID = 0x03
MAGIC = 0x5a
REPORT_LEN = 65

CMD_ACK = 0x02
CMD_INFO = 0x09
CMD_BT = 0x6b

# 6b sub-commands
BT_SLOT, BT_SCAN, BT_CONN, BT_FORGET, BT_GET, BT_SET = 0x02, 0x04, 0x06, 0x07, 0x09, 0x0a
SCAN_START, SCAN_CONNECT = 0x01, 0x02

# setting keys used with "set" (0a) ...
SET_HFP, SET_AUTO_HFP, SET_APTX = 0x01, 0x02, 0x03
# ... and the keys that read the current state back (09)
GET_HFP, GET_AUTO_HFP, GET_APTX, GET_APTX_ACTIVE = 0x06, 0x07, 0x03, 0x08
# readable keys whose meaning is unknown (constant across aptX mode, volume,
# HFP and connection changes on firmware 1.2.230511.1220)
GET_KEYS_ALL = range(0x00, 0x09)

INFO_FIRMWARE, INFO_SERIAL = 0x02, 0x03

APTX = {"ll": 0x20, "hq": 0x40}
APTX_NAMES = {0x20: "Low Latency", 0x40: "High Quality"}
APTX_SHORT = {0x20: "LL", 0x40: "HQ"}
HFP_VALUES = {"on": 0x01, "off": 0x02}
AUTO_HFP_VALUES = {"on": 0x01, "off": 0x00}

STATUS_OK, STATUS_REJECTED, STATUS_UNKNOWN_CMD = 0x00, 0x80, 0x81

# connection states in 6b 06 frames
CONN_NOT_CONNECTED, CONN_CONNECTING, CONN_DISCONNECTED, CONN_CONNECTED = 0x06, 0x08, 0x0a, 0x0b
CONN_STATES = {CONN_NOT_CONNECTED: "not connected", CONN_CONNECTING: "connecting",
               CONN_DISCONNECTED: "disconnected", CONN_CONNECTED: "connected"}

PAIR_SLOTS = 4
ZERO_ADDR = bytes(6)


def build_report(cmd, payload):
    data = bytes([REPORT_ID, MAGIC, cmd, len(payload)]) + bytes(payload)
    return data.ljust(REPORT_LEN, b"\0")


def split_report(raw):
    """Input report -> (cmd, payload). cmd is None for reports that aren't 03 5a frames."""
    raw = bytes(raw)
    if len(raw) < 4 or raw[0] != REPORT_ID:
        return None, raw
    if raw[1] != MAGIC:
        return raw[1], raw[2:]  # other report family
    n = raw[3]
    return raw[2], raw[4:4 + n].ljust(n, b"\0")


def hexs(b):
    return " ".join(f"{x:02x}" for x in b)


def parse_addr(s):
    """'68:00:1a:bc:5c:eb' (or with dashes) -> bytes; ValueError if malformed."""
    parts = s.replace("-", ":").split(":")
    if len(parts) != 6 or not all(1 <= len(p) <= 2 for p in parts):
        raise ValueError("address must look like 68:00:1a:bc:5c:eb")
    return bytes(int(p, 16) for p in parts)


def fmt_addr(b):
    return ":".join(f"{x:02x}" for x in b)


def cstr(b):
    return bytes(b).split(b"\0")[0].decode("utf-8", "replace")


def parse_ack(cmd, payload):
    """Ack frame -> (acked_cmd, status, sub) or None. sub is None when absent."""
    if cmd != CMD_ACK or len(payload) < 2:
        return None
    return payload[0], payload[1], (payload[2] if len(payload) > 2 else None)


def parse_conn(p):
    """6b 06 payload -> (state, addr, name) or None."""
    if len(p) < 9 or p[0] != BT_CONN:
        return None
    return p[1], bytes(p[2:8]), cstr(p[9:9 + p[8]])


def parse_scan(p):
    """6b 05 payload -> (addr, name, rssi) or None."""
    if len(p) < 8 or p[0] != 0x05:
        return None
    n = p[7]
    if len(p) < 10 + n:
        return None
    return bytes(p[1:7]), cstr(p[8:8 + n]), int.from_bytes(p[8 + n:10 + n], "little", signed=True)


def parse_slot(p):
    """6b 02 payload -> (slot, valid, addr, name) or None."""
    if len(p) < 10 or p[0] != BT_SLOT:
        return None
    return p[1], bool(p[2] & 0x80), bytes(p[3:9]), cstr(p[10:10 + p[9]])


def parse_setting(p):
    """6b 09 payload -> (key, value) or None."""
    if len(p) < 3 or p[0] != BT_GET:
        return None
    return p[1], p[2]


def headset_label(addr, name):
    return f"{name} ({fmt_addr(addr)})" if name else fmt_addr(addr)


def conn_state_name(state):
    return CONN_STATES.get(state, f"state 0x{state:02x}")


def onoff(value, on):
    return "on" if value == on else "off"


def setting_text(key, value):
    """(setting name, human value) for keys we understand, else None."""
    if key in (GET_APTX, GET_APTX_ACTIVE):
        name = "aptX Adaptive mode" + (" (active)" if key == GET_APTX_ACTIVE else "")
        return name, APTX_NAMES.get(value, f"0x{value:02x}")
    if key == GET_HFP:
        return "HFP", onoff(value, HFP_VALUES["on"])
    if key == GET_AUTO_HFP:
        return "auto HFP", onoff(value, AUTO_HFP_VALUES["on"])
    return None


def describe_event(cmd, p):
    """Human-readable line for an input report, or None if we can't decode it."""
    if cmd != CMD_BT or not p:
        return None
    conn = parse_conn(p)
    if conn:
        return f"headset {conn_state_name(conn[0])}: {headset_label(conn[1], conn[2])}"
    r = parse_scan(p)
    if r:
        return f"scan result: {fmt_addr(r[0])} {r[2]} dBm {r[1]}"
    s = parse_setting(p)
    if s and setting_text(*s):
        name, val = setting_text(*s)
        return f"{name}: {val}"
    return None


# requests whose replies describe the current state (used to seed ChangeTracker)
STATE_QUERIES = [(CMD_BT, [BT_CONN]), (CMD_BT, [BT_GET, GET_APTX]),
                 (CMD_BT, [BT_GET, GET_HFP]), (CMD_BT, [BT_GET, GET_AUTO_HFP])]


class ChangeTracker:
    """Turns the dongle's report stream into a list of state *changes*.

    Several processes may share the dongle (see broker.py), so the stream also
    contains the replies to their status queries. Only reports that change the
    known state are interesting; the first value seen for anything just sets
    the baseline (printed, but not worth a notification)."""

    def __init__(self):
        self.conn = None
        self.settings = {}

    def feed(self, cmd, p):
        """-> (text, notification) if this report changes the state, else None.

        notification is a (summary, body) tuple, or None for baseline values."""
        if cmd != CMD_BT or not p:
            return None
        conn = parse_conn(p)
        if conn:
            state, addr, name = conn
            if state == CONN_CONNECTING:   # transient; also sent before every 'connected' reply
                return None
            first = self.conn is None
            if (state, addr) == self.conn:
                return None
            self.conn = (state, addr)
            label = headset_label(addr, name)
            text = f"headset {conn_state_name(state)}: {label}"
            return text, None if first else (f"Headset {conn_state_name(state)}", label)
        s = parse_setting(p)
        if s and setting_text(*s):
            key, value = s
            if self.settings.get(key) == value:
                return None
            first = key not in self.settings
            self.settings[key] = value
            name, val = setting_text(key, value)
            quiet = first or key == GET_APTX_ACTIVE
            return f"{name}: {val}", None if quiet else (f"{name}: {val}", "")
        r = parse_scan(p)
        if r:
            return f"scan result: {fmt_addr(r[0])} {r[2]} dBm {r[1]}", None
        return None
