"""Exercise forecast → justified priority → existing controller without devices."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import State  # noqa: E402

from custom_components.sunenergyxt_ems import horizon as module  # noqa: E402
from custom_components.sunenergyxt_ems import runtime as runtime_module  # noqa: E402
from custom_components.sunenergyxt_ems.demand_history import DemandHistory  # noqa: E402
from custom_components.sunenergyxt_ems.forecast import EnergyInterval  # noqa: E402
from custom_components.sunenergyxt_ems.horizon import HorizonPlanner  # noqa: E402
from custom_components.sunenergyxt_ems.logic import Controller  # noqa: E402
from custom_components.sunenergyxt_ems.observer import HouseholdObservation  # noqa: E402
from custom_components.sunenergyxt_ems.runtime import EMSRuntime  # noqa: E402

NOW = datetime(2026, 11, 8, 10, tzinfo=UTC)


async def evaluate(planner):
    planner.refresh()
    if planner._task is not None:
        await planner._task


@pytest.fixture
def setup_horizon(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(runtime_module, "datetime", Clock)
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: NOW)
    monkeypatch.setattr(
        module, "get_astral_event_date", lambda *args: NOW.replace(hour=8) + timedelta(days=1)
    )
    monkeypatch.setattr(runtime_module, "async_dispatcher_send", lambda *args: None)
    states = {
        "sensor.grid": State(
            "sensor.grid", "500", {"unit_of_measurement": "W"}, last_updated=NOW, last_reported=NOW
        )
    }
    entries = {}
    batteries, capacities, mappings = [], {}, {}
    for name, capacity, energy, rated in (("fast", 4300, 3300, 2000), ("slow", 2150, 1700, 800)):
        soc = 10 + energy / capacity * 100
        states[f"sensor.{name}_soc"] = State(
            f"sensor.{name}_soc",
            str(soc),
            {"unit_of_measurement": "%"},
            last_updated=NOW,
            last_reported=NOW,
        )
        states[f"number.{name}"] = State(
            f"number.{name}", "0", {"unit_of_measurement": "W", "min": 0}, last_updated=NOW
        )
        batteries.append(
            {
                "device_id": name,
                "soc": f"sensor.{name}_soc",
                "lower": 10,
                "upper": 100,
                "inverters": [{"entity_id": f"number.{name}", "rated_w": rated}],
            }
        )
        capacities[name] = capacity
        mapping = {}
        for offset, role in ((0, "today"), (1, "tomorrow")):
            entity = f"sensor.{name}_{role}"
            start = NOW.replace(hour=0) + timedelta(days=offset)
            rows = []
            for index in range(48):
                at = start + timedelta(minutes=30 * index)
                power = 1.5 if name == "fast" and offset == 0 and 12 <= at.hour < 16 else 0
                rows.append(
                    {
                        "period_start": at,
                        "pv_estimate": power,
                        "pv_estimate10": power,
                        "pv_estimate90": power,
                    }
                )
            states[entity] = State(
                entity,
                "6",
                {"unit_of_measurement": "kWh", "dataCorrect": True, "detailedForecast": rows},
                last_updated=NOW,
            )
            entries[entity] = SimpleNamespace(
                platform="solcast_solar", config_entry_id=f"solcast_{name}"
            )
            mapping[role] = entity
        entity = f"sensor.{name}_updated"
        states[entity] = State(entity, NOW.isoformat())
        entries[entity] = SimpleNamespace(
            platform="solcast_solar", config_entry_id=f"solcast_{name}"
        )
        mapping["updated"] = entity
        mappings[name] = mapping
    registry = SimpleNamespace(async_get=entries.get)
    monkeypatch.setattr(module.er, "async_get", lambda hass: registry)
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        config=SimpleNamespace(time_zone="UTC"),
        services=SimpleNamespace(async_call=AsyncMock()),
        async_create_task=lambda coro, **kwargs: asyncio.create_task(coro),
        async_add_executor_job=lambda fn, *args: asyncio.to_thread(fn, *args),
    )
    entry = SimpleNamespace(
        entry_id="planning-test",
        data={"grid": "sensor.grid", "batteries": batteries},
        options={
            "enabled": True,
            "planning_enabled": True,
            "capacity_wh": capacities,
            "pv_forecast_by_battery": mappings,
        },
    )
    runtime = EMSRuntime(hass, entry)
    runtime.household_observation = lambda: HouseholdObservation(NOW, 500, "measured")
    runtime.controller.state.main, runtime.controller.state.support = "slow", "fast"
    rows = [
        EnergyInterval(
            start := NOW - timedelta(days=4) + timedelta(minutes=30 * i),
            start + timedelta(minutes=30),
            250,
        )
        for i in range(4 * 48)
    ]
    history = DemandHistory.from_dict(
        {
            "version": 1,
            "intervals": [
                {"start": s.start.isoformat(), "end": s.end.isoformat(), "energy_wh": s.energy_wh}
                for s in rows
            ],
        },
        now=NOW,
    )
    observer = SimpleNamespace(
        persistence_ok=True, reason="measured", history=history, last_poll_at=NOW
    )
    planner = HorizonPlanner(hass, entry, runtime, observer)
    return planner, runtime, observer, entry


@pytest.mark.asyncio
async def test_real_forecast_and_history_bind_to_existing_controller_priority(setup_horizon):
    planner, runtime, _, _ = setup_horizon
    await evaluate(planner)
    assert planner.reason == "expected_pv_loss_reduction"
    assert runtime.controller.planning_order[0] == "fast"
    outputs = runtime.controller.update(runtime._batteries(), 500, 0)
    assert runtime.controller.state.main == "fast"
    assert outputs["number.fast"] > 0
    assert outputs["number.slow"] == 0
    runtime.hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_missing_measurement_or_failed_persistence_immediately_discards_hint(setup_horizon):
    planner, runtime, observer, _ = setup_horizon
    await evaluate(planner)
    assert runtime.controller.planning_order
    observer.persistence_ok = False
    await evaluate(planner)
    assert runtime.controller.planning_order == ()
    assert planner.reason == "storage_unavailable"


@pytest.mark.asyncio
async def test_feature_stays_optional_and_does_not_control_tariffs(setup_horizon):
    planner, runtime, _, entry = setup_horizon
    entry.options.pop("planning_enabled")
    await evaluate(planner)
    assert planner.reason == "disabled"
    assert runtime.controller.planning_order == ()
    assert isinstance(runtime.controller, Controller)


@pytest.mark.asyncio
async def test_forecast_priority_reaches_existing_runtime_writer(setup_horizon):
    planner, runtime, _, _ = setup_horizon
    runtime.horizon = planner
    await evaluate(planner)
    runtime._running = True
    await runtime.async_update()
    assert runtime.controller.state.main == "fast"
    assert runtime._last_dispatched_w.get("number.fast", 0) > 0
    assert runtime._last_dispatched_w.get("number.slow", 0) == 0


@pytest.mark.asyncio
async def test_invalid_forecast_clears_hint_even_inside_evaluation_cache(setup_horizon):
    planner, runtime, _, _ = setup_horizon
    await evaluate(planner)
    assert runtime.controller.planning_order
    runtime.hass.states.get.__self__["sensor.fast_today"].attributes["detailedForecast"].pop()
    await evaluate(planner)
    assert runtime.controller.planning_order == ()


@pytest.mark.asyncio
async def test_horizon_covers_the_coming_night_to_local_sunrise(setup_horizon):
    planner, _, _, _ = setup_horizon
    await evaluate(planner)
    assert planner.horizon_until == NOW.replace(hour=8) + timedelta(days=1)


@pytest.mark.asyncio
async def test_duplicated_per_storage_pv_cannot_authorize_priority(setup_horizon):
    planner, runtime, _, entry = setup_horizon
    entry.options["pv_forecast_by_battery"]["slow"] = entry.options["pv_forecast_by_battery"][
        "fast"
    ]
    await evaluate(planner)
    assert planner.reason == "pv_forecast_unavailable"
    assert runtime.controller.planning_order == ()


@pytest.mark.asyncio
async def test_storage_cannot_keep_a_hint_when_observer_stops_reporting(setup_horizon):
    planner, runtime, observer, _ = setup_horizon
    await evaluate(planner)
    observer.last_poll_at = NOW - timedelta(seconds=30)
    await evaluate(planner)
    assert planner.reason == "measurements_unavailable"
    assert runtime.controller.planning_order == ()


@pytest.mark.asyncio
async def test_invalid_soc_clears_cached_hint_immediately(setup_horizon):
    planner, runtime, _, _ = setup_horizon
    await evaluate(planner)
    assert runtime.controller.planning_order
    runtime.hass.states.get.__self__["sensor.fast_soc"] = State("sensor.fast_soc", "unavailable")
    await evaluate(planner)
    assert runtime.controller.planning_order == ()


@pytest.mark.asyncio
async def test_replay_runs_on_worker_thread_not_control_event_loop(setup_horizon, monkeypatch):
    planner, _, _, _ = setup_horizon
    actual = module._evaluate
    worker_threads = []

    def traced(inputs):
        worker_threads.append(threading.get_ident())
        return actual(inputs)

    monkeypatch.setattr(module, "_evaluate", traced)
    await evaluate(planner)
    assert worker_threads and all(identity != threading.get_ident() for identity in worker_threads)


@pytest.mark.asyncio
async def test_pending_model_cannot_reinstate_priority_after_option_change(setup_horizon):
    planner, runtime, _, entry = setup_horizon
    release = asyncio.Event()

    async def delayed(fn, *args):
        await release.wait()
        return await asyncio.to_thread(fn, *args)

    runtime.hass.async_add_executor_job = delayed
    planner.refresh()
    pending = planner._task
    await asyncio.sleep(0)
    entry.options["planning_enabled"] = False
    planner.refresh()
    assert planner.reason == "disabled"
    release.set()
    await pending
    assert runtime.controller.planning_order == ()
    assert planner.reason == "disabled"


@pytest.mark.asyncio
async def test_pending_model_discards_changed_valid_storage_snapshot(setup_horizon):
    planner, runtime, _, _ = setup_horizon
    release = asyncio.Event()

    async def delayed(fn, *args):
        await release.wait()
        return await asyncio.to_thread(fn, *args)

    runtime.hass.async_add_executor_job = delayed
    planner.refresh()
    pending = planner._task
    states = runtime.hass.states.get.__self__
    states["sensor.fast_soc"] = State(
        "sensor.fast_soc", "70", {"unit_of_measurement": "%"}, last_updated=NOW, last_reported=NOW
    )
    release.set()
    await pending
    assert runtime.controller.planning_order == ()
    assert planner.detail == "planning_snapshot_changed"


@pytest.mark.asyncio
async def test_pending_model_discards_individually_updated_forecast(setup_horizon):
    planner, runtime, _, _ = setup_horizon
    release = asyncio.Event()

    async def delayed(fn, *args):
        await release.wait()
        return await asyncio.to_thread(fn, *args)

    runtime.hass.async_add_executor_job = delayed
    planner.refresh()
    pending = planner._task
    states = runtime.hass.states.get.__self__
    previous = states["sensor.fast_today"]
    rows = [
        {**row, "pv_estimate": 0, "pv_estimate10": 0, "pv_estimate90": 0}
        for row in previous.attributes["detailedForecast"]
    ]
    states["sensor.fast_today"] = State(
        previous.entity_id,
        "0",
        {**previous.attributes, "detailedForecast": rows},
        last_updated=NOW + timedelta(seconds=1),
    )
    release.set()
    await pending
    assert runtime.controller.planning_order == ()
    assert planner.detail == "planning_snapshot_changed"


@pytest.mark.asyncio
async def test_pending_model_discards_result_older_than_live_measurement_window(
    setup_horizon, monkeypatch
):
    planner, runtime, _, _ = setup_horizon
    ticks = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: ticks[0])
    release = asyncio.Event()

    async def delayed(fn, *args):
        await release.wait()
        return await asyncio.to_thread(fn, *args)

    runtime.hass.async_add_executor_job = delayed
    planner.refresh()
    pending = planner._task
    ticks[0] += 16
    release.set()
    await pending
    assert runtime.controller.planning_order == ()
    assert planner.detail == "planning_snapshot_expired"


@pytest.mark.asyncio
async def test_unload_cancels_result_delivery_while_pure_worker_is_pending(setup_horizon):
    planner, runtime, _, _ = setup_horizon
    release = asyncio.Event()

    async def delayed(fn, *args):
        await release.wait()
        return await asyncio.to_thread(fn, *args)

    runtime.hass.async_add_executor_job = delayed
    planner.refresh()
    await asyncio.sleep(0)
    await planner.async_stop()
    release.set()
    assert runtime.controller.planning_order == ()
    assert planner.reason == "disabled"
