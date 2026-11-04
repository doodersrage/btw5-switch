import pytest

BT_W5 = {"model": "Creative BT-W5", "usb_id": "041e:3130", "tested": True}


@pytest.fixture(autouse=True)
def no_real_usb(monkeypatch):
    """Tests never look at the real USB bus (the fake dongle stands in for it)."""
    monkeypatch.setattr("btw5.device.dongle_info", lambda: dict(BT_W5))
    monkeypatch.setattr("btw5.device.other_creative_devices", lambda: [])
