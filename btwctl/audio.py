"""Default audio device switching through PipeWire (pw-dump / wpctl).

The dongle stays a USB sound card while no headset is connected, so audio
played to it then goes nowhere. "Audio follows the headset" makes the
dongle the default output (and, with HFP on, the default microphone) while a
headset is connected, and switches back to what was in use before when it
disconnects.

The dongle's PipeWire node names change every time it re-enumerates, so it's
found by the USB IDs of the node's parent device.
"""

import json
import shutil
import subprocess

from . import device


def _run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None


def available():
    return bool(shutil.which("pw-dump") and shutil.which("wpctl"))


def snapshot(dump=None):
    """{'sinks': [...], 'sources': [...], 'default_sink': name, 'default_source': name}

    Each node is {'id', 'name', 'description', 'dongle', 'priority'}. `dump` is
    pw-dump's parsed JSON (for tests); None runs pw-dump."""
    if dump is None:
        r = _run("pw-dump")
        if r is None or r.returncode != 0:
            return None
        try:
            dump = json.loads(r.stdout)
        except ValueError:
            return None
    devices = {o["id"]: (o.get("info") or {}).get("props") or {}
               for o in dump if o.get("type") == "PipeWire:Interface:Device"}
    out = {"sinks": [], "sources": [], "default_sink": None, "default_source": None}
    for o in dump:
        if o.get("type") == "PipeWire:Interface:Metadata" and (o.get("props") or {}).get("metadata.name") == "default":
            for m in o.get("metadata") or []:
                value = m.get("value")
                name = value.get("name") if isinstance(value, dict) else None
                if m.get("key") == "default.audio.sink":
                    out["default_sink"] = name
                elif m.get("key") == "default.audio.source":
                    out["default_source"] = name
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        props = (o.get("info") or {}).get("props") or {}
        cls = props.get("media.class")
        if cls not in ("Audio/Sink", "Audio/Source"):
            continue
        dev = devices.get(props.get("device.id"), {})
        node = {"id": o["id"], "name": props.get("node.name"),
                "description": props.get("node.description") or props.get("node.name"),
                "dongle": is_dongle(dev.get("device.vendor.id"), dev.get("device.product.id")),
                "priority": int(props.get("priority.session") or 0)}
        out["sinks" if cls == "Audio/Sink" else "sources"].append(node)
    return out


def is_dongle(vendor, product):
    try:
        vid, pid = int(str(vendor), 16), int(str(product), 16)
    except (TypeError, ValueError):
        return False
    if not device.auto_detect():
        return (vid, pid) == (device.VENDOR_ID, device.PRODUCT_ID)
    return vid == 0x041e and pid in device.MODELS


def set_default(node_id):
    r = _run("wpctl", "set-default", str(node_id))
    return r is not None and r.returncode == 0


class AudioFollower:
    """Moves the default output (and microphone) to the dongle and back.

    Only acts on transitions it's told about (headset connected/disconnected,
    HFP on/off) and only when the current default is what it expects, so a
    choice the user made in between is left alone."""

    def __init__(self, snap=snapshot, set_default=set_default):
        self.snap = snap
        self.set_default_fn = set_default
        self.previous = {"sinks": None, "sources": None}   # node names to go back to

    def _pick(self, nodes, name):
        return next((n for n in nodes if n["name"] == name), None)

    def _fallback(self, nodes):
        others = [n for n in nodes if not n["dongle"]]
        return max(others, key=lambda n: n["priority"]) if others else None

    def to_dongle(self, kind):
        """kind: 'sinks' or 'sources'. Returns a description of what changed, or None.
        None also when the dongle's node isn't there (yet): the caller may retry."""
        s = self.snap()
        if not s:
            return None
        dongle = next((n for n in s[kind] if n["dongle"]), None)
        if dongle is None:
            return None
        current = s["default_sink" if kind == "sinks" else "default_source"]
        if current == dongle["name"]:
            return ""          # already there
        cur = self._pick(s[kind], current)
        if cur and not cur["dongle"]:
            self.previous[kind] = cur["name"]
        if self.set_default_fn(dongle["id"]):
            return dongle["description"]
        return None

    def from_dongle(self, kind):
        """Back to the previous default if the dongle is still the default."""
        s = self.snap()
        if not s:
            return None
        current = self._pick(s[kind], s["default_sink" if kind == "sinks" else "default_source"])
        if current is None or not current["dongle"]:
            return ""          # the user already picked something else
        target = self._pick(s[kind], self.previous[kind]) or self._fallback(s[kind])
        if target is None:
            return ""
        if self.set_default_fn(target["id"]):
            return target["description"]
        return None
