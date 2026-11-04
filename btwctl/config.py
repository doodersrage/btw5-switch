"""User settings: per-headset preferences and game mode.

Stored as INI in $XDG_CONFIG_HOME/btwctl/config.ini, e.g.

    [game-mode]
    enabled = yes
    apps = SteamLaunch
           gamescope

    [headset ba:52:e9:09:80:c3]
    name = MOMENTUM 4
    aptx = hq
    hfp = off
    auto-hfp = off

The background service ('btwctl monitor') applies them; see automation.py.
"""

import configparser
import os
import shutil

from . import protocol as P

# Command-line fragments that mean "a game is running" (matched case-insensitively
# anywhere in a process's command line).
DEFAULT_GAME_APPS = [
    "SteamLaunch",       # every Steam game, native or Proton, runs under Steam's launcher
    "gamescope",         # Valve's game compositor
    "lutris-wrapper",    # games started from Lutris
    "heroic/tools",      # games started from the Heroic launcher (Wine/Proton)
]

PREF_KEYS = ("aptx", "hfp", "auto-hfp")
PREF_VALUES = {"aptx": ("hq", "ll"), "hfp": ("on", "off"), "auto-hfp": ("on", "off")}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(base, "btwctl", "config.ini")
    old = os.path.join(base, "btw5-switch", "config.ini")   # before the rename (0.6)
    if not os.path.exists(path) and os.path.exists(old):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copy2(old, path)
        except OSError:
            return old
    return path


class Config:
    def __init__(self, path=None):
        self.path = path or config_path()
        self.cp = configparser.ConfigParser(interpolation=None)
        self.mtime = None
        self.load()

    # --- file -------------------------------------------------------------

    def load(self):
        self.cp = configparser.ConfigParser(interpolation=None)
        try:
            self.cp.read(self.path, encoding="utf-8")
            self.mtime = os.path.getmtime(self.path)
        except (OSError, configparser.Error):
            self.mtime = None

    def reload_if_changed(self):
        """Pick up edits made by another program (the window, the CLI, an editor)."""
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = None
        if mtime != self.mtime:
            self.load()
            return True
        return False

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            self.cp.write(f)
        os.replace(tmp, self.path)
        self.mtime = os.path.getmtime(self.path)

    # --- game mode --------------------------------------------------------

    @property
    def game_mode(self):
        return self.cp.getboolean("game-mode", "enabled", fallback=False)

    @game_mode.setter
    def game_mode(self, on):
        self._section("game-mode")["enabled"] = "yes" if on else "no"

    @property
    def game_apps(self):
        if not self.cp.has_option("game-mode", "apps"):
            return list(DEFAULT_GAME_APPS)
        raw = self.cp.get("game-mode", "apps")
        return [a.strip() for a in raw.replace(",", "\n").splitlines() if a.strip()]

    @game_apps.setter
    def game_apps(self, apps):
        self._section("game-mode")["apps"] = "\n".join(a.strip() for a in apps if a.strip())

    # --- per-headset preferences -----------------------------------------------

    @staticmethod
    def _headset_section(addr):
        return f"headset {P.fmt_addr(addr) if isinstance(addr, (bytes, bytearray)) else addr.lower()}"

    def preferences(self, addr):
        """{'aptx': 'hq', 'hfp': 'off', ...} for a headset (only the keys that are set)."""
        sec = self._headset_section(addr)
        if not self.cp.has_section(sec):
            return {}
        return {k: self.cp.get(sec, k) for k in PREF_KEYS
                if self.cp.get(sec, k, fallback="") in PREF_VALUES[k]}

    def set_preferences(self, addr, name=None, **prefs):
        """Set some preferences (keys aptx, hfp, auto_hfp); None leaves a key unchanged."""
        sec = self._section(self._headset_section(addr))
        if name:
            sec["name"] = name
        for k, v in prefs.items():
            key = k.replace("_", "-")
            if key not in PREF_KEYS:
                raise ValueError(f"unknown preference {k}")
            if v is not None:
                if v not in PREF_VALUES[key]:
                    raise ValueError(f"{key} must be one of {', '.join(PREF_VALUES[key])}")
                sec[key] = v

    def clear_preferences(self, addr):
        self.cp.remove_section(self._headset_section(addr))

    def all_preferences(self):
        """[(address, name, prefs)] for every headset with preferences."""
        out = []
        for sec in self.cp.sections():
            if sec.startswith("headset "):
                addr = sec[len("headset "):]
                out.append((addr, self.cp.get(sec, "name", fallback=""), self.preferences(addr)))
        return out

    def _section(self, name):
        if not self.cp.has_section(name):
            self.cp.add_section(name)
        return self.cp[name]
