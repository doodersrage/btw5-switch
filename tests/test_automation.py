import pytest

from btwctl import protocol as P
from btwctl.automation import Automation, running_games
from btwctl.config import DEFAULT_GAME_APPS, Config
from tests.fakes import HEADSET, conn_frame

X3 = bytes.fromhex("68001abc5ceb")


@pytest.fixture
def cfg(tmp_path):
    return Config(str(tmp_path / "config.ini"))


def fake_proc(tmp_path, *cmdlines):
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    for old in proc.iterdir():
        for f in old.iterdir():
            f.unlink()
        old.rmdir()
    for i, cmd in enumerate(cmdlines, start=100):
        (proc / str(i)).mkdir()
        (proc / str(i) / "cmdline").write_bytes(cmd.replace(" ", "\0").encode())
    return str(proc)


class Rig:
    """An Automation wired to a fake dongle state, clock and process list."""

    def __init__(self, cfg, tmp_path, aptx=0x40, hfp=0x02, auto=0x00):
        self.tracker = P.ChangeTracker()
        self.sent = []
        self.said = []
        self.now = 0.0
        self.tmp = tmp_path
        self.proc = fake_proc(tmp_path)
        self.values = {P.GET_APTX: aptx, P.GET_HFP: hfp, P.GET_AUTO_HFP: auto}
        self.a = Automation(cfg, self.tracker, send=self.send, say=self.said.append,
                            proc=self.proc, clock=lambda: self.now, audio=False)

    def send(self, cmd, payload):
        """Behave like the dongle: settings change, reads answer (as reports)."""
        self.sent.append(bytes(payload))
        if payload[0] == P.BT_SET:
            key = {P.SET_APTX: P.GET_APTX, P.SET_HFP: P.GET_HFP, P.SET_AUTO_HFP: P.GET_AUTO_HFP}[payload[1]]
            self.values[key] = payload[2]
            if key == P.GET_APTX:
                self.report([P.BT_GET, P.GET_APTX_ACTIVE, payload[2]])
        elif payload[0] == P.BT_GET:
            self.report([P.BT_GET, payload[1], self.values[payload[1]]])

    def report(self, payload):
        p = bytes(payload)
        self.tracker.feed(P.CMD_BT, p)
        self.a.feed(P.CMD_BT, p)

    def connect(self, addr=HEADSET, name=b"MOMENTUM 4"):
        self.report(conn_frame(P.CONN_CONNECTED, addr, name)[4:])

    def run(self, seconds):
        end = self.now + seconds
        while self.now < end:
            self.now += 0.5
            self.a.tick()

    def games(self, *cmdlines):
        fake_proc(self.tmp, *cmdlines)

    def sets(self):
        return [p for p in self.sent if p[0] == P.BT_SET]


def test_running_games(tmp_path):
    proc = fake_proc(tmp_path, "/usr/bin/steam", "reaper SteamLaunch AppId=570 -- proton run game.exe",
                     "kate notes.txt")
    assert running_games(DEFAULT_GAME_APPS, proc) == {"SteamLaunch"}
    assert running_games(["STEAMLAUNCH"], proc) == {"STEAMLAUNCH"}   # case-insensitive
    assert running_games([], proc) == set()


def test_config_round_trip(cfg):
    cfg.set_preferences(HEADSET, name="MOMENTUM 4", aptx="hq", hfp="off")
    cfg.game_mode = True
    cfg.game_apps = ["SteamLaunch", "minecraft"]
    cfg.save()
    again = Config(cfg.path)
    assert again.preferences(HEADSET) == {"aptx": "hq", "hfp": "off"}
    assert again.preferences("BA:52:E9:09:80:C3") == {"aptx": "hq", "hfp": "off"}
    assert again.game_mode and again.game_apps == ["SteamLaunch", "minecraft"]
    assert again.all_preferences() == [("ba:52:e9:09:80:c3", "MOMENTUM 4", {"aptx": "hq", "hfp": "off"})]
    with pytest.raises(ValueError):
        again.set_preferences(HEADSET, aptx="loud")


def test_preferences_applied_once_on_connect(cfg, tmp_path):
    cfg.set_preferences(HEADSET, name="MOMENTUM 4", aptx="ll", hfp="on")
    r = Rig(cfg, tmp_path, aptx=0x40, hfp=0x02)
    r.connect()
    r.run(1)
    assert r.sets() == []                         # waits for the headset to settle
    r.run(5)
    assert bytes([P.BT_SET, P.SET_APTX, 0x20]) in r.sets()
    assert r.sets()[-1] == bytes([P.BT_SET, P.SET_HFP, 0x01])   # HFP last (dongle restarts)
    assert "applying aptX Low Latency, microphone on" in r.said[-1]
    # after the HFP restart the headset reconnects: nothing left to change
    n = len(r.sets())
    r.report(conn_frame(P.CONN_NOT_CONNECTED)[4:])
    r.connect()
    r.run(10)
    assert len(r.sets()) == n


def test_no_preferences_no_commands(cfg, tmp_path):
    r = Rig(cfg, tmp_path)
    r.connect(X3, b"X3")
    r.run(10)
    assert r.sets() == []


def test_game_mode_switches_and_restores(cfg, tmp_path):
    cfg.game_mode = True
    r = Rig(cfg, tmp_path, aptx=0x40)
    r.connect()
    r.run(4)
    r.games("reaper SteamLaunch AppId=1")
    r.run(4)
    assert r.values[P.GET_APTX] == 0x20 and "switching to Low Latency" in r.said[-1]
    r.games()
    r.run(4)
    assert r.values[P.GET_APTX] == 0x40 and "back to High Quality" in r.said[-1]


