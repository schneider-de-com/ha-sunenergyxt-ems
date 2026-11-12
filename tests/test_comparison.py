"""Internal costs must preserve coverage and distinguish model from measurement."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.sunenergyxt_ems.comparison import compare, gpe_prices
from custom_components.sunenergyxt_ems.forecast import EnergyInterval, ForecastError
from custom_components.sunenergyxt_ems.logic import Battery, Inverter

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)


def prices(values=(0.2, 0.4)):
    return {
        "prices": [
            {
                "start": (NOW + timedelta(minutes=15 * i)).isoformat(),
                "end": (NOW + timedelta(minutes=15 * (i + 1))).isoformat(),
                "price": price,
            }
            for i, price in enumerate(values)
        ]
    }


def comparison(response=None):
    return compare(
        (Battery("store", 10, 10, 100, (Inverter("inv", 800),)),),
        {"store": 2150},
        (EnergyInterval(NOW, NOW + timedelta(minutes=30), 1000),),
        {"store": (EnergyInterval(NOW, NOW + timedelta(minutes=30), 0),)},
        flat_eur_per_kwh=0.23,
        dynamic_prices=gpe_prices(prices() if response is None else response, unit="EUR/kWh"),
    )


def test_same_energy_replay_is_priced_at_actual_quarter_hour_intervals():
    result = comparison()
    assert result.baseline.import_wh == result.planned.import_wh == 1000
    assert result.baseline.flat_import_eur == pytest.approx(0.23)
    assert result.baseline.dynamic_import_eur == pytest.approx(0.3)
    assert result.mode == "internal_model_not_measured_savings"
    assert result.dynamic_use == "hypothetical_pricing_no_control"
    assert result.planned.export_wh is None
    assert result.planned.measured_curtailment_wh is None


@pytest.mark.parametrize("values", [(0.2,), ()])
def test_missing_dynamic_intervals_are_never_zero_priced(values):
    with pytest.raises(ForecastError, match="price_coverage"):
        comparison(prices(values))


@pytest.mark.parametrize(
    "field,value",
    [("price", True), ("price", float("nan")), ("price", "0.2"), ("start", "2026-11-12T12:00:00")],
)
def test_invalid_price_contract_is_rejected(field, value):
    response = prices()
    response["prices"][0][field] = value
    with pytest.raises(ForecastError):
        comparison(response)


def test_negative_dynamic_price_is_supported_without_grid_charging():
    result = comparison(prices((-0.2, -0.4)))
    assert result.baseline.dynamic_import_eur == pytest.approx(-0.3)
    assert result.baseline.final_usable_dc_wh == {"store": 0}
    assert result.baseline.battery_discharge_dc_wh == {"store": 0}


def test_gap_duplicate_and_overlap_are_rejected():
    for rows in ([prices()["prices"][0]] * 2, prices()["prices"][::-1]):
        with pytest.raises(ForecastError):
            gpe_prices({"prices": rows}, unit="EUR/kWh")


def test_tariff_units_must_be_explicitly_verified():
    with pytest.raises(ForecastError):
        gpe_prices(prices(), unit="ct/kWh")


def test_unrepresentable_price_is_a_contract_error():
    with pytest.raises(ForecastError, match="invalid_price"):
        comparison(prices((10**400, 0.4)))


def test_dynamic_prices_cannot_change_the_flat_price_priority_plan():
    a = comparison(prices((0.01, 2)))
    b = comparison(prices((2, 0.01)))
    assert a.order == b.order == ()
    assert a.expected_advantage_wh == b.expected_advantage_wh == 0


def test_absent_prices_remain_unknown_and_partial_slot_uses_overlap():
    batteries = (Battery("store", 10, 10, 100, (Inverter("inv", 800),)),)
    start, end = NOW + timedelta(minutes=5), NOW + timedelta(minutes=25)
    kwargs = {
        "batteries": batteries,
        "capacities": {"store": 2150},
        "demand": (EnergyInterval(start, end, 1000),),
        "pv": {"store": (EnergyInterval(start, end, 0),)},
    }
    unknown = compare(**kwargs)
    assert unknown.baseline.flat_import_eur is None
    assert unknown.baseline.dynamic_import_eur is None
    priced = compare(**kwargs, dynamic_prices=gpe_prices(prices(), unit="EUR/kWh"))
    assert priced.baseline.dynamic_import_eur == pytest.approx(0.3)


def test_price_gap_is_rejected_even_when_import_is_zero():
    with pytest.raises(ForecastError, match="price_coverage"):
        compare(
            (Battery("store", 90, 10, 100, (Inverter("inv", 800),)),),
            {"store": 2150},
            (EnergyInterval(NOW, NOW + timedelta(minutes=30), 0),),
            {"store": (EnergyInterval(NOW, NOW + timedelta(minutes=30), 0),)},
            dynamic_prices=(),
        )
