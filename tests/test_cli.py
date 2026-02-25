"""Tests for CLI configuration merging, overrides, and entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from maptoposter import cli


def _build_parser() -> argparse.ArgumentParser:
    parser = cli._build_parser()
    cli._add_arguments(parser)
    return parser


def _prepare_args(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace:
    args = parser.parse_args(argv)
    args.latitude = cli._parse_coordinates(args.latitude)
    args.longitude = cli._parse_coordinates(args.longitude)
    return args


def test_build_options_from_config(tmp_path: Path) -> None:
    config = {
        "city": "Paris",
        "country": "France",
        "themes": ["terracotta", "neon_cyberpunk"],
        "distance": 9000,
        "width": 10,
        "height": 14,
        "output_dir": str(tmp_path / "out"),
    }
    cfg = tmp_path / "poster.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")

    parser = _build_parser()
    args = _prepare_args(parser, ["--config", str(cfg)])
    options = cli._build_options_from_sources(parser, args)

    assert options.city == "Paris"
    assert options.country == "France"
    assert options.themes == config["themes"]
    assert options.distance == 9000
    assert options.width == 10
    assert options.output_dir.endswith("out")


def test_cli_overrides_config_values(tmp_path: Path) -> None:
    config = {
        "city": "Paris",
        "country": "France",
        "width": 12,
        "height": 18,
        "no_attribution": True,
    }
    cfg = tmp_path / "poster.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")

    parser = _build_parser()
    args = _prepare_args(
        parser,
        [
            "--config",
            str(cfg),
            "--width",
            "15",
            "--themes",
            "emerald",
            "forest",
        ],
    )
    options = cli._build_options_from_sources(parser, args)

    assert options.width == 15  # CLI override
    assert options.height == 18  # config preserved
    assert options.themes == ["emerald", "forest"]
    assert options.show_attribution is False  # config no_attribution


def test_config_parses_lat_lon_strings(tmp_path: Path) -> None:
    config = {
        "city": "Paris",
        "country": "France",
        "latitude": "48.8566",
        "longitude": "2.3522",
    }
    cfg = tmp_path / "poster.yaml"
    cfg.write_text(json.dumps(config), encoding="utf-8")

    parser = _build_parser()
    args = _prepare_args(parser, ["--config", str(cfg)])
    options = cli._build_options_from_sources(parser, args)

    assert options.latitude == pytest.approx(48.8566)
    assert options.longitude == pytest.approx(2.3522)


# --- New test classes ---


class TestMainEntryPoint:
    """Tests for the main() function."""

    def test_no_args_prints_examples(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = cli.main([])
        assert result == 0
        output = capsys.readouterr().out
        assert "City Map Poster Generator" in output

    @patch("maptoposter.cli.list_themes")
    def test_list_themes_flag(self, mock_list: MagicMock) -> None:
        result = cli.main(["--list-themes"])
        assert result == 0
        mock_list.assert_called_once()

    def test_missing_city_returns_error(self) -> None:
        result = cli.main(["--country", "France"])
        assert result == 1

    def test_missing_country_returns_error(self) -> None:
        result = cli.main(["--city", "Paris"])
        assert result == 1

    def test_debug_flag_accepted(self) -> None:
        """--debug flag should parse without error even with missing city/country."""
        result = cli.main(["--debug", "--city", "Paris"])
        assert result == 1  # missing country

    @patch("maptoposter.cli.list_themes")
    def test_debug_with_list_themes(self, mock_list: MagicMock) -> None:
        result = cli.main(["--debug", "--list-themes"])
        assert result == 0


class TestConfigFileLoading:
    """Tests for YAML/JSON config loading."""

    def test_yaml_config(self, tmp_path: Path) -> None:
        config = {"city": "Tokyo", "country": "Japan", "distance": 15000}
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump(config), encoding="utf-8")

        parser = _build_parser()
        args = _prepare_args(parser, ["--config", str(cfg)])
        options = cli._build_options_from_sources(parser, args)

        assert options.city == "Tokyo"
        assert options.country == "Japan"
        assert options.distance == 15000

    def test_invalid_config_root(self, tmp_path: Path) -> None:
        cfg = tmp_path / "bad.json"
        cfg.write_text('"just a string"', encoding="utf-8")

        parser = _build_parser()
        args = _prepare_args(parser, ["--config", str(cfg)])

        with pytest.raises(ValueError, match="Config root must be"):
            cli._build_options_from_sources(parser, args)

    def test_missing_config_file(self) -> None:
        parser = _build_parser()
        args = _prepare_args(parser, ["--config", "/nonexistent/config.json"])

        with pytest.raises(FileNotFoundError, match="not found"):
            cli._build_options_from_sources(parser, args)


class TestDryRun:
    """Tests for --dry-run flag."""

    @patch("maptoposter.cli._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoposter.cli.generate_posters")
    def test_dry_run_returns_zero_with_summary(
        self,
        mock_generate: MagicMock,
        mock_coords: MagicMock,
        capsys: pytest.CaptureFixture[str],
        sample_theme: dict[str, str],  # noqa: ARG002
    ) -> None:
        result = cli.main(["--city", "Paris", "--country", "France", "--theme", "custom", "--dry-run"])
        assert result == 0
        output = capsys.readouterr().out
        assert "Dry Run Summary" in output
        assert "Paris" in output
        mock_generate.assert_not_called()

    @patch("maptoposter.cli._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoposter.cli.generate_posters")
    def test_dry_run_does_not_call_generate(
        self,
        mock_generate: MagicMock,
        mock_coords: MagicMock,
        sample_theme: dict[str, str],  # noqa: ARG002
    ) -> None:
        cli.main(["--city", "Tokyo", "--country", "Japan", "--theme", "custom", "--dry-run"])
        mock_generate.assert_not_called()


class TestConfigFileSizeLimit:
    """Tests for config file size limit."""

    def test_oversized_config_raises(self, tmp_path: Path) -> None:
        cfg = tmp_path / "huge.json"
        # Write just over 1 MB
        cfg.write_text("x" * (cli._MAX_CONFIG_SIZE + 1), encoding="utf-8")

        with pytest.raises(ValueError, match="too large"):
            cli._load_config_file(cfg)

    def test_normal_config_accepted(self, tmp_path: Path) -> None:
        cfg = tmp_path / "normal.json"
        cfg.write_text('{"city": "Paris", "country": "France"}', encoding="utf-8")
        result = cli._load_config_file(cfg)
        assert result["city"] == "Paris"


class TestNormalizeConfigData:
    """Tests for config normalization."""

    def test_unknown_keys_ignored(self) -> None:
        raw = {"city": "Paris", "country": "France", "unknown_key": "value"}
        result = cli._normalize_config_data(raw)
        assert "unknown_key" not in result
        assert result["city"] == "Paris"

    def test_format_alias(self) -> None:
        raw = {"format": "svg"}
        result = cli._normalize_config_data(raw)
        assert result["output_format"] == "svg"

    def test_themes_string_to_list(self) -> None:
        raw = {"themes": "noir"}
        result = cli._normalize_config_data(raw)
        assert result["themes"] == ["noir"]


class TestCLIHelpText:
    """Tests for improved help text."""

    def test_help_text_contains_improvements(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = _build_parser()
        help_text = parser.format_help()
        assert "max: 100000" in help_text
        assert "Typical values" in help_text
        assert "--list-themes" in help_text
        assert "--dry-run" in help_text
