"""Tests for batch processing module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maptoposter.batch import _is_transient, load_batch_file, run_batch


class TestLoadBatchCSV:
    """Tests for CSV batch file loading."""

    def test_basic_csv(self, tmp_path: Path) -> None:
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\nTokyo,Japan\n")
        entries = load_batch_file(csv)
        assert len(entries) == 2
        assert entries[0]["city"] == "Paris"
        assert entries[1]["country"] == "Japan"

    def test_missing_columns_raises(self, tmp_path: Path) -> None:
        csv = tmp_path / "bad.csv"
        csv.write_text("name,place\nParis,France\n")
        with pytest.raises(ValueError, match="must have 'city' and 'country'"):
            load_batch_file(csv)

    def test_numeric_coercion(self, tmp_path: Path) -> None:
        csv = tmp_path / "numeric.csv"
        csv.write_text("city,country,distance,width\nParis,France,12000,14.5\n")
        entries = load_batch_file(csv)
        assert entries[0]["distance"] == 12000
        assert entries[0]["width"] == 14.5

    def test_invalid_numeric_field_skipped(self, tmp_path: Path) -> None:
        """Invalid numeric values should be dropped from the entry, not kept as strings."""
        csv = tmp_path / "bad_num.csv"
        csv.write_text("city,country,distance\nParis,France,abc\n")
        entries = load_batch_file(csv)
        assert "distance" not in entries[0]  # field skipped, not kept as "abc"

    def test_invalid_float_field_skipped(self, tmp_path: Path) -> None:
        """Invalid float fields (width, height, lat, lon) should be skipped (#R16-10)."""
        csv = tmp_path / "bad_float.csv"
        csv.write_text("city,country,width,height\nParis,France,abc,xyz\n")
        entries = load_batch_file(csv)
        assert "width" not in entries[0]
        assert "height" not in entries[0]
        assert entries[0]["city"] == "Paris"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_batch_file(tmp_path / "nope.csv")

    def test_too_large_raises(self, tmp_path: Path) -> None:
        csv = tmp_path / "huge.csv"
        csv.write_text("city,country\n" + "Paris,France\n" * 100_000)
        with pytest.raises(ValueError, match="too large"):
            load_batch_file(csv)


class TestLoadBatchJSON:
    """Tests for JSON batch file loading."""

    def test_list_format(self, tmp_path: Path) -> None:
        jf = tmp_path / "cities.json"
        jf.write_text(json.dumps([
            {"city": "Paris", "country": "France"},
            {"city": "Tokyo", "country": "Japan"},
        ]))
        entries = load_batch_file(jf)
        assert len(entries) == 2

    def test_cities_key_format(self, tmp_path: Path) -> None:
        jf = tmp_path / "cities.json"
        jf.write_text(json.dumps({
            "cities": [
                {"city": "London", "country": "UK"},
            ]
        }))
        entries = load_batch_file(jf)
        assert len(entries) == 1
        assert entries[0]["city"] == "London"

    def test_invalid_format_raises(self, tmp_path: Path) -> None:
        jf = tmp_path / "bad.json"
        jf.write_text(json.dumps({"name": "not a batch"}))
        with pytest.raises(ValueError, match="must be a list"):
            load_batch_file(jf)


class TestRunBatch:
    """Tests for run_batch execution."""

    @patch("maptoposter.batch.generate_posters", return_value=["/tmp/out.png"])
    def test_all_succeed(self, mock_gen: MagicMock, tmp_path: Path) -> None:
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\nTokyo,Japan\n")
        result = run_batch(csv)
        assert result["total"] == 2
        assert len(result["successes"]) == 2
        assert len(result["failures"]) == 0

    @patch("maptoposter.batch.generate_posters")
    def test_partial_failure(self, mock_gen: MagicMock, tmp_path: Path) -> None:
        mock_gen.side_effect = [
            ["/tmp/paris.png"],
            RuntimeError("OSM error"),
        ]
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\nNowhere,Land\n")
        result = run_batch(csv)
        assert result["total"] == 2
        assert len(result["successes"]) == 1
        assert len(result["failures"]) == 1

    @patch("maptoposter.batch.generate_posters", return_value=["/tmp/out.png"])
    def test_global_overrides_applied(self, mock_gen: MagicMock, tmp_path: Path) -> None:
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\n")
        run_batch(csv, global_overrides={"distance": 5000, "theme": "noir"})
        call_args = mock_gen.call_args
        options = call_args[0][0]
        assert options.distance == 5000
        assert options.theme == "noir"


class TestIsTransient:
    """Tests for transient error classification."""

    def test_connection_error_is_transient(self) -> None:
        assert _is_transient(ConnectionError("refused")) is True

    def test_timeout_error_is_transient(self) -> None:
        assert _is_transient(TimeoutError("timed out")) is True

    def test_os_error_is_transient(self) -> None:
        assert _is_transient(OSError("timed out")) is True

    def test_rate_limit_message_is_transient(self) -> None:
        assert _is_transient(RuntimeError("rate limit exceeded")) is True

    def test_service_unavailable_is_transient(self) -> None:
        assert _is_transient(RuntimeError("service unavailable")) is True

    def test_value_error_is_permanent(self) -> None:
        assert _is_transient(ValueError("bad input")) is False

    def test_file_not_found_is_permanent(self) -> None:
        assert _is_transient(FileNotFoundError("missing")) is False


class TestBatchRetry:
    """Tests for batch retry on transient failures."""

    @patch("maptoposter.batch.time.sleep")
    @patch("maptoposter.batch.generate_posters")
    def test_retries_on_transient_then_succeeds(
        self, mock_gen: MagicMock, mock_sleep: MagicMock, tmp_path: Path,
    ) -> None:
        mock_gen.side_effect = [
            ConnectionError("network down"),
            ["/tmp/paris.png"],
        ]
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\n")
        result = run_batch(csv)
        assert len(result["successes"]) == 1
        assert len(result["failures"]) == 0
        mock_sleep.assert_called_once_with(2)

    @patch("maptoposter.batch.time.sleep")
    @patch("maptoposter.batch.generate_posters")
    def test_no_retry_on_permanent_error(
        self, mock_gen: MagicMock, mock_sleep: MagicMock, tmp_path: Path,
    ) -> None:
        mock_gen.side_effect = ValueError("bad input")
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\n")
        result = run_batch(csv)
        assert len(result["failures"]) == 1
        mock_sleep.assert_not_called()

    @patch("maptoposter.batch.time.sleep")
    @patch("maptoposter.batch.generate_posters")
    def test_exhausts_retries(
        self, mock_gen: MagicMock, mock_sleep: MagicMock, tmp_path: Path,
    ) -> None:
        mock_gen.side_effect = ConnectionError("always fails")
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\n")
        result = run_batch(csv)
        assert len(result["failures"]) == 1
        # 2 retries = 2 sleep calls with backoff values [2, 5]
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0][0] == (2,)
        assert mock_sleep.call_args_list[1][0] == (5,)


