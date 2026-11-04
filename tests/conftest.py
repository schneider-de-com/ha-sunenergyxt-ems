"""Test adapters for runtime tests using lightweight Home Assistant doubles."""

import importlib.util

import pytest


@pytest.fixture(autouse=True)
def isolated_mock_storage(monkeypatch):
    """Use memory storage only for fake hass objects; real HA keeps its own Store."""
    if importlib.util.find_spec("homeassistant") is None:
        return
    from homeassistant.core import HomeAssistant

    from custom_components.sunenergyxt_ems import runtime

    real_store = runtime.Store

    class MemoryStore:
        def __init__(self):
            self.data = None

        async def async_load(self):
            return self.data

        async def async_save(self, data):
            self.data = data

    def make_store(hass, *args, **kwargs):
        if isinstance(hass, HomeAssistant):
            return real_store(hass, *args, **kwargs)
        return MemoryStore()

    monkeypatch.setattr(runtime, "Store", make_store)
