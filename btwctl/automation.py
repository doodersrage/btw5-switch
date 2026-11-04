"""Automatic actions run by the background service ('btwctl monitor').

- Per-headset preferences: when a headset connects, apply its preferred aptX
  mode, HFP and auto-HFP settings (only what differs: changing HFP makes the
  dongle restart, so never send it needlessly).
- Game mode: while a game is running, use aptX Adaptive Low Latency; when the
  last game exits, go back to the mode from before.

It works on the report stream the monitor already watches: ChangeTracker knows
the current state, and commands are sent fire-and-forget (their effects come
back as reports).
"""

import os
import time

from . import protocol as P

GAME_CHECK_SECONDS = 3.0
APPLY_DELAY = 2.5        # settle time after a headset connects before changing settings
APPLY_RETRIES = 4


def running_games(patterns, proc="/proc"):
    """Patterns from `patterns` found in any running process's command line."""
    wanted = [(p, p.lower()) for p in patterns if p]
    found = set()
    me = os.getpid()
    try:
        pids = [d for d in os.listdir(proc) if d.isdigit() and int(d) != me]
    except OSError:
        return found
    for pid in pids:
        try:
            with open(os.path.join(proc, pid, "cmdline"), "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").lower()
        except OSError:
            continue  # gone, or not ours to read
        for orig, low in wanted:
            if low in cmd:
                found.add(orig)
    return found


def describe_prefs(prefs):
    words = {"aptx": {"hq": "aptX High Quality", "ll": "aptX Low Latency"},
             "hfp": {"on": "microphone on", "off": "microphone off"},
             "auto-hfp": {"on": "auto mic switching on", "off": "auto mic switching off"}}
    return ", ".join(words[k][v] for k, v in prefs.items())


class Automation:
    def __init__(self, config, tracker, send, say=print, notify=None,
                 proc="/proc", clock=time.monotonic):
        self.config = config
        self.tracker = tracker
        self.send = send            # send(cmd, payload)
        self.say = say
        self.notify = notify        # notify(summary, body) or None
        self.proc = proc
        self.clock = clock

        self.connected = None       # (addr, name) of the connected headset
        self.pending = None         # [addr, name, due, tries] preferences to apply
        self.next_game_check = 0.0
        self.game = None            # set of running game patterns while game mode is active
        self.saved_mode = None      # aptX mode to restore when the games exit

    # --- state helpers -----------------------------------------------------

    def aptx(self):
        """Current aptX mode value (0x20/0x40), or None if unknown/not available."""
        s = self.tracker.settings
        v = s.get(P.GET_APTX_ACTIVE, s.get(P.GET_APTX))
        return v if v in P.APTX_NAMES else None

    def set(self, key, value):
        self.send(P.CMD_BT, [P.BT_SET, key, value])

    def query(self, key):
        self.send(P.CMD_BT, [P.BT_GET, key])

    def tell(self, line, notify=True):
        self.say(line)
        if notify and self.notify:
            self.notify("BT-W Control", line)

    # --- input -------------------------------------------------------------

    def feed(self, cmd, p):
        """Watch the report stream for headsets connecting and disconnecting."""
        conn = P.parse_conn(p) if cmd == P.CMD_BT else None
        if not conn:
            return
        state, addr, name = conn
        if state == P.CONN_CONNECTED:
            if not self.connected or self.connected[0] != addr:
                self.connected = (addr, name)
                self.pending = [addr, name, self.clock() + APPLY_DELAY, 0]
                # the settings we knew belong to the previous link; read them fresh
                for key in (P.GET_APTX, P.GET_HFP, P.GET_AUTO_HFP):
                    self.tracker.settings.pop(key, None)
                self.tracker.settings.pop(P.GET_APTX_ACTIVE, None)
        elif state in (P.CONN_DISCONNECTED, P.CONN_NOT_CONNECTED):
            if self.connected and self.connected[0] == addr:
                self.connected = None
                self.pending = None

    def tick(self):
        """Call regularly (every second or more often)."""
        self.config.reload_if_changed()
        now = self.clock()
        if self.pending and now >= self.pending[2]:
            self.apply_preferences(now)
        if now >= self.next_game_check:
            self.next_game_check = now + GAME_CHECK_SECONDS
            self.check_games()

    # --- per-headset preferences ----------------------------------------------------

    def apply_preferences(self, now):
        addr, name, _, tries = self.pending
        prefs = self.config.preferences(addr)
        if not prefs:
            self.pending = None
            return
        s = self.tracker.settings
        wanted = dict(prefs)
        if self.game and "aptx" in wanted:
            # a game is running: Low Latency wins; restore the preference afterwards
            self.saved_mode = P.APTX[wanted.pop("aptx")]
        current = {"aptx": self.aptx(), "hfp": s.get(P.GET_HFP), "auto-hfp": s.get(P.GET_AUTO_HFP)}
        unknown = [k for k in wanted if current[k] is None]
        if unknown and tries < APPLY_RETRIES:
            # not known yet (e.g. aptX isn't readable right after connecting): ask, retry soon
            for k in unknown:
                self.query({"aptx": P.GET_APTX, "hfp": P.GET_HFP, "auto-hfp": P.GET_AUTO_HFP}[k])
            self.pending = [addr, name, now + 1.5, tries + 1]
            return
        self.pending = None
        target = {"aptx": lambda v: P.APTX[v], "hfp": lambda v: P.HFP_VALUES[v],
                  "auto-hfp": lambda v: P.AUTO_HFP_VALUES[v]}
        changes = {k: v for k, v in wanted.items()
                   if current[k] is not None and current[k] != target[k](v)}
        if not changes:
            return
        self.tell(f"{name or P.fmt_addr(addr)} connected: applying {describe_prefs(changes)}")
        if "aptx" in changes:
            self.set(P.SET_APTX, P.APTX[changes["aptx"]])
        if "auto-hfp" in changes:
            self.set(P.SET_AUTO_HFP, P.AUTO_HFP_VALUES[changes["auto-hfp"]])
        if "hfp" in changes:   # last: the dongle restarts after this one
            self.set(P.SET_HFP, P.HFP_VALUES[changes["hfp"]])
        for key in (P.GET_APTX, P.GET_AUTO_HFP):
            self.query(key)

    # --- game mode --------------------------------------------------------------------

    def check_games(self):
        games = running_games(self.config.game_apps, self.proc) if self.config.game_mode else set()
        if games and self.game is None:
            mode = self.aptx()
            if mode is None:
                if self.connected:
                    self.query(P.GET_APTX)   # not known yet: ask, and look again shortly
                    self.next_game_check = self.clock() + 0.5
                return
            self.game = games
            if mode != P.APTX["ll"]:
                self.saved_mode = mode
                self.set(P.SET_APTX, P.APTX["ll"])
                self.query(P.GET_APTX)
                self.tell(f"Game running ({', '.join(sorted(games))}): switching to Low Latency")
            else:
                self.saved_mode = None
        elif games:
            self.game = games
        elif self.game is not None:
            self.game = None
            restore, self.saved_mode = self.saved_mode, None
            # only if it's still Low Latency: if the user changed it meanwhile, leave it
            if restore and self.aptx() == P.APTX["ll"]:
                self.set(P.SET_APTX, restore)
                self.query(P.GET_APTX)
                self.tell(f"Game closed: back to {P.APTX_NAMES[restore]}")
