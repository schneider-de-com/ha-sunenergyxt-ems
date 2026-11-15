"""Ensure HACS release metadata agrees with the version actually packaged."""

from json import loads
from pathlib import Path
from re import fullmatch

ROOT = Path(__file__).resolve().parents[1]


def test_release_notes_and_readme_match_manifest() -> None:
    manifest = loads((ROOT / "custom_components/sunenergyxt_ems/manifest.json").read_text())
    version = manifest["version"]
    assert isinstance(version, str)
    # Preserve existing immutable Python-style 0.x prerelease tags, but
    # validate all new releases against strict SemVer without leading zeros.
    legacy = fullmatch(r"0\.\d+\.\d+b\d+", version)
    semver = fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
        r"(?:-(?:alpha|beta|rc)\.(0|[1-9]\d*))?",
        version,
    )
    assert legacy or semver, f"Invalid release version: {version}"
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert f"## [{version}]" in changelog, "Current released version missing from changelog"
    notes = ROOT / f"RELEASE_NOTES_{version}.md"
    assert notes.is_file(), f"Missing release notes for packaged {version}"
    assert notes.read_text().startswith(f"# SunEnergyXT EMS v{version}")
    assert f"v{version}" in (ROOT / "README.md").read_text()


def test_release_history_is_human_readable() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert changelog.startswith("# Changelog")
    assert "## [Unreleased]" in changelog
    assert any(category in changelog for category in ("### Added", "### Fixed", "### Changed"))
    guide = (ROOT / "CONTRIBUTING.md").read_text()
    assert "## Versioning and release history" in guide
    assert "Conventional Commits" in guide
