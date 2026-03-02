"""Core orchestration for the City Map Poster Generator.

Environment variables
---------------------
MAPTOART_THEMES_DIR
    Custom themes directory (overrides bundled themes).
MAPTOPOSTER_THEMES_DIR
    Legacy alias for ``MAPTOART_THEMES_DIR``.
MAPTOART_OUTPUT_DIR
    Default output directory for generated posters.
MAPTOPOSTER_OUTPUT_DIR
    Legacy alias for ``MAPTOART_OUTPUT_DIR``.
MAPTOART_CACHE_DIR
    OSM data cache directory.  Falls back to ``CACHE_DIR`` (legacy) then ``cache/``.
MAPTOART_FONTS_DIR
    Directory for bundled font files (default: package ``fonts/``).
MAPTOART_FONTS_CACHE
    Download cache for Google Fonts files (default: ``~/.cache/maptoart/fonts``).
MAPTOART_NOMINATIM_DELAY
    Rate-limit delay (seconds) before each Nominatim request (default: ``1``).
    Set to ``0`` for private Nominatim instances.

``MAPTOART_THEMES_DIR``, ``MAPTOART_CACHE_DIR`` / ``CACHE_DIR``, and
``MAPTOART_FONTS_DIR`` are read **at import time**.  Changes to these
variables after the package has been imported have no effect.
``MAPTOART_NOMINATIM_DELAY`` is read lazily on each geocoding call.
"""

import difflib
import functools
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
import uuid
import warnings
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence, TypeVar, cast

import matplotlib.pyplot as plt
import osmnx as ox
from geopandas import GeoDataFrame
from networkx import MultiDiGraph

try:
    from osmnx._errors import InsufficientResponseError, ResponseStatusCodeError
except ImportError:  # pragma: no cover — osmnx may move these to a public module
    from osmnx.errors import InsufficientResponseError, ResponseStatusCodeError
from tqdm import tqdm

from .font_management import _get_fonts, load_fonts  # noqa: F401

# Re-exports from _util (backward compat)
from ._util import (  # noqa: F401
    CacheError,
    PermanentFetchError,
    StatusReporter,
    TransientFetchError,
    _emit_status,
    is_latin_script,
    CACHE_DIR,
    _CACHE_VERSION,
    _CACHE_TTL_COORDS,
    _CACHE_TTL_DATA,
    _cache_path,
    _cache_hmac_key,
    _compute_file_hmac,
    cache_get,
    cache_set,
    cache_clear,
    cache_info,
    _atomic_write_text,
)

# Re-exports from geocoding (backward compat)
from .geocoding import (  # noqa: F401
    _resolve_coordinates,
    _validate_coordinate_bounds,
    get_coordinates,
)

# Re-exports from rendering (backward compat)
from .rendering import (  # noqa: F401
    _GRADIENT_HSTACK,
    _GRADIENT_VALS,
    _MAX_MEMORY_BYTES,
    _WARN_MEMORY_BYTES,
    _ZORDER,
    _apply_typography,
    _estimate_memory,
    _render_layers,
    _setup_figure,
    create_gradient_fade,
    get_crop_limits,
    get_edge_colors_by_type,
    get_edge_styles,
    get_edge_widths_by_type,
)

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_THEMES_DIR = PACKAGE_DIR / "themes"
THEMES_DIR = Path(
    os.environ.get("MAPTOART_THEMES_DIR")
    or os.environ.get("MAPTOPOSTER_THEMES_DIR")
    or str(DEFAULT_THEMES_DIR)
)
DEFAULT_POSTERS_DIR = "posters"
OUTPUT_DIR_ENV = "MAPTOART_OUTPUT_DIR"
LEGACY_OUTPUT_DIR_ENV = "MAPTOPOSTER_OUTPUT_DIR"


def _resolve_output_dir(options_output_dir: str | None) -> str:
    return (
        options_output_dir
        or os.environ.get(OUTPUT_DIR_ENV)
        or os.environ.get(LEGACY_OUTPUT_DIR_ENV)
        or DEFAULT_POSTERS_DIR
    )


# Paper sizes in inches (width x height for portrait orientation)
# ISO A-series standard dimensions
PAPER_SIZES = {
    "A0": (33.1, 46.8),
    "A1": (23.4, 33.1),
    "A2": (16.5, 23.4),
    "A3": (11.7, 16.5),
    "A4": (8.3, 11.7),
}
FILE_ENCODING = "utf-8"

MAX_DIMENSION_CUSTOM = 20.0
MAX_DIMENSION_PAPER = 50.0
MAX_VECTOR_DPI = 300
DEFAULT_THEME = "terracotta"

_TERRACOTTA_DEFAULTS: dict[str, str] = {
    "name": "Terracotta",
    "description": "Mediterranean warmth - burnt orange and clay tones on cream",
    "bg": "#F5EDE4",
    "text": "#8B4513",
    "gradient_color": "#F5EDE4",
    "water": "#A8C4C4",
    "parks": "#E8E0D0",
    "road_motorway": "#A0522D",
    "road_primary": "#B8653A",
    "road_secondary": "#C9846A",
    "road_tertiary": "#D9A08A",
    "road_residential": "#E5C4B0",
    "road_default": "#D9A08A",
}

REQUIRED_THEME_KEYS: frozenset[str] = frozenset(_TERRACOTTA_DEFAULTS.keys())

# Thread-safe theme cache: _theme_cache stores loaded theme dicts and is
# guarded by _theme_cache_lock.  The lock is acquired for both reads and
# writes so that concurrent calls to load_theme() in _fetch_map_data's
# thread pool don't race on dict mutation.
_theme_cache: dict[str, dict[str, str]] = {}
_theme_cache_lock = threading.Lock()

