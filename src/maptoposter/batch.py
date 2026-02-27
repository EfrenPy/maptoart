"""Batch processing for generating multiple city posters from a CSV or JSON file."""

import csv
import json
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ._util import MAX_INPUT_FILE_SIZE, StatusReporter, _emit_status
from .core import PosterGenerationOptions, generate_posters
from .geocoding import get_coordinates

__all__ = ["load_batch_file", "run_batch"]

_logger = logging.getLogger(__name__)
_MAX_RETRIES = 2
_RETRY_BACKOFF = [2, 5]  # seconds


def _is_transient(exc: Exception) -> bool:
    """Classify whether an exception is worth retrying."""
    # FileNotFoundError and PermissionError are OSError subclasses but permanent
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return False
    transient_types = (ConnectionError, TimeoutError, OSError)
    if isinstance(exc, transient_types):
        return True
    msg = str(exc).lower()
    return any(pat in msg for pat in ("timed out", "rate limit", "service unavailable", "connection"))


def load_batch_file(path: Path) -> list[dict[str, Any]]:
    """Load a CSV or JSON batch file.

    Returns a list of option dicts, one per city.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is too large, has invalid format, or is missing required columns.
    """
    if not path.exists():
        raise FileNotFoundError(f"Batch file '{path}' not found")

    file_size = path.stat().st_size
    if file_size > MAX_INPUT_FILE_SIZE:
        raise ValueError(
            f"Batch file '{path}' is too large ({file_size} bytes, max {MAX_INPUT_FILE_SIZE})"
        )

    text = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return _parse_csv(text, path)
    elif suffix == ".json":
        return _parse_json(text, path)
    else:
        raise ValueError(f"Unsupported batch file format '{suffix}'. Use .csv or .json")


def _parse_csv(text: str, path: Path) -> list[dict[str, Any]]:
    """Parse CSV text into a list of option dicts."""
    reader = csv.DictReader(text.strip().splitlines())
    if reader.fieldnames is None:
        raise ValueError(f"Batch CSV '{path}' has no header row")

    fields = {f.strip().lower() for f in reader.fieldnames}
    if "city" not in fields or "country" not in fields:
        raise ValueError(f"Batch CSV '{path}' must have 'city' and 'country' columns")

    entries = []
    for row_num, row in enumerate(reader, start=1):
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            key = key.strip().lower()
            if value is not None:
                value = value.strip()
            if key in ("distance", "dpi") and value:
                try:
                    normalized[key] = int(value)
                except ValueError:
                    _logger.warning("Row %d: field '%s' value %r is not an integer; skipped field", row_num, key, value)
            elif key in ("width", "height", "latitude", "longitude") and value:
                try:
                    normalized[key] = float(value)
                except ValueError:
                    _logger.warning("Row %d: field '%s' value %r is not numeric; skipped field", row_num, key, value)
            elif value:
                normalized[key] = value
        city = normalized.get("city", "")
        country = normalized.get("country", "")
        if not city or not country:
            _logger.warning(
                "Row %d: empty city (%r) or country (%r); row will be skipped at run time",
                row_num, city, country,
            )
        entries.append(normalized)

    return entries