class TestBatchDryRun:
    """Tests for batch --dry-run."""

    @patch("maptoposter.batch.generate_posters")
    def test_dry_run_skips_generation(self, mock_gen: MagicMock, tmp_path: Path) -> None:
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\nTokyo,Japan\n")
        result = run_batch(csv, dry_run=True)
        mock_gen.assert_not_called()
        assert result["total"] == 2
        assert len(result["successes"]) == 0
        assert len(result["failures"]) == 0


class TestBatchEdgeCases:
    """Edge case tests for batch file loading (#13)."""

    def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        xml = tmp_path / "cities.xml"
        xml.write_text("<cities/>")
        with pytest.raises(ValueError, match="Unsupported batch file format"):
            load_batch_file(xml)

    def test_json_cities_not_a_list_raises(self, tmp_path: Path) -> None:
        jf = tmp_path / "cities.json"
        jf.write_text(json.dumps({"cities": "not a list"}))
        with pytest.raises(ValueError, match="'cities' must be a list"):
            load_batch_file(jf)

    def test_json_entries_not_dicts_raises(self, tmp_path: Path) -> None:
        jf = tmp_path / "cities.json"
        jf.write_text(json.dumps({"cities": ["Paris", "Tokyo"]}))
        with pytest.raises(ValueError, match="each entry must be a dict"):
            load_batch_file(jf)

    def test_csv_empty_header_raises(self, tmp_path: Path) -> None:
        csv = tmp_path / "empty.csv"
        csv.write_text("")
        with pytest.raises(ValueError, match="no header row"):
            load_batch_file(csv)

    def test_csv_header_only_returns_empty(self, tmp_path: Path) -> None:
        """A CSV with header but no data rows should return an empty list."""
        csv = tmp_path / "header_only.csv"
        csv.write_text("city,country\n")
        entries = load_batch_file(csv)
        assert entries == []


class TestBatchEmptyCityCountry:
    """Tests for empty city/country early validation in run_batch (#R11-6)."""

    @patch("maptoposter.batch.generate_posters", return_value=["/tmp/out.png"])
    def test_empty_city_skipped(self, mock_gen: MagicMock, tmp_path: Path) -> None:
        jf = tmp_path / "cities.json"
        jf.write_text(json.dumps([
            {"city": "", "country": "France"},
            {"city": "Tokyo", "country": "Japan"},
        ]))
        result = run_batch(jf)
        assert result["total"] == 2
        assert len(result["failures"]) == 1
        assert result["failures"][0]["error"] == "empty city or country"
        # Only the valid entry was generated
        assert len(result["successes"]) == 1

    @patch("maptoposter.batch.generate_posters", return_value=["/tmp/out.png"])
    def test_whitespace_only_country_skipped(self, mock_gen: MagicMock, tmp_path: Path) -> None:
        jf = tmp_path / "cities.json"
        jf.write_text(json.dumps([
            {"city": "Paris", "country": "   "},
        ]))
        result = run_batch(jf)
        assert len(result["failures"]) == 1
        assert result["failures"][0]["error"] == "empty city or country"


class TestBatchJsonMissingCityCountry:
    """Test batch JSON entry missing 'city' or 'country' key raises (#R18-4)."""

    def test_missing_country_key_raises(self, tmp_path: Path) -> None:
        jf = tmp_path / "cities.json"
        jf.write_text(json.dumps([{"city": "Paris"}]))
        with pytest.raises(ValueError, match="must have 'city' and 'country'"):
            load_batch_file(jf)

    def test_missing_city_key_raises(self, tmp_path: Path) -> None:
        jf = tmp_path / "cities.json"
        jf.write_text(json.dumps([{"country": "France"}]))
        with pytest.raises(ValueError, match="must have 'city' and 'country'"):
            load_batch_file(jf)


class TestBatchOuterExceptionHandler:
    """Test batch outer except catches PosterGenerationOptions errors (#R18-5)."""

    @patch("maptoposter.batch.generate_posters")
    def test_invalid_options_caught_as_failure(self, mock_gen: MagicMock, tmp_path: Path) -> None:
        # Use an invalid distance value that will cause PosterGenerationOptions to raise ValueError
        jf = tmp_path / "cities.json"
        jf.write_text(json.dumps([
            {"city": "Paris", "country": "France", "distance": -1},
        ]))
        result = run_batch(jf)
        # ValueError from PosterGenerationOptions is caught by outer handler
        assert len(result["failures"]) == 1
        mock_gen.assert_not_called()