_THEME_COLOR_KEYS: frozenset[str] = frozenset(
    {
        "bg",
        "text",
        "gradient_color",
        "water",
        "parks",
        "road_motorway",
        "road_primary",
        "road_secondary",
        "road_tertiary",
        "road_residential",
        "road_default",
    }
)

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_THEME_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_CITY_SLUG_RE = re.compile(r"[^\w\-]")


_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PosterGenerationOptions:
    """Structured configuration for poster rendering."""

    city: str
    country: str
    distance: int = 18000
    width: float = 12.0
    height: float = 16.0
    dpi: int = 300
    output_format: str = "png"
    theme: str = DEFAULT_THEME
    themes: Sequence[str] | None = None
    all_themes: bool = False
    latitude: float | None = None
    longitude: float | None = None
    country_label: str | None = None
    display_city: str | None = None
    display_country: str | None = None
    font_family: str | None = None
    show_attribution: bool = True
    paper_size: str | None = None
    orientation: str = "portrait"
    output_dir: str | None = None
    parallel_themes: bool = False
    max_theme_workers: int = 4

    def __post_init__(self) -> None:
        if not isinstance(self.city, str):
            raise TypeError(f"city must be a string, got {type(self.city).__name__}")
        if not isinstance(self.country, str):
            raise TypeError(
                f"country must be a string, got {type(self.country).__name__}"
            )
        if not self.city or not self.city.strip():
            raise ValueError("city must not be empty")
        if not self.country or not self.country.strip():
            raise ValueError("country must not be empty")
        for _fname, _fval in [
            ("distance", self.distance),
            ("width", self.width),
            ("height", self.height),
        ]:
            if not math.isfinite(_fval):
                raise ValueError(f"{_fname} must be a finite number, got {_fval}")
        if self.distance <= 0:
            raise ValueError(f"distance must be positive, got {self.distance}")
        if self.distance > 100_000:
            raise ValueError(
                f"distance must be \u2264 100000 m (100 km), got {self.distance}"
            )
        if self.width <= 0:
            raise ValueError(f"width must be positive, got {self.width}")
        if self.height <= 0:
            raise ValueError(f"height must be positive, got {self.height}")
        if self.dpi < 72:
            raise ValueError(f"dpi must be at least 72, got {self.dpi}")
        if self.dpi > 2400:
            raise ValueError(f"dpi must not exceed 2400, got {self.dpi}")
        if self.output_format not in {"png", "svg", "pdf"}:
            raise ValueError(
                f"output_format must be one of 'png', 'svg', 'pdf', got '{self.output_format}'"
            )
        if self.orientation not in ("portrait", "landscape"):
            raise ValueError(
                f"orientation must be 'portrait' or 'landscape', got '{self.orientation}'"
            )
        if self.paper_size is not None and self.paper_size not in PAPER_SIZES:
            raise ValueError(
                f"paper_size must be one of {sorted(PAPER_SIZES.keys())} or None, got '{self.paper_size}'"
            )
        if self.max_theme_workers < 1:
            raise ValueError(
                f"max_theme_workers must be at least 1, got {self.max_theme_workers}"
            )


def _apply_paper_size(
    width: float,
    height: float,
    paper_size: str | None,
    orientation: str,
    status_reporter: StatusReporter | None,
) -> tuple[float, float]:
    """Compute final width/height after paper preset + safety clamps."""

    max_dim = MAX_DIMENSION_CUSTOM
    if paper_size:
        preset = PAPER_SIZES.get(paper_size)
        if preset is None:
            raise ValueError(f"Unknown paper size '{paper_size}'")
        base_width, base_height = preset
        if orientation == "landscape":
            new_w, new_h = base_height, base_width
        else:
            new_w, new_h = base_width, base_height
        # Warn if explicit dimensions are being overridden
        defaults = (12.0, 16.0)
        if (width, height) != defaults and (width != new_w or height != new_h):
            _emit_status(
                status_reporter,
                "paper_size.override",
                f"\u26a0 --paper-size {paper_size} overrides explicit --width {width} / --height {height}",
                paper_size=paper_size,
                original_width=width,
                original_height=height,
            )
        width, height = new_w, new_h
        _emit_status(
            status_reporter,
            "paper_size",
            f'✓ Using {paper_size} ({orientation}): {width}" x {height}"',
            paper_size=paper_size,
            orientation=orientation,
        )
        max_dim = MAX_DIMENSION_PAPER

    if width > max_dim:
        _emit_status(
            status_reporter,
            "dimension.adjust",
            f"⚠ Width {width} exceeds the maximum allowed limit of {max_dim}. Enforcing max limit.",
            dimension="width",
            original=width,
            adjusted=max_dim,
        )
        width = max_dim
    if height > max_dim:
        _emit_status(
            status_reporter,
            "dimension.adjust",
            f"⚠ Height {height} exceeds the maximum allowed limit of {max_dim}. Enforcing max limit.",
            dimension="height",
            original=height,
            adjusted=max_dim,
        )
        height = max_dim

    return width, height


def _validate_dpi(dpi: int, status_reporter: StatusReporter | None = None) -> int:
    """Clamp DPI to sane limits and log when adjustments occur."""

    if dpi < 72:
        _emit_status(
            status_reporter,
            "dpi.adjust",
            f"⚠ DPI {dpi} is too low. Setting to minimum 72.",
            original=dpi,
            adjusted=72,
        )
        return 72
    if dpi > 2400:
        _emit_status(
            status_reporter,
            "dpi.warning",
            f"⚠ DPI {dpi} is very high. This may cause memory issues.",
            original=dpi,
        )
    return dpi


