"""Automatic actions run by the background service ('btwctl monitor').

- Per-headset preferences: when a headset connects, apply its preferred aptX
  mode, HFP and auto-HFP settings (only what differs: changing HFP makes the
  dongle restart, so never send it needlessly).
- Game mode: while a game is running, use aptX Adaptive Low Latency; when the
  last game exits, go back to the mode from before.
- Audio follows the headset: make the dongle the default output (and, with
  HFP on, microphone) while a headset is connected, and switch back after.

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
QUIET_SECONDS = 3.0      # after sending a setting, its echoes aren't the user's doing
APTX_QUERY_INTERVAL = 5.0
QUICK_CHECKS = 3
RESTORE_TRIES = 6
AUDIO_DELAY = 1.0        # let PipeWire notice the change first
AUDIO_RETRIES = 8


def game_running(config, proc="/proc"):
    """Game mode is on and a game is running right now (its Low Latency isn't the user's choice)."""
    return bool(config.game_mode and running_games(config.game_apps, proc))


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
                 proc="/proc", clock=time.monotonic, audio=None):
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
        self.restore = None         # [mode, tries]: restore after a game, once aptX is readable
        self.after_restart = False  # the dongle restarted (HFP change, forget): a user action
        self.quiet_until = 0.0      # ignore setting reports caused by our own commands until then
        self.seen = {}              # "aptx"/"hfp"/"auto-hfp" -> last value reported for this link
        self.last_aptx_query = -1e9
        self.aptx_key = P.GET_APTX  # key that reads the aptX mode (key 08 on the BT-W6)
        self.quick_checks = 0

        if audio is None:
            from .audio import AudioFollower, available
            audio = AudioFollower() if available() else None
        self.audio = audio          # None: no PipeWire tools
        self.link = None            # headset connected? (None: not known yet)
        self.hfp = None             # last HFP value seen
        self.audio_jobs = []        # [action, kind, due, tries]

    # --- state helpers -----------------------------------------------------

    def aptx(self):
        """Current aptX mode value (0x20/0x40), or None if unknown/not available."""
        s = self.tracker.settings
        v = s.get(P.GET_APTX_ACTIVE, s.get(P.GET_APTX))
        return v if v in P.APTX_NAMES else None

    def set(self, key, value):
        self.send(P.CMD_BT, [P.BT_SET, key, value])
        # its effect (and stale replies to other programs' queries) shouldn't count as the user's
        self.quiet_until = self.clock() + QUIET_SECONDS

    def query(self, key):
        self.send(P.CMD_BT, [P.BT_GET, key])

    def ask_aptx(self):
        """Ask for the aptX mode, at most every few seconds (it may never be readable)."""
        now = self.clock()
        if now - self.last_aptx_query >= APTX_QUERY_INTERVAL:
            self.last_aptx_query = now
            self.query(self.aptx_key)
            return True
        return False

    def tell(self, line, notify=True):
        self.say(line)
        if notify and self.notify:
            self.notify("BT-W Control", line)

    # --- input -------------------------------------------------------------

    def dongle_gone(self):
        """The dongle disappeared (re-enumerating after HFP/forget, or unplugged)."""
        self.after_restart = True
        self.link = None           # so sound follows it again once it's back
        self.connected = None
        self.pending = None
        self.hfp = None

    def feed(self, cmd, p):
        """Watch the report stream for headsets connecting and settings changing."""
        setting = P.parse_setting(p) if cmd == P.CMD_BT else None
        if setting:
            self.adopt_user_change(*setting)
        if setting and setting[0] == P.GET_HFP:
            on = setting[1] == P.HFP_VALUES["on"]
            if on != self.hfp:
                self.hfp = on
                if self.link:
                    self.queue_audio("to" if on else "from", "sources")
        conn = P.parse_conn(p) if cmd == P.CMD_BT else None
        if not conn:
            return
        if conn[0] in (P.CONN_CONNECTED, P.CONN_DISCONNECTED, P.CONN_NOT_CONNECTED):
            link = conn[0] == P.CONN_CONNECTED
            if link != self.link:
                self.link = link
                if link:
                    self.queue_audio("to", "sinks")
                    if self.hfp:
                        self.queue_audio("to", "sources")
                else:
                    self.queue_audio("from", "sinks")
                    self.queue_audio("from", "sources")
        state, addr, name = conn
        if state == P.CONN_CONNECTED:
            if not self.connected or self.connected[0] != addr:
                self.connected = (addr, name)
                if self.after_restart:
                    # back after a restart the user caused (e.g. switching HFP): keep their
                    # settings instead of re-applying the stored ones. What we saw before the
                    # restart stays, so the new HFP value counts as their change.
                    self.after_restart = False
                else:
                    self.pending = [addr, name, self.clock() + APPLY_DELAY, 0]
                    self.seen = {}
                # the settings we knew belong to the previous link; read them fresh
                for key in (P.GET_APTX, P.GET_APTX_ACTIVE, P.GET_HFP, P.GET_AUTO_HFP):
                    self.tracker.settings.pop(key, None)
                self.quick_checks = 0
                self.next_game_check = min(self.next_game_check, self.clock() + APPLY_DELAY)
        elif state in (P.CONN_DISCONNECTED, P.CONN_NOT_CONNECTED):
            if self.connected and self.connected[0] == addr:
                self.connected = None
                self.pending = None

    def adopt_user_change(self, key, value):
        """A setting changed while a headset with preferences is connected, not by us or by
        game mode: the user changed it (tray, command line, …), so remember that instead of
        undoing it at the next connect.

        Only an actual change counts: a report that merely repeats the value (e.g. the
        window or 'btwctl status' reading it) must not overwrite preferences the user
        just saved for the connected headset."""
        names = {P.GET_APTX: "aptx", P.GET_APTX_ACTIVE: "aptx", P.GET_HFP: "hfp", P.GET_AUTO_HFP: "auto-hfp"}
        pref = names.get(key)
        if pref is None:
            return
        before = self.seen.get(pref)
        self.seen[pref] = value
        if before is None or before == value:
            return
        if not self.connected or self.pending or self.clock() < self.quiet_until:
            return
        if pref == "aptx" and (self.game or self.restore):
            return
        words = {"aptx": {v: k for k, v in P.APTX.items()},
                 "hfp": {v: k for k, v in P.HFP_VALUES.items()},
                 "auto-hfp": {v: k for k, v in P.AUTO_HFP_VALUES.items()}}[pref]
        if value not in words:
            return
        addr, name = self.connected
        prefs = self.config.preferences(addr)
        if pref not in prefs or prefs[pref] == words[value]:
            return
        self.config.reload_if_changed()
        self.config.set_preferences(addr, **{pref.replace("-", "_"): words[value]})
        self.config.save()
        self.say(f"Remembering {describe_prefs({pref: words[value]})} for {name or P.fmt_addr(addr)}")

    def tick(self):
        """Call regularly (every second or more often)."""
        self.config.reload_if_changed()
        now = self.clock()
        if self.pending and now >= self.pending[2]:
            self.apply_preferences(now)
        if now >= self.next_game_check:
            self.next_game_check = now + GAME_CHECK_SECONDS
            self.check_games()
        if self.audio_jobs:
            self.run_audio_jobs(now)

    # --- audio follows the headset ----------------------------------------------------

    def queue_audio(self, action, kind):
        # a newer job for the same kind replaces an older one
        self.audio_jobs = [j for j in self.audio_jobs if j[1] != kind]
        self.audio_jobs.append([action, kind, self.clock() + AUDIO_DELAY, 0])

    def run_audio_jobs(self, now):
        if not (self.audio and self.config.audio_follow):
            self.audio_jobs = []
            return
        keep = []
        for job in self.audio_jobs:
            action, kind, due, tries = job
            if now < due:
                keep.append(job)
                continue
            fn = self.audio.to_dongle if action == "to" else self.audio.from_dongle
            result = fn(kind)
            if result is None and tries < AUDIO_RETRIES:
                # e.g. the dongle's sound card isn't back yet after a restart
                keep.append([action, kind, now + 1.5, tries + 1])
            elif result:
                what = "Sound" if kind == "sinks" else "Microphone"
                self.tell(f"{what}: {result}")
        self.audio_jobs = keep

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
            # a game is running: Low Latency wins (check_games sees to it);
            # the preference is what comes back afterwards
            self.saved_mode = P.APTX[wanted.pop("aptx")]
        current = {"aptx": self.aptx(), "hfp": s.get(P.GET_HFP), "auto-hfp": s.get(P.GET_AUTO_HFP)}
        unknown = [k for k in wanted if current[k] is None]
        if unknown and tries < APPLY_RETRIES:
            # not known yet (e.g. aptX isn't readable right after connecting): ask, retry soon
            for k in unknown:
                self.query({"aptx": self.aptx_key, "hfp": P.GET_HFP, "auto-hfp": P.GET_AUTO_HFP}[k])
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
        for key in (self.aptx_key, P.GET_AUTO_HFP):
            self.query(key)

    # --- game mode --------------------------------------------------------------------

    def recheck_soon(self):
        """aptX isn't known yet: look again shortly (a few times), then at the normal pace."""
        if self.quick_checks < QUICK_CHECKS:
            self.quick_checks += 1
            self.next_game_check = self.clock() + 0.5

    def check_games(self):
        games = running_games(self.config.game_apps, self.proc) if self.config.game_mode else set()
        mode = self.aptx()
        if mode is not None:
            self.quick_checks = 0

        if games:
            self.restore = None
            if self.game is None:
                if mode is None:
                    if self.connected:
                        self.ask_aptx()
                        self.recheck_soon()
                    return
                self.game = games
                self.saved_mode = mode if mode != P.APTX["ll"] else None
            else:
                self.game = games
            if mode is None:
                if self.connected:         # e.g. the headset reconnected during the game
                    self.ask_aptx()
                    self.recheck_soon()
            elif mode != P.APTX["ll"]:
                self.set(P.SET_APTX, P.APTX["ll"])
                self.query(self.aptx_key)
                self.tell(f"Game running ({', '.join(sorted(games))}): switching to Low Latency")
            return

        if self.game is not None:
            self.game = None
            if self.saved_mode:
                self.restore = [self.saved_mode, 0]
            self.saved_mode = None
        if self.restore:
            restore, tries = self.restore
            if mode is None:
                # not readable right now (e.g. reconnecting): try again a few times
                if self.connected and tries < RESTORE_TRIES:
                    self.ask_aptx()
                    self.restore = [restore, tries + 1]
                    self.recheck_soon()
                else:
                    self.restore = None
                return
            self.restore = None
            # only if it's still Low Latency: if the user changed it meanwhile, leave it
            if mode == P.APTX["ll"]:
                self.set(P.SET_APTX, restore)
                self.query(self.aptx_key)
                self.tell(f"Game closed: back to {P.APTX_NAMES[restore]}")
