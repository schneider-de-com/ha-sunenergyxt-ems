"""Ensure setup explanations remain actionable for new Home Assistant users."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "sunenergyxt_ems"


@pytest.mark.parametrize(
    "locale",
    ["strings.json", "translations/en.json", "translations/de.json"],
)
def test_setup_steps_explain_each_choice(locale):
    content = json.loads((ROOT / locale).read_text(encoding="utf-8"))
    for step in (
        "user",
        "inverters",
        "ratings",
        "grid",
        "correct",
        "reconfigure",
    ):
        description = content["config"]["step"][step]["description"]
        assert len(description) >= 140, f"{locale}: {step} lacks a clear explanation"
    assert len(content["options"]["step"]["init"]["description"]) >= 100


def test_german_meter_explanation_defines_sign_and_activation():
    data = json.loads((ROOT / "translations/de.json").read_text(encoding="utf-8"))
    grid = data["config"]["step"]["grid"]["description"]
    assert "Positiv" in grid
    assert "negativ" in grid
    assert "automatisch" in grid
    rating = data["config"]["step"]["ratings"]["description"]
    assert "Nennleistung" in rating
    assert "AC" in rating