def _resolve_theme_names(
    options: PosterGenerationOptions, available: Sequence[str]
) -> list[str]:
    """Determine the list of themes to render for the current run."""

    if not available:
        raise ValueError(f"No themes found in '{THEMES_DIR}'.")

    if options.all_themes:
        return list(available)

    requested = (
        list(options.themes) if options.themes else [options.theme or DEFAULT_THEME]
    )
    for name in requested:
        if not _THEME_NAME_RE.match(name):
            raise ValueError(
                f"Invalid theme name '{name}': only alphanumeric, hyphens, underscores allowed"
            )
    missing = [theme for theme in requested if theme not in available]
    if missing:
        suggestions = []
        for name in missing:
            matches = difflib.get_close_matches(name, available, n=3, cutoff=0.6)
            if matches:
                suggestions.append(
                    f"'{name}' — did you mean: {', '.join(repr(m) for m in matches)}?"
                )
            else:
                suggestions.append(f"'{name}'")
        msg = "Theme(s) not found: " + "; ".join(suggestions)
        if not any("did you mean" in s for s in suggestions):
            msg += f". Available: {', '.join(sorted(available))}"
        raise ValueError(msg)
    return requested


def _load_custom_fonts(
    font_family: str | None,
    status_reporter: StatusReporter | None,
) -> dict[str, str] | None:
    if not font_family:
        return None
    fonts = load_fonts(font_family)
    if not fonts:
        _emit_status(
            status_reporter,
            "fonts.error",
            f"⚠ Failed to load '{font_family}', falling back to Roboto",
            font_family=font_family,
        )
    else:
        _emit_status(
            status_reporter,
            "fonts.loaded",
            f"✓ Font '{font_family}' loaded successfully",
            font_family=font_family,
        )
    return fonts


def generate_output_filename(
    city: str,
    theme_name: str,
    output_format: str,
    output_dir: str,
) -> str:
    """Generate unique output filename with city, theme, and datetime."""

    resolved_dir = Path(output_dir).resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    # Verify the directory is writable before proceeding
    if not os.access(resolved_dir, os.W_OK):
        raise PermissionError(f"Output directory is not writable: {resolved_dir}")
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    city_slug = _CITY_SLUG_RE.sub("_", city.lower()).strip("_")[:50]
    ext = output_format.lower()
    unique = uuid.uuid4().hex[:6]
    filename = f"{city_slug}_{theme_name}_{timestamp}_{unique}.{ext}"
    return str(resolved_dir / filename)


def get_available_themes() -> list[str]:
    """Return available theme names from the configured directory.

    Results are cached based on the directory's modification time to avoid
    repeated filesystem scans.
    """
    if not THEMES_DIR.exists():
        THEMES_DIR.mkdir(parents=True, exist_ok=True)
        return []

    mtime = THEMES_DIR.stat().st_mtime
    return _get_available_themes_cached(mtime)


@functools.lru_cache(maxsize=1)
def _get_available_themes_cached(_mtime: float) -> list[str]:
    """Filesystem scan gated by directory mtime for cache invalidation."""
    return sorted(p.stem for p in THEMES_DIR.glob("*.json"))


def load_theme(
    theme_name: str = "terracotta",
    *,
    status_reporter: StatusReporter | None = None,
) -> dict[str, str]:
    """Load theme from JSON file in themes directory."""

    with _theme_cache_lock:
        cached = _theme_cache.get(theme_name)
        if cached is not None:
            return cached

    theme_file = THEMES_DIR / f"{theme_name}.json"

    if not theme_file.exists():
        _emit_status(
            status_reporter,
            "theme.fallback",
            f"⚠ Theme file '{theme_file}' not found. Using default terracotta theme.",
            theme=theme_name,
        )
        fallback = dict(_TERRACOTTA_DEFAULTS)
        with _theme_cache_lock:
            _theme_cache[theme_name] = fallback
        return fallback

    try:
        with theme_file.open("r", encoding=FILE_ENCODING) as f:
            theme = json.load(f)
    except json.JSONDecodeError as e:
        _emit_status(
            status_reporter,
            "theme.fallback",
            f"⚠ Theme file '{theme_file}' has invalid JSON: {e}. Using default terracotta theme.",
            theme=theme_name,
        )
        fallback = dict(_TERRACOTTA_DEFAULTS)
        with _theme_cache_lock:
            _theme_cache[theme_name] = fallback
        return fallback

    missing = REQUIRED_THEME_KEYS - theme.keys()
    if missing:
        _emit_status(
            status_reporter,
            "theme.validation",
            f"⚠ Theme '{theme_name}' missing keys: {', '.join(sorted(missing))}. Filling from defaults.",
            theme=theme_name,
            missing_keys=sorted(missing),
        )
        for key in missing:
            theme[key] = _TERRACOTTA_DEFAULTS[key]

    for key in _THEME_COLOR_KEYS:
        val = theme.get(key, "")
        if not _HEX_COLOR_RE.match(val):
            raise ValueError(
                f"Theme '{theme_name}': invalid color for '{key}': {val!r}"
            )

    description = theme.get("description")
    msg = f"✓ Loaded theme: {theme.get('name', theme_name)}"
    if description:
        msg += f"\n  {description}"
    _emit_status(
        status_reporter,
        "theme.loaded",
        msg,
        theme=theme_name,
        description=description,
    )

    with _theme_cache_lock:
        existing = _theme_cache.get(theme_name)
        if existing is not None:
            return existing  # another thread already cached it
        cached_copy = dict(theme)
        _theme_cache[theme_name] = cached_copy
    return cached_copy


