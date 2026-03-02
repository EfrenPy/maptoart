"""Tests for theme key validation and fallback behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import maptoart.core as core


class TestThemeValidation:
    """Verify that load_theme fills missing keys from _TERRACOTTA_DEFAULTS."""

    def test_missing_keys_filled(
        self,
        sample_theme_dir: Path,
    ) -> None:
        """A theme missing a few keys should get them from defaults."""
        partial = {
            "name": "Partial",
            "description": "Incomplete",
            "bg": "#000000",
            "text": "#ffffff",
            # missing all road_* and gradient, water, parks
        }
        (sample_theme_dir / "partial.json").write_text(json.dumps(partial))

        loaded = core.load_theme("partial")
        assert loaded["name"] == "Partial"
        assert loaded["bg"] == "#000000"
        # filled from defaults
        assert loaded["road_motorway"] == core._TERRACOTTA_DEFAULTS["road_motorway"]
        assert loaded["water"] == core._TERRACOTTA_DEFAULTS["water"]

    def test_valid_theme_unchanged(
        self,
        sample_theme: dict[str, str],
    ) -> None:
        """A complete theme should not be altered."""
        loaded = core.load_theme("custom")
        for key in core.REQUIRED_THEME_KEYS:
            assert loaded[key] == sample_theme[key]

    def test_fallback_to_terracotta_on_missing_file(self) -> None:
        """Missing file should return the full terracotta palette."""
        theme = core.load_theme("does_not_exist")
        assert theme == core._TERRACOTTA_DEFAULTS

    def test_required_keys_constant(self) -> None:
        """Sanity-check that REQUIRED_THEME_KEYS has the expected count."""
        assert len(core.REQUIRED_THEME_KEYS) == 13
        assert "bg" in core.REQUIRED_THEME_KEYS
        assert "road_default" in core.REQUIRED_THEME_KEYS


class TestThemeColorValidation:
    """Tests for hex color validation in load_theme."""

    def test_invalid_hex_color_raises(self, sample_theme_dir: Path) -> None:
        bad_theme = dict(core._TERRACOTTA_DEFAULTS, name="Bad", bg="not-a-color")
        (sample_theme_dir / "badcolor.json").write_text(json.dumps(bad_theme))
        with pytest.raises(ValueError, match="invalid color for 'bg'"):
            core.load_theme("badcolor")

    def test_all_shipped_themes_valid(self) -> None:
        themes_dir = core.DEFAULT_THEMES_DIR
        theme_files = sorted(themes_dir.glob("*.json"))
        assert len(theme_files) > 0, "No shipped themes found"
        for tf in theme_files:
            # load_theme validates colors; just verify no exception
            core.load_theme(tf.stem)
