from btwctl import audio
from btwctl import protocol as P
from btwctl.audio import AudioFollower
from tests.fakes import HEADSET, conn_frame


def node(id, name, cls, dev, prio=1000):
    return {"id": id, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": name, "node.description": name.title(), "media.class": cls,
                               "device.id": dev, "priority.session": prio}}}


def dev(id, vid, pid):
    return {"id": id, "type": "PipeWire:Interface:Device",
            "info": {"props": {"device.vendor.id": vid, "device.product.id": pid}}}


def meta(sink, source):
    return {"id": 30, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
            "metadata": [{"key": "default.audio.sink", "value": {"name": sink}},
                         {"key": "default.audio.source", "value": {"name": source}}]}


def dump(sink="fiio", source="webcam", dongle=True):
    d = [dev(83, "0x2972", "0x0047"), dev(52, "0x046d", "0x08e5"),
         node(53, "fiio", "Audio/Sink", 83, 1109), node(99, "hdmi", "Audio/Sink", 64, 696),
         node(100, "webcam", "Audio/Source", 52), meta(sink, source)]
    if dongle:   # the node names change on every re-enumeration; the USB IDs don't
        d += [dev(106, "0x041e", "0x3130"), node(67, "bt-w5-out-D97E", "Audio/Sink", 106, 1109),
              node(132, "bt-w5-mic-D97E", "Audio/Source", 106)]
    return d


def test_snapshot_finds_the_dongle_by_usb_id():
    s = audio.snapshot(dump())
    assert [n["name"] for n in s["sinks"] if n["dongle"]] == ["bt-w5-out-D97E"]
    assert [n["name"] for n in s["sources"] if n["dongle"]] == ["bt-w5-mic-D97E"]
    assert s["default_sink"] == "fiio" and s["default_source"] == "webcam"


class FakePipeWire:
    def __init__(self, **kw):
        self.state = dict(sink="fiio", source="webcam", dongle=True)
        self.state.update(kw)
        self.sets = []

    def snap(self):
        return audio.snapshot(dump(**self.state))

    def set_default(self, node_id):
        self.sets.append(node_id)
        s = audio.snapshot(dump(**self.state))
        name = next(n["name"] for n in s["sinks"] + s["sources"] if n["id"] == node_id)
        self.state["sink" if any(n["id"] == node_id for n in s["sinks"]) else "source"] = name
        return True

    def follower(self):
        return AudioFollower(snap=self.snap, set_default=self.set_default)


def test_to_dongle_and_back():
    pw = FakePipeWire()
    f = pw.follower()
    assert f.to_dongle("sinks") == "Bt-W5-Out-D97E"
    assert pw.state["sink"] == "bt-w5-out-D97E"
    assert f.to_dongle("sinks") == ""                 # already there: nothing to do
    assert f.from_dongle("sinks") == "Fiio"
    assert pw.state["sink"] == "fiio"


def test_leaves_the_users_choice_alone():
    pw = FakePipeWire()
    f = pw.follower()
    f.to_dongle("sinks")
    pw.state["sink"] = "hdmi"                         # the user picked HDMI meanwhile
    assert f.from_dongle("sinks") == ""
    assert pw.state["sink"] == "hdmi"


def test_back_without_a_remembered_device_uses_the_preferred_one():
    pw = FakePipeWire(sink="bt-w5-out-D97E")          # dongle was already the default
    f = pw.follower()
    assert f.from_dongle("sinks") == "Fiio"           # highest priority non-dongle output


def test_dongle_sound_card_not_there_yet():
    pw = FakePipeWire(dongle=False)
    assert pw.follower().to_dongle("sinks") is None   # caller retries


# --- in the automation ---------------------------------------------------------

class RecordingAudio:
    def __init__(self):
        self.calls = []

    def to_dongle(self, kind):
        self.calls.append(("to", kind))
        return "Creative BT-W5"

    def from_dongle(self, kind):
        self.calls.append(("from", kind))
        return "FiiO K7"


def rig(tmp_path, follow=True):
    from btwctl.automation import Automation
    from btwctl.config import Config
    cfg = Config(str(tmp_path / "c.ini"))
    cfg.audio_follow = follow
    t = P.ChangeTracker()
    clock = {"now": 0.0}
    rec = RecordingAudio()
    said = []
    a = Automation(cfg, t, send=lambda *_: None, say=said.append, proc=str(tmp_path),
                   clock=lambda: clock["now"], audio=rec)

    def feed(payload):
        t.feed(P.CMD_BT, payload)
        a.feed(P.CMD_BT, payload)

    def run(seconds):
        end = clock["now"] + seconds
        while clock["now"] < end:
            clock["now"] += 0.5
            a.tick()
    return feed, run, rec, said


def test_follows_connect_disconnect_and_hfp(tmp_path):
    feed, run, rec, said = rig(tmp_path)
    feed(bytes([P.BT_GET, P.GET_HFP, P.HFP_VALUES["off"]]))
    feed(conn_frame(P.CONN_CONNECTED)[4:])
    run(3)
    assert rec.calls == [("to", "sinks")]
    assert "Sound: Creative BT-W5" in said
    feed(conn_frame(P.CONN_CONNECTING)[4:])             # replies to status queries: no effect
    feed(conn_frame(P.CONN_CONNECTED)[4:])
    feed(bytes([P.BT_GET, P.GET_HFP, P.HFP_VALUES["on"]]))   # mic switched on
    run(3)
    assert rec.calls[-1] == ("to", "sources")
    feed(conn_frame(P.CONN_DISCONNECTED)[4:])
    run(3)
    assert set(rec.calls[-2:]) == {("from", "sinks"), ("from", "sources")}


def test_follow_off_does_nothing(tmp_path):
    feed, run, rec, _ = rig(tmp_path, follow=False)
    feed(conn_frame(P.CONN_CONNECTED)[4:])
    feed(conn_frame(P.CONN_DISCONNECTED)[4:])
    run(5)
    assert rec.calls == []


def test_headset_already_off_at_start_moves_audio_away(tmp_path):
    feed, run, rec, _ = rig(tmp_path)
    feed(conn_frame(P.CONN_NOT_CONNECTED, HEADSET)[4:])
    run(3)
    assert ("from", "sinks") in rec.calls
