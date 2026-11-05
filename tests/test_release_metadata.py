"""Ensure HACS release metadata agrees with the version actually packaged."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_notes_and_readme_match_manifest() -> None:
    manifest = json.loads(
        (ROOT / "custom_components/sunenergyxt_ems/manifest.json").read_text()
    )
    version = manifest["version"]
    assert isinstance(version, str) and version.startswith("0.1.0b")
    notes = ROOT / f"RELEASE_NOTES_{version}.md"
    assert notes.is_file(), f"Missing release notes for packaged {version}"
    assert notes.read_text().startswith(f"# SunEnergyXT EMS v{version}")
    assert f"v{version}" in (ROOT / "README.md").read_text()

