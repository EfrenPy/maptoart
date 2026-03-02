"""Geocoding logic for the City Map Poster Generator."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim
from geopy.location import Location
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from ._util import (
    CacheError,
    StatusReporter,
    _CACHE_TTL_COORDS,
    _emit_status,
    cache_get,
    cache_set,
)

if TYPE_CHECKING:
    from .core import PosterGenerationOptions

_logger = logging.getLogger(__name__)

try:
    _MAPTOART_VERSION = version("maptoart")
except PackageNotFoundError:  # pragma: no cover
    _MAPTOART_VERSION = "0.0.0"

# Nominatim usage policy requires max 1 request/second. Configurable via
# MAPTOART_NOMINATIM_DELAY.
_NOMINATIM_DELAY_DEFAULT = 1.0


def _nominatim_delay() -> float:
    """Return the configured Nominatim rate-limit delay (lazy env-var read)."""
    raw = os.environ.get("MAPTOART_NOMINATIM_DELAY")
    if raw is None:
        return _NOMINATIM_DELAY_DEFAULT
    try:
        val = float(raw)
        if not math.isfinite(val):
            _logger.warning(
                "MAPTOART_NOMINATIM_DELAY=%r is not finite, using default %.1fs",
                raw,
                _NOMINATIM_DELAY_DEFAULT,
            )
            return _NOMINATIM_DELAY_DEFAULT
        if val < 0:
            _logger.warning(
                "MAPTOART_NOMINATIM_DELAY=%r is negative, clamping to 0.0",
                raw,
            )
        return max(0.0, val)
    except ValueError:
        _logger.warning(
            "Invalid MAPTOART_NOMINATIM_DELAY=%r, using default %.1fs",
            raw,
            _NOMINATIM_DELAY_DEFAULT,
        )
        return _NOMINATIM_DELAY_DEFAULT


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type((GeocoderTimedOut, GeocoderUnavailable)),
    reraise=True,
)
def _geocode_with_retry(geolocator: Nominatim, query: str) -> Location | None:
    """Geocode a query with automatic retry on transient errors."""
    return geolocator.geocode(query)


def _validate_coordinate_bounds(lat: float, lon: float) -> None:
    """Raise ValueError if lat/lon fall outside valid ranges."""
    if not -90 <= lat <= 90:
        raise ValueError(f"Latitude must be between -90 and 90, got {lat}")
    if not -180 <= lon <= 180:
        raise ValueError(f"Longitude must be between -180 and 180, got {lon}")


def _resolve_coordinates(
    options: PosterGenerationOptions,
    status_reporter: StatusReporter | None,
) -> tuple[float, float]:
    """Determine final (lat, lon) from explicit overrides or geocoding."""
    has_lat = options.latitude is not None
    has_lon = options.longitude is not None

    if has_lat != has_lon:
        raise ValueError(
            "Both latitude and longitude must be provided together, or neither."
        )

    if has_lat and has_lon:
        assert (
            options.latitude is not None and options.longitude is not None
        )  # narrowing
        result = (options.latitude, options.longitude)
    else:
        result = get_coordinates(
            options.city,
            options.country,
            status_reporter=status_reporter,
        )

    _validate_coordinate_bounds(result[0], result[1])
    return result


def get_coordinates(
    city: str,
    country: str,
    *,
    status_reporter: StatusReporter | None = None,
) -> tuple[float, float]:
    """
    Fetches coordinates for a given city and country using geopy.
    Includes rate limiting to be respectful to the geocoding service.
    """
    coords = f"coords_{city.lower()}_{country.lower()}"
    try:
        cached = cache_get(coords, default_ttl=_CACHE_TTL_COORDS)
    except CacheError as e:
        _logger.warning("Cache read failed for coordinates: %s", e)
        cached = None
    if cached is not None:
        _emit_status(
            status_reporter,
            "geocode.cache_hit",
            f"\u2713 Using cached coordinates for {city}, {country}",
            city=city,
            country=country,
        )
        return cached

    _emit_status(
        status_reporter,
        "geocode.lookup",
        "Looking up coordinates...",
        city=city,
        country=country,
    )
    geolocator = Nominatim(
        user_agent=f"maptoart/{_MAPTOART_VERSION} (https://github.com/EfrenPy/maptoart)",
        timeout=10,
    )

    # Rate-limit to respect Nominatim's usage policy
    time.sleep(_nominatim_delay())

    try:
        location = _geocode_with_retry(geolocator, f"{city}, {country}")
    except (GeocoderTimedOut, GeocoderUnavailable) as e:
        # Tenacity exhausted all retries and reraised the original exception
        raise ValueError(
            f"Geocoding failed for {city}, {country}: {e}. "
            "The geocoding service is not responding."
        ) from e
    except GeocoderServiceError as e:
        raise ValueError(
            f"Geocoding failed for {city}, {country}: {e}. "
            "Check your internet connection and try again."
        ) from e

    # If geocode returned a coroutine in some environments, run it to get the result.
    if asyncio.iscoroutine(location):
        try:
            location = asyncio.run(location)
        except RuntimeError as exc:
            # asyncio.run() fails when a loop is already running; fall back
            # to a new loop to avoid the deprecated get_event_loop() API.
            loop = asyncio.new_event_loop()
            try:
                location = loop.run_until_complete(location)
            except RuntimeError:
                raise RuntimeError(
                    "Geocoder returned a coroutine but no event loop is available. "
                    "Run this script in a synchronous environment."
                ) from exc
            finally:
                loop.close()

    if location:
        addr = getattr(location, "address", None)
        message = (
            f"\u2713 Found: {addr}"
            if addr
            else "\u2713 Found location (address not available)"
        )
        _emit_status(
            status_reporter,
            "geocode.result",
            message,
            city=city,
            country=country,
        )
        _emit_status(
            status_reporter,
            "geocode.success",
            f"\u2713 Coordinates: {location.latitude}, {location.longitude}",
            city=city,
            country=country,
            latitude=location.latitude,
            longitude=location.longitude,
        )
        try:
            cache_set(
                coords, (location.latitude, location.longitude), ttl=_CACHE_TTL_COORDS
            )
        except CacheError as e:
            _logger.warning("Failed to cache coordinates: %s", e)
        return (location.latitude, location.longitude)

    _emit_status(
        status_reporter,
        "geocode.error",
        f"\u2717 Could not find coordinates for {city}, {country}",
        city=city,
        country=country,
    )
    raise ValueError(
        f"Could not find coordinates for {city}, {country}. "
        "Verify the city and country spelling, or use "
        "--latitude and --longitude to specify coordinates directly."
    )
