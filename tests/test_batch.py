"""Tests for batch processing module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from maptoart.batch import (
    _is_transient,
    _pre_geocode_batch,
    _process_city_worker,
    load_batch_file,
    run_batch,
)


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

    @patch("maptoart.batch.generate_posters", return_value=["/tmp/out.png"])
    def test_all_succeed(self, mock_gen: MagicMock, tmp_path: Path) -> None:
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\nTokyo,Japan\n")
        result = run_batch(csv)
        assert result["total"] == 2
        assert len(result["successes"]) == 2
        assert len(result["failures"]) == 0

    @patch("maptoart.batch.generate_posters")
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

    @patch("maptoart.batch.generate_posters", return_value=["/tmp/out.png"])
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

    @patch("maptoart.batch.time.sleep")
    @patch("maptoart.batch.generate_posters")
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

    @patch("maptoart.batch.time.sleep")
    @patch("maptoart.batch.generate_posters")
    def test_no_retry_on_permanent_error(
        self, mock_gen: MagicMock, mock_sleep: MagicMock, tmp_path: Path,
    ) -> None:
        mock_gen.side_effect = ValueError("bad input")
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\n")
        result = run_batch(csv)
        assert len(result["failures"]) == 1
        mock_sleep.assert_not_called()

    @patch("maptoart.batch.time.sleep")
    @patch("maptoart.batch.generate_posters")
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

    @patch("maptoart.batch.generate_posters")
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

    @patch("maptoart.batch.generate_posters", return_value=["/tmp/out.png"])
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

    @patch("maptoart.batch.generate_posters", return_value=["/tmp/out.png"])
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


class TestBatchMalformedJson:
    """Malformed JSON batch file gives a friendly ValueError."""

    def test_invalid_json_raises_value_error(self, tmp_path: Path) -> None:
        jf = tmp_path / "bad.json"
        jf.write_text("{invalid json content")
        with pytest.raises(ValueError, match="invalid JSON"):
            load_batch_file(jf)


class TestBatchOuterExceptionHandler:
    """Test batch outer except catches PosterGenerationOptions errors (#R18-5)."""

    @patch("maptoart.batch.generate_posters")
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


class TestBatchDryRunMessage:
    """Batch dry-run completion message includes previewed count."""

    @patch("maptoart.batch.generate_posters")
    def test_dry_run_message_shows_previewed_count(
        self, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\nTokyo,Japan\n")
        result = run_batch(csv, dry_run=True)
        assert result["dry_run_count"] == 2
        captured = capsys.readouterr()
        assert "2 previewed" in captured.out
        mock_gen.assert_not_called()

    @patch("maptoart.batch.generate_posters", return_value=["/tmp/out.png"])
    def test_non_dry_run_message_shows_succeeded(
        self, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\n")
        run_batch(csv, dry_run=False)
        captured = capsys.readouterr()
        assert "1 succeeded" in captured.out


class TestPreGeocodeBatch:
    """Tests for Phase 3: _pre_geocode_batch."""

    @patch("maptoart.batch.get_coordinates", return_value=(48.8566, 2.3522))
    def test_geocodes_entries_without_coords(self, mock_geo: MagicMock) -> None:
        entries = [
            {"city": "Paris", "country": "France"},
            {"city": "Tokyo", "country": "Japan"},
        ]
        result = _pre_geocode_batch(entries)
        assert len(result) == 2
        assert result[0] == (48.8566, 2.3522)
        assert mock_geo.call_count == 2

    @patch("maptoart.batch.get_coordinates")
    def test_skips_entries_with_existing_coords(self, mock_geo: MagicMock) -> None:
        entries = [
            {"city": "Paris", "country": "France", "latitude": 48.0, "longitude": 2.0},
        ]
        result = _pre_geocode_batch(entries)
        assert result[0] == (48.0, 2.0)
        mock_geo.assert_not_called()

    @patch("maptoart.batch.get_coordinates", side_effect=ValueError("not found"))
    def test_handles_geocode_failure(self, mock_geo: MagicMock) -> None:
        entries = [{"city": "Nowhere", "country": "Land"}]
        result = _pre_geocode_batch(entries)
        assert len(result) == 0

    def test_skips_entries_with_empty_city(self) -> None:
        entries = [{"city": "", "country": "France"}]
        result = _pre_geocode_batch(entries)
        assert len(result) == 0


class TestPreGeocodeIntegration:
    """Test that run_batch injects pre-geocoded coordinates."""

    @patch("maptoart.batch.generate_posters", return_value=["/tmp/out.png"])
    @patch("maptoart.batch.get_coordinates", return_value=(48.8566, 2.3522))
    def test_coords_injected_into_entries(
        self, mock_geo: MagicMock, mock_gen: MagicMock, tmp_path: Path,
    ) -> None:
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\n")
        result = run_batch(csv)
        assert len(result["successes"]) == 1
        # Verify coordinates were passed via the options
        call_args = mock_gen.call_args[0][0]
        assert call_args.latitude == 48.8566
        assert call_args.longitude == 2.3522


class _FakeFuture:
    """Minimal Future stand-in for parallel batch tests."""

    def __init__(self, return_value: Any = None, exception: Exception | None = None) -> None:
        self._return_value = return_value
        self._exception = exception

    def result(self) -> Any:
        if self._exception:
            raise self._exception
        return self._return_value


class TestParallelBatch:
    """Tests for Phase 4: parallel batch processing."""

    @patch("maptoart.batch.get_coordinates", return_value=(48.8566, 2.3522))
    def test_parallel_dispatches_workers(
        self, mock_geo: MagicMock, tmp_path: Path,
    ) -> None:
        """Verify ProcessPoolExecutor is used and results are collected."""
        f1 = _FakeFuture(return_value=(["/tmp/paris.png"], None))
        f2 = _FakeFuture(return_value=(["/tmp/tokyo.png"], None))

        mock_executor = MagicMock()
        mock_executor.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor.__exit__ = MagicMock(return_value=False)
        mock_executor.submit.side_effect = [f1, f2]

        def fake_as_completed(future_dict):
            return list(future_dict.keys())

        with patch("maptoart.batch.ProcessPoolExecutor", return_value=mock_executor), \
             patch("maptoart.batch.as_completed", side_effect=fake_as_completed):
            csv = tmp_path / "cities.csv"
            csv.write_text("city,country\nParis,France\nTokyo,Japan\n")
            result = run_batch(csv, parallel=True, max_workers=2)

        assert len(result["successes"]) == 2
        assert mock_executor.submit.call_count == 2

    @patch("maptoart.batch.get_coordinates", return_value=(48.8566, 2.3522))
    def test_parallel_handles_worker_failure(
        self, mock_geo: MagicMock, tmp_path: Path,
    ) -> None:
        f1 = _FakeFuture(return_value=(["/tmp/paris.png"], None))
        f2 = _FakeFuture(return_value=([], {"city": "Tokyo", "country": "Japan", "error": "OSM error"}))

        mock_executor = MagicMock()
        mock_executor.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor.__exit__ = MagicMock(return_value=False)
        mock_executor.submit.side_effect = [f1, f2]

        def fake_as_completed(future_dict):
            return list(future_dict.keys())

        with patch("maptoart.batch.ProcessPoolExecutor", return_value=mock_executor), \
             patch("maptoart.batch.as_completed", side_effect=fake_as_completed):
            csv = tmp_path / "cities.csv"
            csv.write_text("city,country\nParis,France\nTokyo,Japan\n")
            result = run_batch(csv, parallel=True, max_workers=2)

        assert len(result["successes"]) == 1
        assert len(result["failures"]) == 1

    @patch("maptoart.batch.generate_posters", return_value=["/tmp/out.png"])
    @patch("maptoart.batch.get_coordinates", return_value=(48.8566, 2.3522))
    def test_parallel_single_entry_falls_back_to_sequential(
        self, mock_geo: MagicMock, mock_gen: MagicMock, tmp_path: Path,
    ) -> None:
        """With only 1 valid entry, parallel=True still uses sequential path."""
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\n")
        result = run_batch(csv, parallel=True)
        assert len(result["successes"]) == 1
        mock_gen.assert_called_once()

    @patch("maptoart.batch.get_coordinates", return_value=(48.8566, 2.3522))
    def test_parallel_handles_unexpected_exception(
        self, mock_geo: MagicMock, tmp_path: Path,
    ) -> None:
        """Verify unexpected exceptions from futures are caught in parallel branch."""
        f1 = _FakeFuture(return_value=(["/tmp/paris.png"], None))
        f2 = _FakeFuture(exception=TypeError("unexpected"))

        mock_executor = MagicMock()
        mock_executor.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor.__exit__ = MagicMock(return_value=False)
        mock_executor.submit.side_effect = [f1, f2]

        def fake_as_completed(future_dict: dict) -> list:
            return list(future_dict.keys())

        with patch("maptoart.batch.ProcessPoolExecutor", return_value=mock_executor), \
             patch("maptoart.batch.as_completed", side_effect=fake_as_completed):
            csv = tmp_path / "cities.csv"
            csv.write_text("city,country\nParis,France\nTokyo,Japan\n")
            result = run_batch(csv, parallel=True, max_workers=2)

        assert len(result["successes"]) == 1
        assert len(result["failures"]) == 1


class TestProcessCityWorker:
    """Tests for _process_city_worker function."""

    @patch("maptoart.batch.generate_posters", return_value=["/tmp/poster.png"])
    def test_successful_generation(self, mock_gen: MagicMock) -> None:
        entry = {"city": "Paris", "country": "France"}
        outputs, failure = _process_city_worker(entry, {})
        assert outputs == ["/tmp/poster.png"]
        assert failure is None
        mock_gen.assert_called_once()

    def test_invalid_options_returns_failure(self) -> None:
        entry = {"city": "", "country": ""}
        outputs, failure = _process_city_worker(entry, {})
        assert outputs == []
        assert failure is not None
        assert "error" in failure

    @patch("maptoart.batch.generate_posters", side_effect=RuntimeError("OSM error"))
    def test_generation_error_returns_failure(self, mock_gen: MagicMock) -> None:
        entry = {"city": "Paris", "country": "France"}
        outputs, failure = _process_city_worker(entry, {})
        assert outputs == []
        assert failure is not None
        assert "OSM error" in failure["error"]

    @patch("maptoart.batch.time.sleep")
    @patch("maptoart.batch.generate_posters")
    def test_transient_error_retries(self, mock_gen: MagicMock, mock_sleep: MagicMock) -> None:
        """Verify transient errors trigger retry logic."""
        mock_gen.side_effect = [
            ConnectionError("network timeout"),
            ["/tmp/poster.png"],
        ]
        entry = {"city": "Paris", "country": "France"}
        outputs, failure = _process_city_worker(entry, {})
        assert outputs == ["/tmp/poster.png"]
        assert failure is None
        assert mock_gen.call_count == 2
        mock_sleep.assert_called_once()


class TestMaxWorkersValidation:
    """Tests for max_workers validation in run_batch."""

    def test_max_workers_zero_rejected(self, tmp_path: Path) -> None:
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\n")
        with pytest.raises(ValueError, match="max_workers must be at least 1"):
            run_batch(csv, max_workers=0)

    def test_max_workers_negative_rejected(self, tmp_path: Path) -> None:
        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\nParis,France\n")
        with pytest.raises(ValueError, match="max_workers must be at least 1"):
            run_batch(csv, max_workers=-1)