_T = TypeVar("_T")


def _cached_fetch(
    cache_key: str,
    fetcher: Callable[[], _T],
    name: str,
    *,
    status_reporter: StatusReporter | None = None,
    rate_limit: float = 0.3,
    **event_kwargs: Any,
) -> _T | None:
    """Shared cache-check → download → cache-set → error-handling pattern."""
    try:
        cached = cache_get(cache_key, default_ttl=_CACHE_TTL_DATA)
    except CacheError as e:
        _logger.warning("Cache read failed for %s: %s", name, e)
        cached = None
    if cached is not None:
        _emit_status(
            status_reporter,
            f"{name}.cache_hit",
            f"✓ Using cached {name}",
            **event_kwargs,
        )
        return cast(_T, cached)

    try:
        _emit_status(
            status_reporter,
            f"{name}.download",
            f"Downloading {name}",
            **event_kwargs,
        )
        result = fetcher()
        time.sleep(rate_limit)
        try:
            cache_set(cache_key, result, ttl=_CACHE_TTL_DATA)
        except CacheError as e:
            _logger.warning("Failed to cache %s: %s", name, e)
        _emit_status(
            status_reporter,
            f"{name}.download.complete",
            f"✓ {name.capitalize()} downloaded",
            **event_kwargs,
        )
        return result
    except (ConnectionError, ResponseStatusCodeError) as e:
        _emit_status(
            status_reporter,
            f"{name}.download.error",
            f"OSMnx error while fetching {name}: {e}",
            error_type="transient",
            **event_kwargs,
        )
        return None
    except (InsufficientResponseError, ValueError) as e:
        _emit_status(
            status_reporter,
            f"{name}.download.error",
            f"OSMnx error while fetching {name}: {e}",
            error_type="permanent",
            **event_kwargs,
        )
        return None


def fetch_graph(
    point: tuple[float, float],
    dist: float,
    *,
    status_reporter: StatusReporter | None = None,
) -> MultiDiGraph | None:
    """
    Fetch street network graph from OpenStreetMap.

    Uses caching to avoid redundant downloads. Fetches all network types
    within the specified distance from the center point.

    Args:
        point: (latitude, longitude) tuple for center point
        dist: Distance in meters from center point

    Returns:
        MultiDiGraph of street network, or None if fetch fails
    """
    lat, lon = point
    key = f"graph_{lat}_{lon}_{dist}"
    return _cached_fetch(
        key,
        lambda: ox.graph_from_point(
            point,
            dist=dist,
            dist_type="bbox",
            network_type="all",
            truncate_by_edge=True,
        ),
        "graph",
        status_reporter=status_reporter,
        rate_limit=0.5,
        distance=dist,
    )


def fetch_features(
    point: tuple[float, float],
    dist: float,
    tags: dict[str, Any],
    name: str,
    *,
    status_reporter: StatusReporter | None = None,
) -> GeoDataFrame | None:
    """
    Fetch geographic features (water, parks, etc.) from OpenStreetMap.

    Uses caching to avoid redundant downloads. Fetches features matching
    the specified OSM tags within distance from center point.

    Args:
        point: (latitude, longitude) tuple for center point
        dist: Distance in meters from center point
        tags: Dictionary of OSM tags to filter features
        name: Name for this feature type (for caching and logging)

    Returns:
        GeoDataFrame of features, or None if fetch fails
    """
    lat, lon = point
    tag_parts = sorted(f"{k}={v}" for k, v in tags.items())
    tag_str = "_".join(tag_parts)
    key = f"{name}_{lat}_{lon}_{dist}_{tag_str}"
    return _cached_fetch(
        key,
        lambda: ox.features_from_point(point, tags=tags, dist=dist),
        name,
        status_reporter=status_reporter,
        distance=dist,
    )


def _fetch_map_data(
    point: tuple[float, float],
    dist: float,
    width: float,
    height: float,
    *,
    status_reporter: StatusReporter | None = None,
) -> tuple[MultiDiGraph, GeoDataFrame | None, GeoDataFrame | None, float]:
    """Fetch street network, water and park features in parallel."""
    # Shrink the fetch radius so the map fills the poster's aspect ratio
    # without excessive whitespace.  The divisor converts the full bounding
    # box span to a half-extent suitable for the crop limits calculation.
    _ASPECT_COMPENSATION_DIVISOR = 4
    aspect_factor = min(max(height, width) / min(height, width), 4.0)
    compensated_dist = dist * aspect_factor / _ASPECT_COMPENSATION_DIVISOR

    with tqdm(
        total=3,
        desc="Fetching map data",
        unit="step",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
        disable=status_reporter is not None and status_reporter.json_mode,
    ) as pbar:

        def _do_graph():
            r = fetch_graph(point, compensated_dist, status_reporter=status_reporter)
            return ("graph", r)

        def _do_water():
            r = fetch_features(
                point,
                compensated_dist,
                tags={"natural": ["water", "bay", "strait"], "waterway": "riverbank"},
                name="water",
                status_reporter=status_reporter,
            )
            return ("water", r)

        def _do_parks():
            r = fetch_features(
                point,
                compensated_dist,
                tags={"leisure": "park", "landuse": "grass"},
                name="parks",
                status_reporter=status_reporter,
            )
            return ("parks", r)

        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            task_names = ["graph", "water", "parks"]
            future_to_name = {
                executor.submit(fn): name
                for fn, name in zip([_do_graph, _do_water, _do_parks], task_names)
            }
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    key, val = future.result()
                    results[key] = val
                except Exception as exc:
                    _logger.warning(
                        "Parallel fetch '%s' failed: %s", name, exc, exc_info=True
                    )
                pbar.update(
                    1
                )  # always update from main thread (tqdm is not thread-safe)

    g = results.get("graph")
    water = results.get("water")
    parks = results.get("parks")

    if g is None:
        raise RuntimeError(
            f"Failed to retrieve street network data for point "
            f"({point[0]:.4f}, {point[1]:.4f}), distance {dist}m "
            f"(compensated {compensated_dist:.0f}m)."
        )
    if status_reporter:
        status_reporter.debug_log(
            "Graph fetched",
            nodes=g.number_of_nodes(),
            edges=g.number_of_edges(),
            compensated_dist=compensated_dist,
        )
    if g.number_of_nodes() < 10:
        _emit_status(
            status_reporter,
            "data.sparse_network",
            f"\u26a0 Road network has only {g.number_of_nodes()} nodes. "
            "The area may be remote or have limited data coverage.",
            nodes=g.number_of_nodes(),
        )

    return g, water, parks, compensated_dist


