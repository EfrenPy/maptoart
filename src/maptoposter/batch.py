"""Batch processing for generating multiple city posters from a CSV or JSON file."""

import csv
import json
import logging
import time
from pathlib import Path
from typing import Any

from ._util import StatusReporter, _emit_status
from .core import PosterGenerationOptions, generate_posters

__all__ = ["load_batch_file", "run_batch"]

_logger = logging.getLogger(__name__)

_MAX_BATCH_SIZE = 1_048_576  # 1 MB file limit
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
    if file_size > _MAX_BATCH_SIZE:
        raise ValueError(
            f"Batch file '{path}' is too large ({file_size} bytes, max {_MAX_BATCH_SIZE})"
        )

    text = path.read_text(encoding="utf-8")
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
    for row in reader:
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            key = key.strip().lower()
            if value is not None:
                value = value.strip()
            if key in ("distance", "dpi") and value:
                try:
                    normalized[key] = int(value)
                except ValueError:
                    _logger.warning("Row %d: field '%s' value %r is not an integer; skipped field", len(entries) + 1, key, value)
            elif key in ("width", "height", "latitude", "longitude") and value:
                try:
                    normalized[key] = float(value)
                except ValueError:
                    _logger.warning("Row %d: field '%s' value %r is not numeric; skipped field", len(entries) + 1, key, value)
            elif value:
                normalized[key] = value
        row_num = len(entries) + 1
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
    data = json.loads(text)

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


def run_batch(
    batch_path: Path | str,
    *,
    global_overrides: dict[str, Any] | None = None,
    status_reporter: StatusReporter | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute a batch of poster generations.

    Args:
        batch_path: Path to CSV or JSON batch file.
        global_overrides: Default options merged under each entry.
        status_reporter: Optional status reporter.
        dry_run: If True, print summary for each entry without generating.

    Returns a dict with total, successes, and failures.
    """
    batch_path = Path(batch_path)
    entries = load_batch_file(batch_path)
    overrides = global_overrides or {}

    _emit_status(
        status_reporter, "batch.start",
        f"Starting batch: {len(entries)} cities from {batch_path.name}",
        total=len(entries),
    )

    successes: list[str] = []
    failures: list[dict[str, Any]] = []

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
            continue

        _emit_status(
            status_reporter, "batch.item.start",
            f"[{i}/{len(entries)}] {city}, {country}",
            index=i, city=city, country=country,
        )

        try:
            # Merge entry with global overrides (entry values take precedence)
            merged = {**overrides, **entry}
            options = PosterGenerationOptions(**merged)

            if dry_run:
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
                except (ValueError, RuntimeError, OSError, ConnectionError, TimeoutError) as exc:
                    last_exc = exc
                    if attempt < _MAX_RETRIES and _is_transient(exc):
                        delay = _RETRY_BACKOFF[attempt]
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
        except (ValueError, RuntimeError, OSError, ConnectionError, TimeoutError) as exc:
            _logger.warning("Batch item %d (%s, %s) failed: %s", i, city, country, exc)
            failures.append({"city": city, "country": country, "error": str(exc)})
            _emit_status(
                status_reporter, "batch.item.error",
                f"[{i}/{len(entries)}] Failed: {city}, {country} — {exc}",
                index=i, city=city, country=country, error=str(exc),
            )

    result = {
        "total": len(entries),
        "successes": successes,
        "failures": failures,
    }

    _emit_status(
        status_reporter, "batch.complete",
        f"Batch complete: {len(successes)} succeeded, {len(failures)} failed",
        **result,
    )

    return result
