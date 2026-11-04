import pytest

BT_W5 = {"model": "Creative BT-W5", "usb_id": "041e:3130", "tested": True}


@pytest.fixture(autouse=True)
def no_real_usb(monkeypatch, tmp_path):
    """Tests never touch the real USB bus or a running btwctl service (its socket)."""
    run = tmp_path / "xdg-runtime"
    run.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setattr("btwctl.device.dongle_info", lambda: dict(BT_W5))
    monkeypatch.setattr("btwctl.device.other_creative_devices", lambda: [])
