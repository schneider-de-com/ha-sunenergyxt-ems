"""Check the packaged manifest and single-source beta release notes."""

from json import loads
from pathlib import Path
from re import fullmatch

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_uses_changelog_not_per_tag_files() -> None:
    manifest = loads((ROOT / "custom_components/sunenergyxt_ems/manifest.json").read_text())
    version = manifest["version"]
    assert isinstance(version, str) and fullmatch(
        r"(?:\d+\.\d+\.\d+b\d+|\d+\.\d+\.\d+-(?:alpha|beta|rc)\.\d+)",
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