def _parse_json(text: str, path: Path) -> list[dict[str, Any]]:
    """Parse JSON text into a list of option dicts."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Batch JSON '{path}' has invalid JSON: {e}") from e

    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict) and "cities" in data:
        entries = data["cities"]
    else:
        raise ValueError(
            f"Batch JSON '{path}' must be a list or have a 'cities' key"
        )

    if not isinstance(entries, list):
        raise ValueError(f"Batch JSON '{path}': 'cities' must be a list")

    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Batch JSON '{path}': each entry must be a dict")
        if "city" not in entry or "country" not in entry:
            raise ValueError(
                f"Batch JSON '{path}': each entry must have 'city' and 'country'"
            )

    return entries


def _process_city_worker(
    entry: dict[str, Any],
    overrides: dict[str, Any],
) -> tuple[list[str], dict[str, Any] | None]:
    """Worker function for parallel batch processing (runs in a subprocess).

    Returns (output_files, failure_dict_or_None).
    Must be top-level for pickle serialization.
    """
    city = entry.get("city", "").strip()
    country = entry.get("country", "").strip()
    merged = {**overrides, **entry}
    try:
        options = PosterGenerationOptions(**merged)
    except (ValueError, RuntimeError, OSError) as exc:
        return [], {"city": city, "country": country, "error": str(exc)}

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            outputs = generate_posters(options)
            return outputs, None
        except (ValueError, RuntimeError, OSError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES and _is_transient(exc):
                delay = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                time.sleep(delay)
            else:
                break

    return [], {"city": city, "country": country, "error": str(last_exc)}


def _pre_geocode_batch(
    entries: list[dict[str, Any]],
    *,
    status_reporter: StatusReporter | None = None,
) -> dict[int, tuple[float, float]]:
    """Resolve coordinates for all batch entries upfront.

    Skips entries that already have both latitude and longitude.
    Returns a mapping from entry index (0-based) to (lat, lon).
    Respects Nominatim rate limits (sequential, ~1 req/sec).
    """
    coords_map: dict[int, tuple[float, float]] = {}
    for i, entry in enumerate(entries):
        city = entry.get("city", "").strip()
        country = entry.get("country", "").strip()
        if not city or not country:
            continue
        # Skip entries that already have explicit coordinates
        if entry.get("latitude") is not None and entry.get("longitude") is not None:
            coords_map[i] = (float(entry["latitude"]), float(entry["longitude"]))
            continue
        try:
            lat, lon = get_coordinates(city, country, status_reporter=status_reporter)
            coords_map[i] = (lat, lon)
            _emit_status(
                status_reporter, "batch.geocode",
                f"Pre-geocoded {city}, {country}: ({lat:.4f}, {lon:.4f})",
                index=i, city=city, country=country,
            )
        except (ValueError, RuntimeError) as exc:
            _logger.warning("Pre-geocode failed for %s, %s: %s", city, country, exc)
    return coords_map


def run_batch(
    batch_path: Path | str,
    *,
    global_overrides: dict[str, Any] | None = None,
    status_reporter: StatusReporter | None = None,
    dry_run: bool = False,
    parallel: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Execute a batch of poster generations.

    Args:
        batch_path: Path to CSV or JSON batch file.
        global_overrides: Default options merged under each entry.
        status_reporter: Optional status reporter.
        dry_run: If True, print summary for each entry without generating.
        parallel: If True, process cities in parallel using multiprocessing.
        max_workers: Maximum number of parallel workers (default: 4).

    Returns a dict with total, successes, and failures.
    """
    if max_workers < 1:
        raise ValueError(f"max_workers must be at least 1, got {max_workers}")
    batch_path = Path(batch_path)
    entries = load_batch_file(batch_path)
    overrides = global_overrides or {}

    _emit_status(
        status_reporter, "batch.start",
        f"Starting batch: {len(entries)} cities from {batch_path.name}",
        total=len(entries),
    )

    # Pre-geocode all cities so they're independent for parallel processing
    if not dry_run:
        geocoded = _pre_geocode_batch(entries, status_reporter=status_reporter)
        for idx, (lat, lon) in geocoded.items():
            entries[idx].setdefault("latitude", lat)
            entries[idx].setdefault("longitude", lon)

    successes: list[str] = []
    failures: list[dict[str, Any]] = []
    dry_run_count = 0

    # Filter valid entries and collect invalid ones
    valid_entries: list[tuple[int, dict[str, Any]]] = []
    for i, entry in enumerate(entries, 1):
        city = entry.get("city", "").strip()
        country = entry.get("country", "").strip()
        if not city or not country:
            _logger.warning("Batch item %d has empty city or country, skipping", i)
            failures.append({"city": city, "country": country, "error": "empty city or country"})
            _emit_status(
                status_reporter, "batch.item.error",
                f"[{i}/{len(entries)}] Skipped: empty city or country",
                index=i, city=city, country=country,
            )
        else:
            valid_entries.append((i, entry))

    # Parallel batch processing
    if parallel and not dry_run and len(valid_entries) > 1:
        n_workers = min(max_workers, os.cpu_count() or 1, len(valid_entries))
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            future_to_info = {
                executor.submit(_process_city_worker, entry, overrides): (idx, entry)
                for idx, entry in valid_entries
            }
            for future in as_completed(future_to_info):
                idx, entry = future_to_info[future]
                city = entry.get("city", "").strip()
                country = entry.get("country", "").strip()
                try:
                    city_outputs, failure = future.result()
                    if failure:
                        failures.append(failure)
                        _emit_status(
                            status_reporter, "batch.item.error",
                            f"[{idx}/{len(entries)}] Failed: {city}, {country} — {failure['error']}",
                            index=idx, city=city, country=country, error=failure["error"],
                        )
                    else:
                        successes.extend(city_outputs)
                        _emit_status(
                            status_reporter, "batch.item.complete",
                            f"[{idx}/{len(entries)}] Done: {city}, {country}",
                            index=idx, city=city, country=country, outputs=city_outputs,
                        )
                except Exception as exc:
                    _logger.warning("Batch item %d (%s, %s) failed: %s", idx, city, country, exc)
                    failures.append({"city": city, "country": country, "error": str(exc)})
    else:
        # Sequential processing (original path)
        for i, entry in valid_entries:
            city = entry.get("city", "").strip()
            country = entry.get("country", "").strip()

            _emit_status(
                status_reporter, "batch.item.start",
                f"[{i}/{len(entries)}] {city}, {country}",
                index=i, city=city, country=country,
            )

            try:
                merged = {**overrides, **entry}
                options = PosterGenerationOptions(**merged)

                if dry_run:
                    dry_run_count += 1
                    _emit_status(
                        status_reporter, "batch.item.dry_run",
                        f"[{i}/{len(entries)}] Dry run: {city}, {country} "
                        f"(distance={options.distance}, theme={options.theme})",
                        index=i, city=city, country=country,
                    )
                    continue

                last_exc: Exception | None = None
                for attempt in range(_MAX_RETRIES + 1):
                    try:
                        outputs = generate_posters(options, status_reporter=status_reporter)
                        successes.extend(outputs)
                        _emit_status(
                            status_reporter, "batch.item.complete",
                            f"[{i}/{len(entries)}] Done: {city}, {country}",
                            index=i, city=city, country=country, outputs=outputs,
                        )
                        last_exc = None
                        break
                    except (ValueError, RuntimeError, OSError) as exc:
                        last_exc = exc
                        if attempt < _MAX_RETRIES and _is_transient(exc):
                            delay = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                            _emit_status(
                                status_reporter, "batch.item.retry",
                                f"[{i}/{len(entries)}] Transient error, retrying in {delay}s: {exc}",
                                index=i, city=city, country=country, attempt=attempt + 1,
                            )
                            time.sleep(delay)
                        else:
                            break

                if last_exc is not None:
                    _logger.warning("Batch item %d (%s, %s) failed: %s", i, city, country, last_exc)
                    failures.append({"city": city, "country": country, "error": str(last_exc)})
                    _emit_status(
                        status_reporter, "batch.item.error",
                        f"[{i}/{len(entries)}] Failed: {city}, {country} — {last_exc}",
                        index=i, city=city, country=country, error=str(last_exc),
                    )
            except (ValueError, RuntimeError, OSError) as exc:
                _logger.warning("Batch item %d (%s, %s) failed: %s", i, city, country, exc)
                failures.append({"city": city, "country": country, "error": str(exc)})
                _emit_status(
                    status_reporter, "batch.item.error",
                    f"[{i}/{len(entries)}] Failed: {city}, {country} — {exc}",
                    index=i, city=city, country=country, error=str(exc),
                )

    result: dict[str, Any] = {
        "total": len(entries),
        "successes": successes,
        "failures": failures,
        "dry_run_count": dry_run_count,
    }

    parts = []
    if dry_run_count:
        parts.append(f"{dry_run_count} previewed")
    else:
        parts.append(f"{len(successes)} succeeded")
    if failures:
        parts.append(f"{len(failures)} failed")
    _emit_status(
        status_reporter, "batch.complete",
        f"Batch complete: {', '.join(parts)}",
        **result,
    )

    return result