def _save_output(
    fig: Any,
    output_file: str,
    output_format: str,
    theme: dict[str, str],
    width: float,
    height: float,
    dpi: int,
    *,
    status_reporter: StatusReporter | None = None,
) -> None:
    """Save figure to *output_file* atomically."""
    _emit_status(
        status_reporter,
        "poster.save.start",
        f"Saving to {output_file}...",
        output_file=output_file,
    )

    fmt = output_format.lower()
    save_kwargs: dict[str, Any] = dict(
        facecolor=theme["bg"],
        bbox_inches="tight",
        pad_inches=0.05,
    )

    if fmt == "png":
        save_kwargs["dpi"] = dpi
        output_width_px = int(width * dpi)
        output_height_px = int(height * dpi)
        _emit_status(
            status_reporter,
            "poster.save.resolution",
            f"  Output resolution: {output_width_px} x {output_height_px} px ({dpi} DPI)",
        )
    else:
        effective_dpi = min(dpi, MAX_VECTOR_DPI)
        save_kwargs["dpi"] = effective_dpi
        if dpi > MAX_VECTOR_DPI:
            _emit_status(
                status_reporter,
                "poster.save.dpi_cap",
                f"  Using {effective_dpi} DPI for {fmt.upper()} (vector format is resolution-independent, "
                f"high DPI not needed)",
            )

    target = Path(output_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=f".tmp.{fmt}", dir=str(target.parent))
    os.close(tmp_fd)
    try:
        plt.savefig(tmp_path, format=fmt, **save_kwargs)
        Path(tmp_path).replace(target)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    if status_reporter:
        file_size = target.stat().st_size
        status_reporter.debug_log(
            "File saved", output_file=output_file, file_size_bytes=file_size
        )

    _emit_status(
        status_reporter,
        "poster.save.complete",
        f"✓ Done! Poster saved as {output_file}",
        output_file=output_file,
    )


