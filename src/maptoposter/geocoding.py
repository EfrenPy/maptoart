"""Geocoding logic for the City Map Poster Generator."""

import asyncio
import time
from importlib.metadata import PackageNotFoundError, version

from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim

from ._util import StatusReporter, _emit_status

try:
    _MAPTOPOSTER_VERSION = version("maptoposter")
except PackageNotFoundError:
    _MAPTOPOSTER_VERSION = "0.0.0"


def _validate_coordinate_bounds(lat: float, lon: float) -> None:
    """Raise ValueError if lat/lon fall outside valid ranges."""
    if not -90 <= lat <= 90:
        raise ValueError(f"Latitude must be between -90 and 90, got {lat}")
    if not -180 <= lon <= 180:
        raise ValueError(f"Longitude must be between -180 and 180, got {lon}")


def _resolve_coordinates(
    options,  # PosterGenerationOptions — avoid circular import
    status_reporter: StatusReporter | None,
) -> tuple[float, float]:
    """Determine final (lat, lon) from explicit overrides or geocoding."""
    from .core import get_coordinates  # deferred to avoid circular import

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
):
    """
    Fetches coordinates for a given city and country using geopy.
    Includes rate limiting to be respectful to the geocoding service.
    """
    import logging

    from .core import CacheError, cache_get, cache_set

    _logger = logging.getLogger(__name__)

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

    # Add a small delay to respect Nominatim's usage policy
    time.sleep(1)

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            location = geolocator.geocode(f"{city}, {country}")
            break
        except (GeocoderTimedOut, GeocoderUnavailable) as e:
            if attempt < max_retries:
                backoff = 2 ** attempt  # 1s, 2s
                _emit_status(
                    status_reporter,
                    "geocode.retry",
                    f"Geocoder transient error, retrying in {backoff}s...",
                    attempt=attempt + 1,
                )
                time.sleep(backoff)
            else:
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
            cache_set(coords, (location.latitude, location.longitude))
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
