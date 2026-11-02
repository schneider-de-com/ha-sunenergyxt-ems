"""Home Assistant integration setup and teardown behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import (  # noqa: E402
    _async_options_updated,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.sunenergyxt_ems import runtime as runtime_module  # noqa: E402


@pytest.fixture
def lifecycle(monkeypatch):
    controller = SimpleNamespace(
        async_start=AsyncMock(), async_stop=AsyncMock(),
        async_change_options=AsyncMock(),
    )
    monkeypatch.setattr(runtime_module, "EMSRuntime", lambda hass, entry: controller)
    config_entries = SimpleNamespace(
        async_forward_entry_setups=AsyncMock(),
        async_unload_platforms=AsyncMock(return_value=True),
    )
    hass = SimpleNamespace(config_entries=config_entries, data={})
    entry = SimpleNamespace(
        entry_id="ems-entry",
        async_on_unload=Mock(),
        add_update_listener=Mock(return_value=lambda: None),
    )
    return hass, entry, controller


@pytest.mark.asyncio
async def test_positive_setup_starts_one_runtime(lifecycle):
    hass, entry, controller = lifecycle
    assert await async_setup_entry(hass, entry)
    controller.async_start.assert_awaited_once()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()
    assert hass.data["sunenergyxt_ems"]["ems-entry"] is controller
    entry.async_on_unload.assert_called_once()


@pytest.mark.asyncio
async def test_positive_options_change_updates_active_runtime(lifecycle):
    hass, entry, controller = lifecycle
    await async_setup_entry(hass, entry)
    await _async_options_updated(hass, entry)
    controller.async_change_options.assert_awaited_once()


@pytest.mark.asyncio
async def test_negative_missing_runtime_options_update_does_nothing(lifecycle):
    hass, entry, controller = lifecycle
    await _async_options_updated(hass, entry)
    controller.async_change_options.assert_not_awaited()


@pytest.mark.asyncio
async def test_positive_unload_stops_task_and_forwards_platform_unload(lifecycle):
    hass, entry, controller = lifecycle
    await async_setup_entry(hass, entry)
    assert await async_unload_entry(hass, entry)
    controller.async_stop.assert_awaited_once()
    assert hass.data["sunenergyxt_ems"] == {}


@pytest.mark.asyncio
async def test_negative_failed_unload_keeps_runtime_reference(lifecycle):
    hass, entry, controller = lifecycle
    await async_setup_entry(hass, entry)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
    assert not await async_unload_entry(hass, entry)
    assert "ems-entry" in hass.data["sunenergyxt_ems"]