def create_poster(
    city: str,
    country: str,
    point: tuple[float, float],
    dist: int,
    output_file: str,
    output_format: str,
    *,
    theme: dict[str, str],
    width: float = 12,
    height: float = 16,
    dpi: int = 300,
    country_label: str | None = None,
    name_label: str | None = None,
    display_city: str | None = None,
    display_country: str | None = None,
    fonts: dict[str, str] | None = None,
    show_attribution: bool = True,
    status_reporter: StatusReporter | None = None,
    _prefetched_data: tuple[
        MultiDiGraph, GeoDataFrame | None, GeoDataFrame | None, float
    ]
    | None = None,
    _projected_graph: Any | None = None,
) -> None:
    """Generate a complete map poster with roads, water, parks, and typography.

    Creates a high-quality poster by fetching OSM data, rendering map layers,
    applying the current theme, and adding text labels with coordinates.

    Args:
        city: City name for display on poster.
        country: Country name for display on poster.
        point: (latitude, longitude) tuple for map center.
        dist: Map radius in meters.
        output_file: Path where poster will be saved.
        output_format: File format ('png', 'svg', or 'pdf').
        theme: Theme dict with keys from ``REQUIRED_THEME_KEYS``.
        width: Poster width in inches (default: 12).
        height: Poster height in inches (default: 16).
        dpi: Output resolution in dots per inch (default: 300).
        country_label: Deprecated — use ``display_country`` instead.
        name_label: Deprecated — use ``display_city`` instead.
        display_city: Custom display name for city on poster.
        display_country: Custom display name for country on poster.
        fonts: Dict with 'light', 'regular', 'bold' keys mapping to font
            file paths, or None to use bundled/monospace fallback.
        show_attribution: Whether to render the OSM attribution text.
        status_reporter: Optional reporter for progress events.

    Raises:
        ValueError: If city or country is empty.
        RuntimeError: If street network data cannot be retrieved.
    """
    if not city or not city.strip():
        raise ValueError("city must be a non-empty string")
    if not country or not country.strip():
        raise ValueError("country must be a non-empty string")
    if width <= 0:
        raise ValueError(f"width must be positive, got {width}")
    if height <= 0:
        raise ValueError(f"height must be positive, got {height}")

    # Enforce minimum DPI so direct callers get the same guard as generate_posters.
    # Warn rather than silently clamp, so callers notice the change.
    if dpi < 72:
        _emit_status(
            status_reporter,
            "dpi.clamped",
            f"\u26a0 DPI {dpi} is below minimum; clamped to 72.",
            original_dpi=dpi,
            clamped_dpi=72,
        )
    dpi = max(dpi, 72)

    if name_label is not None:
        warnings.warn(
            "name_label is deprecated and will be removed in v0.6.0; use display_city instead",
            DeprecationWarning,
            stacklevel=2,
        )
    if country_label is not None:
        warnings.warn(
            "country_label is deprecated and will be removed in v0.6.0; use display_country instead",
            DeprecationWarning,
            stacklevel=2,
        )
    display_city = display_city or name_label or city
    display_country = display_country or country_label or country

    _emit_status(
        status_reporter,
        "poster.start",
        f"Generating map for {city}, {country}...",
        city=city,
        country=country,
        theme=theme.get("name", theme.get("id", "")),
    )

    # 1. Fetch data (skip if pre-fetched for multi-theme runs)
    if _prefetched_data is not None:
        g, water, parks, compensated_dist = _prefetched_data
    else:
        g, water, parks, compensated_dist = _fetch_map_data(
            point,
            dist,
            width,
            height,
            status_reporter=status_reporter,
        )

    _emit_status(
        status_reporter,
        "poster.data.ready",
        "✓ All data retrieved successfully!",
        city=city,
        country=country,
    )

    # 2. Memory check + auto DPI reduction + Setup figure
    mem = _estimate_memory(width, height, dpi)
    if mem > _MAX_MEMORY_BYTES:
        max_dpi = int(math.sqrt(_MAX_MEMORY_BYTES / (width * height * 4)))
        if max_dpi < 72:
            raise ValueError(
                f"Estimated memory {mem / 1024**3:.1f} GB exceeds 2 GB limit "
                f'even at DPI 72. Reduce dimensions (currently {width}" x {height}").'
            )
        new_mem = _estimate_memory(width, height, max_dpi)
        _emit_status(
            status_reporter,
            "dpi.auto_reduce",
            f"\u26a0 DPI {dpi} would use {mem / 1024**3:.1f} GB. "
            f"Auto-reduced to {max_dpi} DPI ({new_mem / 1024**2:.0f} MB).",
            original_dpi=dpi,
            reduced_dpi=max_dpi,
        )
        dpi = max_dpi
        mem = new_mem
    if mem > _WARN_MEMORY_BYTES:
        _emit_status(
            status_reporter,
            "memory.warning",
            f"\u26a0 Estimated figure memory: {mem / 1024**2:.0f} MB",
            bytes=mem,
        )

    _emit_status(
        status_reporter,
        "poster.render",
        "Rendering map...",
        city=city,
        country=country,
    )
    fig, ax = _setup_figure(width, height, theme)
    try:
        g_proj = (
            _projected_graph if _projected_graph is not None else ox.project_graph(g)
        )

        # 3. Render layers
        _render_layers(
            ax,
            g_proj,
            point,
            fig,
            compensated_dist,
            water,
            parks,
            theme,
            status_reporter=status_reporter,
        )

        # 4. Typography
        _apply_typography(
            fig,
            ax,
            display_city,
            display_country,
            point,
            theme,
            fonts,
            width,
            height,
            show_attribution=show_attribution,
        )

        # 5. Save
        _save_output(
            fig,
            output_file,
            output_format,
            theme,
            width,
            height,
            dpi,
            status_reporter=status_reporter,
        )
    finally:
        plt.close(fig)


def _build_poster_metadata(
    options: PosterGenerationOptions,
    theme_name: str,
    theme: dict[str, str],
    output_file: str,
    coords: tuple[float, float],
    width: float,
    height: float,
    dpi: int,
) -> dict[str, Any]:
    """Build the metadata sidecar dict for a rendered poster."""
    return {
        "city": options.city,
        "country": options.country,
        "display_city": options.display_city or options.city,
        "display_country": options.display_country
        or options.country_label
        or options.country,
        "theme": theme_name,
        "theme_description": theme.get("description"),
        "output_file": output_file,
        "output_format": options.output_format,
        "width_in": width,
        "height_in": height,
        "dpi": dpi,
        "distance_m": options.distance,
        "latitude": coords[0],
        "longitude": coords[1],
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "show_attribution": options.show_attribution,
        "paper_size": options.paper_size,
        "orientation": options.orientation,
        "font_family": options.font_family,
    }


def _write_metadata(output_file: str, metadata: dict[str, Any]) -> str:
    metadata_path = Path(output_file).with_suffix(".json")
    _atomic_write_text(
        metadata_path,
        json.dumps(metadata, indent=2, ensure_ascii=False),
    )
    return str(metadata_path)


def create_poster_from_options(
    options: PosterGenerationOptions,
    theme_name: str,
    *,
    status_reporter: StatusReporter | None = None,
) -> str:
    """High-level API: resolve coordinates + load theme + create poster.

    Returns the output file path.
    """
    reporter = status_reporter or StatusReporter()
    width, height = _apply_paper_size(
        options.width,
        options.height,
        options.paper_size,
        options.orientation,
        reporter,
    )
    dpi = _validate_dpi(options.dpi, reporter)
    custom_fonts = _load_custom_fonts(options.font_family, reporter)
    coords = _resolve_coordinates(options, reporter)
    theme = load_theme(theme_name, status_reporter=reporter)
    output_dir = _resolve_output_dir(options.output_dir)
    output_file = generate_output_filename(
        options.city,
        theme_name,
        options.output_format,
        output_dir,
    )
    create_poster(
        options.city,
        options.country,
        coords,
        options.distance,
        output_file,
        options.output_format,
        theme=theme,
        width=width,
        height=height,
        dpi=dpi,
        display_city=options.display_city,
        display_country=options.display_country or options.country_label,
        fonts=custom_fonts,
        show_attribution=options.show_attribution,
        status_reporter=reporter,
    )
    metadata = _build_poster_metadata(
        options,
        theme_name,
        theme,
        output_file,
        coords,
        width,
        height,
        dpi,
    )
    _write_metadata(output_file, metadata)
    return output_file