def test_game_mode_leaves_manual_change_alone(cfg, tmp_path):
    cfg.game_mode = True
    r = Rig(cfg, tmp_path, aptx=0x40)
    r.connect()
    r.run(4)
    r.games("gamescope -- game")
    r.run(4)
    r.send(0, [P.BT_SET, P.SET_APTX, 0x40])   # the user picks HQ during the game
    r.games()
    r.run(4)
    assert r.values[P.GET_APTX] == 0x40
    assert not any("back to" in s for s in r.said)


def test_game_mode_off_does_nothing(cfg, tmp_path):
    r = Rig(cfg, tmp_path, aptx=0x40)
    r.connect()
    r.games("reaper SteamLaunch AppId=1")
    r.run(8)
    assert r.sets() == []


def test_game_beats_headset_preference_then_restores_it(cfg, tmp_path):
    cfg.game_mode = True
    cfg.set_preferences(HEADSET, aptx="hq")
    r = Rig(cfg, tmp_path, aptx=0x20)
    r.games("reaper SteamLaunch AppId=1")
    r.run(4)                       # game already running, headset not yet connected
    r.connect()
    r.run(6)
    assert r.values[P.GET_APTX] == 0x20          # stays Low Latency during the game
    r.games()
    r.run(4)
    assert r.values[P.GET_APTX] == 0x40          # then the headset's preference


# --- regressions found in review ------------------------------------------------------

def test_hfp_change_from_the_tray_is_kept_not_undone(cfg, tmp_path):
    """Switching HFP restarts the dongle; the stored preference used to switch it straight back."""
    cfg.set_preferences(HEADSET, name="MOMENTUM 4", hfp="off")
    r = Rig(cfg, tmp_path, hfp=0x02)
    r.connect()
    r.run(6)
    assert r.sets() == []                                 # already as preferred
    r.values[P.GET_HFP] = 0x01                            # user: 'btwctl hfp on' -> dongle restarts
    r.a.dongle_gone()
    r.report(conn_frame(P.CONN_NOT_CONNECTED)[4:])
    r.connect()
    r.report([P.BT_GET, P.GET_HFP, 0x01])
    r.run(8)
    assert r.sets() == []                                 # not switched back off
    assert Config(cfg.path).preferences(HEADSET)["hfp"] == "on"   # and remembered


def test_aptx_change_from_the_command_line_is_remembered(cfg, tmp_path):
    cfg.set_preferences(HEADSET, aptx="hq")
    r = Rig(cfg, tmp_path, aptx=0x40)
    r.connect()
    r.run(6)
    r.now += 5                                            # well after our own commands
    r.report([P.BT_GET, P.GET_APTX_ACTIVE, 0x20])         # user: 'btwctl ll'
    assert Config(cfg.path).preferences(HEADSET)["aptx"] == "ll"


def test_reading_a_setting_doesnt_overwrite_new_preferences(cfg, tmp_path):
    """Preferences saved for the connected headset survived only until the next status read:
    the read's reply differed from them and was taken for a change by the user."""
    r = Rig(cfg, tmp_path, hfp=0x01, auto=0x00)
    r.connect(X3, b"X3")
    r.report([P.BT_GET, P.GET_HFP, 0x01])
    r.report([P.BT_GET, P.GET_AUTO_HFP, 0x00])
    r.run(6)
    cfg.set_preferences(X3, name="X3", hfp="off", auto_hfp="on")
    cfg.save()
    r.now += 5
    r.report([P.BT_GET, P.GET_HFP, 0x01])               # e.g. 'btwctl status' or the window
    r.report([P.BT_GET, P.GET_AUTO_HFP, 0x00])
    r.run(2)
    assert Config(cfg.path).preferences(X3) == {"hfp": "off", "auto-hfp": "on"}
    assert not any("Remembering" in s for s in r.said)


def test_game_mode_survives_a_reconnect_during_the_game(cfg, tmp_path):
    cfg.game_mode = True
    r = Rig(cfg, tmp_path, aptx=0x40)
    r.connect()
    r.run(4)
    r.games("reaper SteamLaunch AppId=1")
    r.run(4)
    assert r.values[P.GET_APTX] == 0x20
    r.report(conn_frame(P.CONN_DISCONNECTED)[4:])         # headset drops out mid-game ...
    r.values[P.GET_APTX] = 0x40                           # ... and comes back in High Quality
    r.connect()
    r.run(8)
    assert r.values[P.GET_APTX] == 0x20                   # Low Latency again
    r.games()
    r.run(8)
    assert r.values[P.GET_APTX] == 0x40                   # and High Quality after the game


def test_no_endless_polling_with_a_headset_without_aptx(cfg, tmp_path):
    cfg.game_mode = True
    r = Rig(cfg, tmp_path)
    r.send = lambda cmd, payload: r.sent.append(bytes(payload))   # aptX never answers
    r.a.send = r.send
    r.connect()
    r.games("reaper SteamLaunch AppId=1")
    r.run(30)
    queries = [p for p in r.sent if p == bytes([P.BT_GET, P.GET_APTX])]
    assert len(queries) <= 30 / 5 + 2, len(queries)


def test_game_running_helper(cfg, tmp_path):
    from btwctl.automation import game_running
    proc = fake_proc(tmp_path, "reaper SteamLaunch AppId=1")
    assert not game_running(cfg, proc)                    # game mode off
    cfg.game_mode = True
    assert game_running(cfg, proc)
