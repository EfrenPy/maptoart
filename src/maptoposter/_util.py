"""Shared utilities for the maptoposter package (no internal imports)."""

import hashlib
import hmac
import io
import json
import logging
import os
import pickle
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any, ClassVar


class CacheError(Exception):
    """Raised when a cache operation fails."""


class TransientFetchError(Exception):
    """Network/service error that may resolve on retry."""


class PermanentFetchError(Exception):
    """Data error that will not resolve on retry (e.g., no roads in area)."""


class StatusReporter:
    """Lightweight status/event logger with optional JSON output."""

    def __init__(
        self,
        json_mode: bool = False,
        debug: bool = False,
        *,
        on_progress: Callable[[str, str | None, dict[str, Any]], None] | None = None,
    ) -> None:
        self.json_mode = json_mode
        self.debug = debug
        self._on_progress = on_progress

    def emit(self, event: str, message: str | None = None, **extra: Any) -> None:
        """Emit a status event.

        Thread-safety: This method is safe to call from multiple threads
        (e.g. inside ``ThreadPoolExecutor`` workers).  The ``on_progress``
        callback is guarded so that exceptions never propagate to the caller.

        Event names follow dot-separated hierarchy::

            <module>.<action>[.<detail>]

        Examples:
            geocode.lookup, geocode.cache_hit, geocode.success
            graph.download, graph.download.complete, graph.download.error
            poster.start, poster.render, poster.save.complete
            batch.start, batch.item.start, batch.item.error
            run.start, run.complete
        """
        if self._on_progress is not None:
            try:
                self._on_progress(event, message, extra)
            except Exception:
                _logger.warning("on_progress callback failed for event '%s'", event, exc_info=True)
        payload = {
            "event": event,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            **extra,
        }
        if message is not None:
            payload["message"] = message
        if self.json_mode:
            print(json.dumps(payload, ensure_ascii=False))
        elif message is not None:
            print(message)

    def debug_log(self, message: str, **extra: Any) -> None:
        """Emit a debug-level message only when debug mode is active."""
        if not self.debug:
            return
        self.emit("debug", f"[DEBUG] {message}", **extra)


def _emit_status(
    status_reporter: StatusReporter | None,
    event: str,
    message: str | None = None,
    **extra: Any,
) -> None:
    if status_reporter is not None:
        status_reporter.emit(event, message, **extra)
    elif message is not None:
        print(message)


_logger = logging.getLogger(__name__)

CACHE_DIR_PATH = os.environ.get("MAPTOPOSTER_CACHE_DIR", os.environ.get("CACHE_DIR", "cache"))
CACHE_DIR = Path(CACHE_DIR_PATH)
_CACHE_VERSION = "v2"
# Coordinates rarely change and geocoding is rate-limited, so cache for 30 days.
_CACHE_TTL_COORDS = 30 * 24 * 3600   # 30 days
# OSM road/feature data changes more frequently; 7 days balances freshness
# against Overpass API rate limits and download times.
_CACHE_TTL_DATA = 7 * 24 * 3600      # 7 days


_MAX_CACHE_KEY_LEN = 180  # keep total path under filesystem limits


def _cache_path(key: str) -> Path:
    """
    Generate a safe cache file path from a cache key.

    Args:
        key: Cache key identifier

    Returns:
        Path to cache file with .pkl extension
    """
    safe = key.replace(os.sep, "_")
    if len(safe) > _MAX_CACHE_KEY_LEN:
        suffix = hashlib.sha256(safe.encode()).hexdigest()[:16]
        safe = safe[:_MAX_CACHE_KEY_LEN] + "_" + suffix
    return CACHE_DIR / f"{safe}_{_CACHE_VERSION}.pkl"


def _cache_hmac_key() -> bytes:
    """Machine-local HMAC key derived from MAC address."""
    return uuid.getnode().to_bytes(8, "big")


