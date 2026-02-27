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

    def test_malformed_json_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "bad.json"
        cfg.write_text("{not valid json", encoding="utf-8")
        parser = _build_parser()
        args = _prepare_args(parser, ["--config", str(cfg)])
        with pytest.raises(ValueError, match="invalid syntax"):
            cli._build_options_from_sources(parser, args)

    def test_malformed_yaml_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("key: [unclosed\n  - bad", encoding="utf-8")
        parser = _build_parser()
        args = _prepare_args(parser, ["--config", str(cfg)])
        with pytest.raises(ValueError, match="invalid syntax"):
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
        cfg.write_text("x" * (cli.MAX_INPUT_FILE_SIZE + 1), encoding="utf-8")

        with pytest.raises(ValueError, match="too large"):
            cli._load_config_file(cfg)

    def test_normal_config_accepted(self, tmp_path: Path) -> None:
        cfg = tmp_path / "normal.json"
        cfg.write_text('{"city": "Paris", "country": "France"}', encoding="utf-8")
        result = cli._load_config_file(cfg)
        assert result["city"] == "Paris"


class TestNormalizeConfigData:
    """Tests for config normalization."""

    def test_unknown_keys_ignored_with_warning(self) -> None:
        raw = {"city": "Paris", "country": "France", "unknown_key": "value"}
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = cli._normalize_config_data(raw)
        assert "unknown_key" not in result
        assert result["city"] == "Paris"
        assert len(w) == 1
        assert "unknown_key" in str(w[0].message)

    def test_format_alias(self) -> None:
        raw = {"format": "svg"}
        result = cli._normalize_config_data(raw)
        assert result["output_format"] == "svg"

    def test_themes_string_to_list(self) -> None:
        raw = {"themes": "noir"}
        result = cli._normalize_config_data(raw)
        assert result["themes"] == ["noir"]


class TestNoAttributionOverride:
    """Tests for --no-attribution CLI flag (#R15-9)."""

    def test_no_attribution_overrides_default(self) -> None:
        parser = _build_parser()
        args = _prepare_args(parser, [
            "--city", "Paris", "--country", "France", "--no-attribution",
        ])
        options = cli._build_options_from_sources(parser, args)
        assert options.show_attribution is False

    def test_attribution_default_is_true(self) -> None:
        parser = _build_parser()
        args = _prepare_args(parser, ["--city", "Paris", "--country", "France"])
        options = cli._build_options_from_sources(parser, args)
        assert options.show_attribution is True


class TestCacheClearFlag:
    """Tests for --cache-clear flag."""

    @patch("maptoposter.core.cache_clear", return_value=5)
    def test_cache_clear_returns_zero(
        self, mock_clear: MagicMock, capsys: pytest.CaptureFixture[str],
    ) -> None:
        result = cli.main(["--cache-clear"])
        assert result == 0
        output = capsys.readouterr().out
        assert "5" in output

    @patch("maptoposter.core.cache_clear", return_value=0)
    def test_cache_clear_empty(
        self, mock_clear: MagicMock, capsys: pytest.CaptureFixture[str],
    ) -> None:
        result = cli.main(["--cache-clear"])
        assert result == 0
        output = capsys.readouterr().out
        assert "already empty" in output


class TestCacheInfoFlag:
    """Tests for --cache-info flag."""

    @patch("maptoposter.core.cache_info", return_value={
        "total_files": 2, "total_bytes": 4096,
        "entries": [{"key": "test_v2", "size_bytes": 2048, "created": None, "ttl": None}],
    })
    def test_cache_info_returns_zero(
        self, mock_info: MagicMock, capsys: pytest.CaptureFixture[str],
    ) -> None:
        result = cli.main(["--cache-info"])
        assert result == 0
        output = capsys.readouterr().out
        assert "2" in output


class TestBatchCLI:
    """Tests for --batch flag."""

    @patch("maptoposter.batch.run_batch", return_value={"total": 2, "successes": ["a", "b"], "failures": []})
    def test_batch_dispatches(self, mock_batch: MagicMock, tmp_path: Path) -> None:
        csv_file = tmp_path / "cities.csv"
        csv_file.write_text("city,country\nParis,France\nTokyo,Japan\n")
        result = cli.main(["--batch", str(csv_file), "--city", "X", "--country", "Y"])
        assert result == 0
        mock_batch.assert_called_once()
        # Verify city/country are stripped from global_overrides (batch entries provide them)
        overrides = mock_batch.call_args.kwargs.get("global_overrides", {})
        assert "city" not in overrides
        assert "country" not in overrides

    @patch("maptoposter.batch.run_batch", return_value={"total": 2, "successes": [], "failures": []})
    def test_batch_dry_run_passes_flag(self, mock_batch: MagicMock, tmp_path: Path) -> None:
        csv_file = tmp_path / "cities.csv"
        csv_file.write_text("city,country\nParis,France\nTokyo,Japan\n")
        result = cli.main(["--batch", str(csv_file), "--dry-run"])
        assert result == 0
        mock_batch.assert_called_once()
        assert mock_batch.call_args.kwargs["dry_run"] is True


