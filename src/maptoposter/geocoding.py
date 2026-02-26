"""Geocoding logic for the City Map Poster Generator."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim
from geopy.location import Location
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ._util import CacheError, StatusReporter, _CACHE_TTL_COORDS, _emit_status, cache_get, cache_set

if TYPE_CHECKING:
    from .core import PosterGenerationOptions

_logger = logging.getLogger(__name__)

try:
    _MAPTOPOSTER_VERSION = version("maptoposter")
except PackageNotFoundError:  # pragma: no cover
    _MAPTOPOSTER_VERSION = "0.0.0"

# Nominatim usage policy requires max 1 request/second.  Configurable via
# MAPTOPOSTER_NOMINATIM_DELAY for testing or when using a private instance.
_NOMINATIM_DELAY: float = float(os.environ.get("MAPTOPOSTER_NOMINATIM_DELAY", "1"))


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
    cached = cache_get(coords)
    if cached:
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
        user_agent=f"maptoposter/{_MAPTOPOSTER_VERSION} (https://github.com/EfrenPy/maptoposter)",
        timeout=10,
    )

    # Rate-limit to respect Nominatim's usage policy
    time.sleep(_NOMINATIM_DELAY)

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
            # If an event loop is already running, try using it to complete the coroutine.
            loop = asyncio.get_event_loop()
            if loop.is_running():
                raise RuntimeError(
                    "Geocoder returned a coroutine while an event loop is already running. "
                    "Run this script in a synchronous environment."
                ) from exc
            location = loop.run_until_complete(location)

    if location:
        addr = getattr(location, "address", None)
        message = f"\u2713 Found: {addr}" if addr else "\u2713 Found location (address not available)"
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
            cache_set(coords, (location.latitude, location.longitude), ttl=_CACHE_TTL_COORDS)
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
        "Verify the city and country spelling."
    )
