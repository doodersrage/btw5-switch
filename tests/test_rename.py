"""btwctl was called btw5-switch before 0.7: upgrades must keep working."""

import os
import subprocess

from btwctl import broker, config, service
from tests.fakes import FakeDongle


def test_config_migrates_from_old_name(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old = tmp_path / "btw5-switch" / "config.ini"
    old.parent.mkdir()
    old.write_text("[game-mode]\nenabled = yes\n")
    path = config.config_path()
    assert path == str(tmp_path / "btwctl" / "config.ini")
    assert config.Config().game_mode
    assert old.exists()                       # copied, not moved: downgrading still works
    old.write_text("[game-mode]\nenabled = no\n")
    assert config.Config().game_mode          # migrated once; the new file wins afterwards


def test_finds_a_still_running_old_service(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    with broker.Broker(FakeDongle(), path=str(run / "btw5-switch.sock")):
        assert isinstance(broker.default_transport(), broker.SocketTransport)
        assert broker.default_transport().path.endswith("btw5-switch.sock")


def fake_systemctl(monkeypatch, states):
    calls = []

    def run(*args):
        calls.append(args)
        if args[0] == "is-active":
            out = "\n".join(states.get(u, "inactive") for u in args[1:])
        elif args[0] == "is-enabled":
            out = "enabled" if states.get(args[1]) == "active" else "disabled"
        else:
            out = ""
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(service, "_systemctl", run)
    return calls


def test_old_service_counts_as_running(monkeypatch):
    fake_systemctl(monkeypatch, {"btw5-switch-monitor.service": "active"})
    assert service.is_active() is True
    fake_systemctl(monkeypatch, {})
    assert service.is_active() is False


def test_enabling_switches_from_old_to_new_service(monkeypatch):
    calls = fake_systemctl(monkeypatch, {"btw5-switch-monitor.service": "active"})
    ok, _ = service.enable()
    assert ok
    assert ("disable", "--now", "btw5-switch-monitor.service") in calls
    assert ("enable", "--now", "btwctl-monitor.service") in calls
    assert calls.index(("disable", "--now", "btw5-switch-monitor.service")) < \
        calls.index(("enable", "--now", "btwctl-monitor.service"))


def test_old_command_name_is_kept():
    here = os.path.dirname(__file__)
    pyproject = open(os.path.join(here, "..", "pyproject.toml")).read()
    assert 'btw5-switch = "btwctl.cli:main"' in pyproject
    for f in ("completions/btwctl.bash", "completions/_btwctl"):
        assert "btw5-switch" in open(os.path.join(here, "..", f)).read()