def _compute_file_hmac(path_or_data: str | Path | bytes) -> str:
    """Compute HMAC-SHA256 hex digest for a file or raw bytes."""
    h = hmac.new(_cache_hmac_key(), digestmod=hashlib.sha256)
    if isinstance(path_or_data, bytes):
        h.update(path_or_data)
    else:
        with open(path_or_data, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    return h.hexdigest()


class _RestrictedUnpickler(pickle.Unpickler):
    """Only allow safe types from our cache files."""

    _ALLOWED_MODULES: ClassVar[frozenset[str]] = frozenset({
        "builtins", "collections", "datetime", "numpy", "numpy.core",
        "numpy.core.multiarray", "pandas", "pandas.core", "geopandas",
        "shapely", "networkx", "pyproj", "numpy.core.numeric",
    })

    def find_class(self, module: str, name: str) -> type:
        top = module.split(".")[0]
        if top in self._ALLOWED_MODULES:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(
            f"Blocked unpickling of {module}.{name}"
        )


def cache_get(key: str, *, default_ttl: int | None = None) -> Any:
    """
    Retrieve a cached object by key.

    Args:
        key: Cache key identifier
        default_ttl: Default TTL in seconds if not specified in metadata

    Returns:
        Cached object if found and not expired, None otherwise

    Raises:
        CacheError: If cache read operation fails
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(key)
        if not path.exists():
            return None
        sig_path = Path(f"{path}.sig")
        if sig_path.exists():
            expected = sig_path.read_text(encoding="utf-8").strip()
        else:
            _logger.warning("Cache signature missing for '%s', treating as miss", key)
            return None
        # Read file once for both HMAC verification and deserialization
        data = path.read_bytes()
        actual = _compute_file_hmac(data)
        if not hmac.compare_digest(expected, actual):
            _logger.warning("Cache HMAC mismatch for '%s', treating as miss", key)
            return None
        # Check TTL from metadata sidecar
        meta_path = Path(f"{path}.meta")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                ttl = meta.get("ttl") or default_ttl
                if ttl is not None:
                    created = meta.get("created", 0)
                    if time.time() - created > ttl:
                        _logger.info("Cache entry '%s' expired (TTL=%ds)", key, ttl)
                        return None
            except (json.JSONDecodeError, KeyError):
                _logger.warning("Corrupt metadata for '%s', ignoring TTL", key)
        elif default_ttl is not None:
            # No metadata but TTL requested — can't verify age, treat as miss
            return None
        return _RestrictedUnpickler(io.BytesIO(data)).load()
    except CacheError:
        raise
    except (OSError, pickle.UnpicklingError, json.JSONDecodeError, ValueError) as e:
        raise CacheError(f"Cache read failed: {e}") from e


def cache_set(key: str, value: Any, *, ttl: int | None = None) -> None:
    """
    Store an object in the cache.

    Args:
        key: Cache key identifier
        value: Object to cache (must be picklable)
        ttl: Time-to-live in seconds (None = never expires)

    Raises:
        CacheError: If cache write operation fails
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(key)
        with path.open("wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
        sig = _compute_file_hmac(path)
        Path(f"{path}.sig").write_text(sig, encoding="utf-8")
        # Write metadata sidecar
        meta = {
            "created": time.time(),
            "ttl": ttl,
            "cache_version": _CACHE_VERSION,
        }
        Path(f"{path}.meta").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8",
        )
    except (OSError, pickle.PicklingError, ValueError) as e:
        raise CacheError(f"Cache write failed: {e}") from e


def cache_clear() -> int:
    """Remove all cache files. Returns count of files removed."""
    if not CACHE_DIR.exists():
        return 0
    count = 0
    for pattern in ("*.pkl", "*.sig", "*.meta"):
        for f in CACHE_DIR.glob(pattern):
            f.unlink(missing_ok=True)
            count += 1
    return count


def cache_info() -> dict[str, Any]:
    """Return cache statistics.

    Returns:
        Dict with total_files, total_bytes, and entries list.
    """
    if not CACHE_DIR.exists():
        return {"total_files": 0, "total_bytes": 0, "entries": []}
    entries = []
    total_bytes = 0
    for f in sorted(CACHE_DIR.glob("*.pkl")):
        size = f.stat().st_size
        total_bytes += size
        meta_path = Path(f"{f}.meta")
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        entries.append({
            "key": f.stem,
            "size_bytes": size,
            "created": meta.get("created"),
            "ttl": meta.get("ttl"),
        })
    return {"total_files": len(entries), "total_bytes": total_bytes, "entries": entries}
