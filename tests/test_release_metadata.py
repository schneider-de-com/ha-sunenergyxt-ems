"""Check legacy and SemVer manifests against the changelog-based release process."""

from json import loads
from pathlib import Path
from re import fullmatch

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_uses_changelog_not_per_tag_files() -> None:
    manifest = loads((ROOT / "custom_components/sunenergyxt_ems/manifest.json").read_text())
    version = manifest["version"]
    assert isinstance(version, str) and fullmatch(
        r"(?:\d+\.\d+\.\d+b\d+|\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?)",
        version,
    )

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in changelog
    assert "### Added" in changelog
    assert "### Fixed" in changelog
    assert "Keep a Changelog" in changelog

    # Historical releases are on GitHub, not duplicated into tracked files.
    assert not list(ROOT.glob("RELEASE_NOTES_*.md"))
    workflow = (ROOT / ".github/workflows/beta-release.yml").read_text(encoding="utf-8")
    assert "--notes-file" in workflow
    assert "CHANGELOG.md" in workflow
    assert "RELEASE_NOTES_" not in workflow


def test_current_documentation_never_claims_stored_battery_export_as_pv_only() -> None:
    """Older beta notes are historical; current product promise must be accurate."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "PV feed-in can export stored battery energy" not in readme
    assert "verified BMS" in readme
    assert "fresh" in readme and "PV" in readme

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## [Unreleased]", 1)[1].split("## [1.0.0-beta.1]", 1)[0]
    assert "PV-only" in unreleased
    assert "BMS" in unreleased
    assert "not installed" in unreleased
    assert "hardware" in unreleased