def _render_theme_worker(
    city: str,
    country: str,
    coords: tuple[float, float],
    distance: int,
    output_file: str,
    output_format: str,
    theme_name: str,
    width: float,
    height: float,
    dpi: int,
    display_city: str | None,
    display_country: str | None,
    fonts: dict[str, str] | None,
    show_attribution: bool,
    prefetched_data: tuple[
        MultiDiGraph, GeoDataFrame | None, GeoDataFrame | None, float
    ],
    projected_graph: Any,
    options_dict: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Worker function for parallel theme rendering (runs in a subprocess).

    Returns (output_file, metadata_path, metadata_dict).
    Must be a top-level function for pickle serialization.
    """
    theme = load_theme(theme_name)
    create_poster(
        city,
        country,
        coords,
        distance,
        output_file,
        output_format,
        theme=theme,
        width=width,
        height=height,
        dpi=dpi,
        display_city=display_city,
        display_country=display_country,
        fonts=fonts,
        show_attribution=show_attribution,
        _prefetched_data=prefetched_data,
        _projected_graph=projected_graph,
    )
    options = PosterGenerationOptions(**options_dict)
    metadata = _build_poster_metadata(
        options,
        theme_name,
        theme,
        output_file,
        coords,
        width,
        height,
        dpi,
    )
    metadata_path = _write_metadata(output_file, metadata)
    return output_file, metadata_path, metadata


def generate_posters(
    options: PosterGenerationOptions,
    status_reporter: StatusReporter | None = None,
) -> list[str]:
    """Render one or more posters according to the supplied configuration."""

    reporter = status_reporter or StatusReporter()

    width, height = _apply_paper_size(
        options.width,
        options.height,
        options.paper_size,
        options.orientation,
        reporter,
    )
    dpi = _validate_dpi(options.dpi, reporter)
    available_themes = get_available_themes()
    themes_to_generate = _resolve_theme_names(options, available_themes)
    custom_fonts = _load_custom_fonts(options.font_family, reporter)
    if custom_fonts is None and _get_fonts() is None:
        _logger.warning(
            "No custom or bundled fonts available; falling back to monospace"
        )
    coords = _resolve_coordinates(options, reporter)

    # Resolve deprecated fields early so create_poster doesn't emit warnings
    # with a stacklevel pointing at our own code.
    if options.country_label is not None:
        warnings.warn(
            "country_label is deprecated and will be removed in v0.6.0; use display_country instead",
            DeprecationWarning,
            stacklevel=2,
        )
    resolved_display_country = options.display_country or options.country_label

    reporter.debug_log(
        "Resolved config",
        width=width,
        height=height,
        dpi=dpi,
        themes=themes_to_generate,
        coords=list(coords),
    )

    if not reporter.json_mode:
        _emit_status(
            reporter,
            "run.banner",
            "=" * 50 + "\nCity Map Poster Generator\n" + "=" * 50,
        )
    reporter.emit(
        "run.start",
        city=options.city,
        country=options.country,
        themes=themes_to_generate,
        output_format=options.output_format,
    )

    output_dir = _resolve_output_dir(options.output_dir)

    # Hoist data fetching + graph projection so multi-theme runs don't repeat them
    prefetched_data = _fetch_map_data(
        coords,
        options.distance,
        width,
        height,
        status_reporter=reporter,
    )
    g_proj = ox.project_graph(prefetched_data[0])

    outputs: list[str] = []
    failures: list[str] = []

    use_parallel = options.parallel_themes and len(themes_to_generate) > 1

    if use_parallel:
        # Build serialisable options dict for the worker (excludes non-picklable fields)
        options_dict = {
            "city": options.city,
            "country": options.country,
            "distance": options.distance,
            "width": options.width,
            "height": options.height,
            "dpi": options.dpi,
            "output_format": options.output_format,
            "theme": options.theme,
            "show_attribution": options.show_attribution,
            "paper_size": options.paper_size,
            "orientation": options.orientation,
            "display_city": options.display_city,
            "display_country": options.display_country or options.country_label,
            "font_family": options.font_family,
            "output_dir": options.output_dir,
        }
        n_workers = min(
            options.max_theme_workers, os.cpu_count() or 1, len(themes_to_generate)
        )
        theme_output_files = {
            tn: generate_output_filename(
                options.city, tn, options.output_format, output_dir
            )
            for tn in themes_to_generate
        }
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            future_to_theme = {
                executor.submit(
                    _render_theme_worker,
                    options.city,
                    options.country,
                    coords,
                    options.distance,
                    theme_output_files[tn],
                    options.output_format,
                    tn,
                    width,
                    height,
                    dpi,
                    options.display_city,
                    resolved_display_country,
                    custom_fonts,
                    options.show_attribution,
                    prefetched_data,
                    g_proj,
                    options_dict,
                ): tn
                for tn in themes_to_generate
            }
            for future in as_completed(future_to_theme):
                theme_name = future_to_theme[future]
                try:
                    output_file, metadata_path, _meta = future.result()
                    reporter.emit(
                        "poster.metadata",
                        metadata_path=metadata_path,
                        output_file=output_file,
                    )
                    outputs.append(output_file)
                except (RuntimeError, ValueError, OSError) as exc:
                    _logger.warning("Theme '%s' failed: %s", theme_name, exc)
                    failures.append(theme_name)
    else:
        for theme_name in themes_to_generate:
            theme = load_theme(theme_name, status_reporter=reporter)
            output_file = generate_output_filename(
                options.city,
                theme_name,
                options.output_format,
                output_dir,
            )
            try:
                create_poster(
                    options.city,
                    options.country,
                    coords,
                    options.distance,
                    output_file,
                    options.output_format,
                    theme=theme,
                    width=width,
                    height=height,
                    dpi=dpi,
                    display_city=options.display_city,
                    display_country=resolved_display_country,
                    fonts=custom_fonts,
                    show_attribution=options.show_attribution,
                    status_reporter=reporter,
                    _prefetched_data=prefetched_data,
                    _projected_graph=g_proj,
                )
            except (RuntimeError, ValueError, OSError) as exc:
                _logger.warning("Theme '%s' failed: %s", theme_name, exc)
                failures.append(theme_name)
                continue
            metadata = _build_poster_metadata(
                options,
                theme_name,
                theme,
                output_file,
                coords,
                width,
                height,
                dpi,
            )
            metadata_path = _write_metadata(output_file, metadata)
            reporter.emit(
                "poster.metadata",
                metadata_path=metadata_path,
                output_file=output_file,
            )
            outputs.append(output_file)

    if failures:
        reporter.emit(
            "run.partial",
            f"⚠ Failed themes: {', '.join(failures)}",
            failed_themes=failures,
        )

    if not reporter.json_mode:
        _emit_status(
            reporter,
            "run.complete.banner",
            "\n" + "=" * 50 + "\n✓ Poster generation complete!\n" + "=" * 50,
        )
    reporter.emit("run.complete", outputs=outputs)
    return outputs


def print_examples() -> None:
    """Print usage examples."""
    print("""
City Map Poster Generator
=========================

Usage:
  maptoart-cli --city <city> --country <country> [options]

Examples:
  # Iconic grid patterns
  maptoart-cli -c "New York" -C "USA" -t noir -d 12000           # Manhattan grid
  maptoart-cli -c "Barcelona" -C "Spain" -t warm_beige -d 8000   # Eixample district grid

  # Waterfront & canals
  maptoart-cli -c "Venice" -C "Italy" -t blueprint -d 4000       # Canal network
  maptoart-cli -c "Amsterdam" -C "Netherlands" -t ocean -d 6000  # Concentric canals
  maptoart-cli -c "Dubai" -C "UAE" -t midnight_blue -d 15000     # Palm & coastline

  # Radial patterns
  maptoart-cli -c "Paris" -C "France" -t pastel_dream -d 10000   # Haussmann boulevards
  maptoart-cli -c "Moscow" -C "Russia" -t noir -d 12000          # Ring roads

  # Organic old cities
  maptoart-cli -c "Tokyo" -C "Japan" -t japanese_ink -d 15000    # Dense organic streets
  maptoart-cli -c "Marrakech" -C "Morocco" -t terracotta -d 5000 # Medina maze
  maptoart-cli -c "Rome" -C "Italy" -t warm_beige -d 8000        # Ancient street layout

  # Coastal cities
  maptoart-cli -c "San Francisco" -C "USA" -t sunset -d 10000    # Peninsula grid
  maptoart-cli -c "Sydney" -C "Australia" -t ocean -d 12000      # Harbor city
  maptoart-cli -c "Mumbai" -C "India" -t contrast_zones -d 18000 # Coastal peninsula

  # River cities
  maptoart-cli -c "London" -C "UK" -t noir -d 15000              # Thames curves
  maptoart-cli -c "Budapest" -C "Hungary" -t copper_patina -d 8000  # Danube split

  # List themes
  maptoart-cli --list-themes

Options:
  --city, -c        City name (required)
  --country, -C     Country name (required)
  --display-country Override country text displayed on poster
  --theme, -t       Theme name (default: terracotta)
  --all-themes      Generate posters for all themes
  --distance, -d    Map radius in meters (default: 18000)
  --list-themes     List all available themes

Distance guide:
  4000-6000m   Small/dense cities (Venice, Amsterdam old center)
  8000-12000m  Medium cities, focused downtown (Paris, Barcelona)
  15000-20000m Large metros, full city view (Tokyo, Mumbai)

Available themes ship with the package (override via MAPTOART_THEMES_DIR).
Generated posters are saved to 'posters/' directory.
""")


def list_themes() -> None:
    """List all available themes with descriptions."""

    available_themes = get_available_themes()
    if not available_themes:
        print(f"No themes found in '{THEMES_DIR}'.")
        return

    print("\nAvailable Themes:")
    print("-" * 60)
    for theme_name in available_themes:
        theme_path = THEMES_DIR / f"{theme_name}.json"
        try:
            with theme_path.open("r", encoding=FILE_ENCODING) as f:
                theme_data = json.load(f)
                display_name = theme_data.get("name", theme_name)
                description = theme_data.get("description", "")
        except (OSError, json.JSONDecodeError) as exc:
            _logger.warning(
                "Failed to read theme '%s' for listing: %s", theme_name, exc
            )
            display_name = theme_name
            description = ""
        print(f"  {theme_name}")
        print(f"    {display_name}")
        if description:
            print(f"    {description}")
        print()