class TestVersionFlag:
    """Tests for --version flag."""

    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit, match="0"):
            cli.main(["--version"])
        output = capsys.readouterr().out
        assert "maptoposter-cli" in output


class TestCLIHelpText:
    """Tests for improved help text."""

    def test_help_text_contains_improvements(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = _build_parser()
        help_text = parser.format_help()
        assert "max: 100000" in help_text
        assert "--list-themes" in help_text
        assert "--dry-run" in help_text
        assert "--batch" in help_text
        assert "--gallery" in help_text
        assert "Auto-reduced" in help_text

    def test_help_text_documents_env_vars(self) -> None:
        parser = _build_parser()
        help_text = parser.format_help()
        assert "MAPTOPOSTER_OUTPUT_DIR" in help_text
        assert "MAPTOPOSTER_CACHE_DIR" in help_text
        assert "MAPTOPOSTER_THEMES_DIR" in help_text
        assert "MAPTOPOSTER_NOMINATIM_DELAY" in help_text


class TestParseCoordinatesNone:
    """Test _parse_coordinates(None) returns None (#R19-3)."""

    def test_none_returns_none(self) -> None:
        assert cli._parse_coordinates(None) is None

    def test_valid_string_returns_float(self) -> None:
        result = cli._parse_coordinates("48.8566")
        assert result == pytest.approx(48.8566)


class TestDryRunKBFormatting:
    """Test dry-run shows KB for small poster sizes (#R19-4)."""

    @patch("maptoposter.cli._resolve_coordinates", return_value=(48.8566, 2.3522))
    def test_small_size_shows_kb(
        self,
        mock_coords: MagicMock,
        capsys: pytest.CaptureFixture[str],
        sample_theme: dict[str, str],  # noqa: ARG002
    ) -> None:
        # width=1, height=1, dpi=72 => 72*72*4/10 = 2074 bytes (~2 KB, well under 1 MB)
        result = cli.main([
            "--city", "Paris", "--country", "France",
            "--theme", "custom", "--dry-run",
            "--width", "1", "--height", "1", "--dpi", "72",
        ])
        assert result == 0
        output = capsys.readouterr().out
        assert "KB" in output
        assert "MB" not in output.split("Est. size")[1]


class TestGalleryFlag:
    """Test --gallery flag triggers gallery generation (#R19-5)."""

    @patch("maptoposter.gallery.generate_gallery", return_value="/tmp/gallery.html")
    @patch("maptoposter.cli.generate_posters", return_value=["/tmp/paris_custom.png"])
    @patch("maptoposter.cli._resolve_coordinates", return_value=(48.8566, 2.3522))
    def test_gallery_flag_calls_generate_gallery(
        self,
        mock_coords: MagicMock,
        mock_gen: MagicMock,
        mock_gallery: MagicMock,
        capsys: pytest.CaptureFixture[str],
        sample_theme: dict[str, str],  # noqa: ARG002
    ) -> None:
        result = cli.main([
            "--city", "Paris", "--country", "France",
            "--theme", "custom", "--gallery",
        ])
        assert result == 0
        mock_gallery.assert_called_once()
        output = capsys.readouterr().out
        assert "Gallery" in output

    @patch("maptoposter.gallery.generate_gallery", return_value="/tmp/gallery.html")
    @patch("maptoposter.batch.run_batch", return_value={"successes": ["/tmp/london.png"], "failures": []})
    def test_batch_gallery_flag(
        self,
        mock_batch: MagicMock,
        mock_gallery: MagicMock,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        csv_file = tmp_path / "cities.csv"
        csv_file.write_text("city,country\nLondon,UK\n")
        result = cli.main(["--batch", str(csv_file), "--gallery"])
        assert result == 0
        mock_batch.assert_called_once()
        mock_gallery.assert_called_once()
        output = capsys.readouterr().out
        assert "Gallery" in output

    @patch("maptoposter.cli.generate_posters", return_value=["/tmp/paris_custom.png"])
    @patch("maptoposter.cli._resolve_coordinates", return_value=(48.8566, 2.3522))
    def test_no_gallery_flag_skips_gallery(
        self,
        mock_coords: MagicMock,
        mock_gen: MagicMock,
        capsys: pytest.CaptureFixture[str],
        sample_theme: dict[str, str],  # noqa: ARG002
    ) -> None:
        result = cli.main([
            "--city", "Paris", "--country", "France",
            "--theme", "custom",
        ])
        assert result == 0
        output = capsys.readouterr().out
        assert "Gallery" not in output


class TestGeneratePostersValueError:
    """Test main() handles ValueError from generate_posters (#R20-3)."""

    @patch("maptoposter.cli.generate_posters", side_effect=ValueError("bad config"))
    @patch("maptoposter.cli._resolve_coordinates", return_value=(48.8566, 2.3522))
    def test_value_error_returns_1(
        self,
        mock_coords: MagicMock,
        mock_gen: MagicMock,
        capsys: pytest.CaptureFixture[str],
        sample_theme: dict[str, str],  # noqa: ARG002
    ) -> None:
        result = cli.main([
            "--city", "Paris", "--country", "France", "--theme", "custom",
        ])
        assert result == 1
        output = capsys.readouterr().out
        assert "Configuration error" in output
        assert "bad config" in output


class TestConfigStringNumericCoercion:
    """Config values with string-typed numerics are coerced to correct types."""

    def test_string_distance_coerced_to_int(self) -> None:
        raw = {"city": "Paris", "country": "France", "distance": "18000"}
        result = cli._normalize_config_data(raw)
        assert result["distance"] == 18000
        assert isinstance(result["distance"], int)

    def test_string_dpi_coerced_to_int(self) -> None:
        raw = {"city": "Paris", "country": "France", "dpi": "300"}
        result = cli._normalize_config_data(raw)
        assert result["dpi"] == 300
        assert isinstance(result["dpi"], int)

    def test_string_width_coerced_to_float(self) -> None:
        raw = {"city": "Paris", "country": "France", "width": "14.5"}
        result = cli._normalize_config_data(raw)
        assert result["width"] == pytest.approx(14.5)
        assert isinstance(result["width"], float)

    def test_string_height_coerced_to_float(self) -> None:
        raw = {"city": "Paris", "country": "France", "height": "11.0"}
        result = cli._normalize_config_data(raw)
        assert result["height"] == pytest.approx(11.0)
        assert isinstance(result["height"], float)

    def test_int_city_coerced_to_string(self) -> None:
        raw = {"city": 123, "country": "France"}
        result = cli._normalize_config_data(raw)
        assert result["city"] == "123"
        assert isinstance(result["city"], str)

    def test_int_country_coerced_to_string(self) -> None:
        raw = {"city": "Paris", "country": 456}
        result = cli._normalize_config_data(raw)
        assert result["country"] == "456"
        assert isinstance(result["country"], str)

    def test_numeric_types_already_correct_passthrough(self) -> None:
        raw = {"city": "Paris", "country": "France", "distance": 5000, "width": 12.0}
        result = cli._normalize_config_data(raw)
        assert result["distance"] == 5000
        assert result["width"] == 12.0


class TestParallelThemesCLI:
    """Tests for --parallel-themes CLI flag."""

    def test_parallel_themes_override(self) -> None:
        parser = cli._build_parser()
        cli._add_arguments(parser)
        args = parser.parse_args(["--city", "X", "--country", "Y", "--parallel-themes"])
        overrides = cli._collect_cli_overrides(parser, args)
        assert overrides["parallel_themes"] is True

    def test_parallel_themes_default_not_in_overrides(self) -> None:
        parser = cli._build_parser()
        cli._add_arguments(parser)
        args = parser.parse_args(["--city", "X", "--country", "Y"])
        overrides = cli._collect_cli_overrides(parser, args)
        assert "parallel_themes" not in overrides


class TestParallelBatchCLI:
    """Tests for --parallel and --max-workers batch CLI flags."""

    @patch("maptoposter.batch.run_batch", return_value={"total": 2, "successes": ["a"], "failures": []})
    def test_batch_parallel_flags_passed(self, mock_batch: MagicMock, tmp_path: Path) -> None:
        csv_file = tmp_path / "cities.csv"
        csv_file.write_text("city,country\nParis,France\n")
        result = cli.main(["--batch", str(csv_file), "--parallel", "--max-workers", "8"])
        assert result == 0
        mock_batch.assert_called_once()
        assert mock_batch.call_args.kwargs["parallel"] is True
        assert mock_batch.call_args.kwargs["max_workers"] == 8
