"""The background service (systemd user unit running 'btwctl monitor --notify')."""

import shutil
import subprocess

UNIT = "btwctl-monitor.service"
OLD_UNIT = "btw5-switch-monitor.service"   # before the rename (0.6); still shipped
ENABLE_CMD = f"systemctl --user enable --now {UNIT}"


def _systemctl(*args):
    if not shutil.which("systemctl"):
        return None
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None


def is_active():
    """True/False, or None if it can't be told (no systemd)."""
    r = _systemctl("is-active", UNIT, OLD_UNIT)
    return None if r is None else "active" in r.stdout.split()


def enable():
    """Enable and start it. Returns (ok, message)."""
    old = _systemctl("is-enabled", OLD_UNIT)
    if old is not None and old.stdout.strip() == "enabled":
        _systemctl("disable", "--now", OLD_UNIT)   # migrate from the pre-rename unit
    r = _systemctl("enable", "--now", UNIT)
    if r is None:
        return False, "systemd isn't available; run 'btwctl monitor --notify' at login instead."
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip() or f"systemctl failed ({r.returncode})"
    return True, "Background service started; it also starts at login."
