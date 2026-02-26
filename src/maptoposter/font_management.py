"""
Font Management Module
Handles font loading, Google Fonts integration, and caching.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

_logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_FONTS_DIR = PACKAGE_DIR / "fonts"
DEFAULT_CACHE_DIR = Path(os.environ.get("MAPTOPOSTER_FONTS_CACHE", Path.home() / ".cache" / "maptoposter" / "fonts"))

FONTS_DIR = Path(os.environ.get("MAPTOPOSTER_FONTS_DIR", str(DEFAULT_FONTS_DIR)))
FONTS_CACHE_DIR = DEFAULT_CACHE_DIR

_RETRYABLE_HTTP_CODES = {429, 500, 502, 503}


class _RetryableHTTPError(Exception):
    """Raised for HTTP status codes that should be retried."""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, _RetryableHTTPError)),
    reraise=True,
)
def _download_font_file(url: str, timeout: int = 10) -> bytes:
    """Download a single font file with automatic retry on transient errors."""
    resp = requests.get(url, timeout=timeout)
    _logger.debug("Font download %s → HTTP %d", url, resp.status_code)
    if resp.status_code in _RETRYABLE_HTTP_CODES:
        _logger.warning("Retryable HTTP %d from %s", resp.status_code, url)
        raise _RetryableHTTPError(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    return resp.content


def download_google_font(font_family: str, weights: list[int] | None = None) -> dict[str, str] | None:
    """
    Download a font family from Google Fonts and cache it locally.
    Returns dict with font paths for different weights, or None if download fails.

    :param font_family: Google Fonts family name (e.g., 'Noto Sans JP', 'Open Sans')
    :param weights: List of font weights to download (300=light, 400=regular, 700=bold)
    :return: Dict with 'light', 'regular', 'bold' keys mapping to font file paths
    """
    if weights is None:
        weights = [300, 400, 700]

    # Create fonts cache directory
    FONTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Normalize font family name for file paths
    font_name_safe = font_family.replace(" ", "_").lower()

    font_files = {}

    try:
        # Google Fonts API endpoint - request all weights at once
        weights_str = ";".join(map(str, weights))
        api_url = "https://fonts.googleapis.com/css2"

        # Use requests library for cleaner HTTP handling
        params = {"family": f"{font_family}:wght@{weights_str}"}
        headers = {
            "User-Agent": "Mozilla/5.0"  # Get .woff2 files (better compression)
        }

        # Fetch CSS file
        response = requests.get(api_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        css_content = response.text

        # Parse CSS to extract weight-specific URLs
        # Google Fonts CSS has @font-face blocks with font-weight and src: url()
        weight_url_map = {}

        # Split CSS into font-face blocks
        font_face_blocks = re.split(r"@font-face\s*\{", css_content)

        for block in font_face_blocks[1:]:  # Skip first empty split
            # Extract font-weight
            weight_match = re.search(r"font-weight:\s*(\d+)", block)
            if not weight_match:
                continue

            weight = int(weight_match.group(1))

            # Extract URL (prefer woff2, fallback to ttf)
            url_match = re.search(r"url\((https://[^)]+\.(woff2|ttf))\)", block)
            if url_match:
                weight_url_map[weight] = url_match.group(1)

        # Map weights to our keys
        weight_map = {300: "light", 400: "regular", 700: "bold"}

        # Download each weight
        for weight in weights:
            weight_key = weight_map.get(weight, "regular")

            # Find URL for this weight
            weight_url = weight_url_map.get(weight)

            # If exact weight not found, try to find closest
            if not weight_url and weight_url_map:
                # Find closest weight
                closest_weight = min(
                    weight_url_map.keys(), key=lambda x: abs(x - weight)
                )
                weight_url = weight_url_map[closest_weight]
                _logger.info(
                    "Using weight %d for %s (requested %d not available)",
                    closest_weight, weight_key, weight,
                )

            if weight_url:
                # Determine file extension
                file_ext = "woff2" if weight_url.endswith(".woff2") else "ttf"

                # Download font file
                font_filename = f"{font_name_safe}_{weight_key}.{file_ext}"
                font_path = FONTS_CACHE_DIR / font_filename

                if not font_path.exists():
                    _logger.info("Downloading %s %s (%d)...", font_family, weight_key, weight)
                    try:
                        content = _download_font_file(weight_url)
                        font_path.write_bytes(content)
                    except (requests.ConnectionError, requests.Timeout, _RetryableHTTPError) as e:
                        _logger.warning(
                            "Failed to download %s after retries: %s",
                            weight_key, e,
                        )
                        continue
                    except requests.HTTPError as e:
                        _logger.warning("Failed to download %s: %s", weight_key, e)
                        continue
                    except OSError as e:
                        _logger.warning("Failed to write font file %s: %s", weight_key, e)
                        continue
                else:
                    _logger.debug("Using cached %s %s", font_family, weight_key)

                font_files[weight_key] = str(font_path)

        # Ensure we have at least regular weight
        if "regular" not in font_files and font_files:
            # Use first available as regular
            font_files["regular"] = list(font_files.values())[0]
            _logger.info("Using %s weight as regular", list(font_files.keys())[0])

        # If we don't have all three weights, duplicate available ones
        if "bold" not in font_files and "regular" in font_files:
            font_files["bold"] = font_files["regular"]
            _logger.warning("Bold weight not available for '%s'; using regular as substitute", font_family)
        if "light" not in font_files and "regular" in font_files:
            font_files["light"] = font_files["regular"]
            _logger.warning("Light weight not available for '%s'; using regular as substitute", font_family)

        return font_files if font_files else None

    except (requests.ConnectionError, requests.Timeout) as e:
        _logger.warning(
            "Network error downloading Google Font '%s': %s. Check your internet connection.",
            font_family, e,
        )
        return None
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            _logger.warning("Font family '%s' not found on Google Fonts.", font_family)
        else:
            _logger.warning("HTTP error downloading Google Font '%s': %s", font_family, e)
        return None
    except (requests.RequestException, OSError, json.JSONDecodeError) as e:
        _logger.warning("Error downloading Google Font '%s': %s", font_family, e)
        return None


def load_fonts(font_family: str | None = None) -> dict[str, str] | None:
    """
    Load fonts from local directory or download from Google Fonts.
    Returns dict with font paths for different weights.

    :param font_family: Google Fonts family name (e.g., 'Noto Sans JP', 'Open Sans').
                       If None, uses local Roboto fonts.
    :return: Dict with 'bold', 'regular', 'light' keys mapping to font file paths,
             or None if all loading methods fail
    """
    # If custom font family specified, try to download from Google Fonts
    if font_family and font_family.lower() != "roboto":
        _logger.info("Loading Google Font: %s", font_family)
        fonts = download_google_font(font_family)
        if fonts:
            _logger.info("Font '%s' loaded successfully", font_family)
            return fonts

        _logger.warning("Failed to load '%s', falling back to local Roboto", font_family)

    # Default: Load local Roboto fonts
    fonts = {
        "bold": str(FONTS_DIR / "Roboto-Bold.ttf"),
        "regular": str(FONTS_DIR / "Roboto-Regular.ttf"),
        "light": str(FONTS_DIR / "Roboto-Light.ttf"),
    }

    # Verify fonts exist
    for _weight, path in fonts.items():
        if not Path(path).exists():
            _logger.warning("Font not found: %s", path)
            return None

    return fonts


def get_active_fonts(font_family: str | None = None) -> dict[str, Any]:
    """Return info about which fonts would be active for a given config.

    Args:
        font_family: Google Fonts family name, or None for bundled Roboto.

    Returns:
        Dict with the following keys:

        - ``source`` (str): One of ``"bundled"``, ``"google"``, or
          ``"monospace_fallback"``.
        - ``family`` (str): Human-readable family name (e.g. ``"Roboto"``).
        - ``paths`` (dict[str, str]): Weight name to file path mapping.
          Keys are ``"light"``, ``"regular"``, ``"bold"`` when available;
          empty dict for monospace fallback.
        - ``available`` (bool): Whether usable font files were found.
    """
    if font_family and font_family.lower() != "roboto":
        # Check Google Fonts cache for existing files
        font_name_safe = font_family.replace(" ", "_").lower()
        cached_paths: dict[str, str] = {}
        for weight_key in ("light", "regular", "bold"):
            for ext in ("woff2", "ttf"):
                candidate = FONTS_CACHE_DIR / f"{font_name_safe}_{weight_key}.{ext}"
                if candidate.exists():
                    cached_paths[weight_key] = str(candidate)
                    break
        if cached_paths:
            return {
                "source": "google",
                "family": font_family,
                "paths": cached_paths,
                "available": True,
            }

    # Check bundled Roboto
    bundled: dict[str, str] = {
        "bold": str(FONTS_DIR / "Roboto-Bold.ttf"),
        "regular": str(FONTS_DIR / "Roboto-Regular.ttf"),
        "light": str(FONTS_DIR / "Roboto-Light.ttf"),
    }
    if all(Path(p).exists() for p in bundled.values()):
        return {
            "source": "bundled",
            "family": "Roboto",
            "paths": bundled,
            "available": True,
        }

    return {
        "source": "monospace_fallback",
        "family": "monospace",
        "paths": {},
        "available": False,
    }
