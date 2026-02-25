"""Core orchestration for the City Map Poster Generator."""

import hashlib
import hmac
import json
import logging
import os
import pickle
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Sequence, cast

import matplotlib.pyplot as plt
import osmnx as ox
from geopandas import GeoDataFrame
from networkx import MultiDiGraph
from osmnx._errors import InsufficientResponseError, ResponseStatusCodeError
from tqdm import tqdm

from .font_management import load_fonts

# Re-exports from _util (backward compat)
from ._util import CacheError, StatusReporter, _emit_status  # noqa: F401

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
    get_edge_widths_by_type,
)

try:
    _MAPTOPOSTER_VERSION = version("maptoposter")
except PackageNotFoundError:
    _MAPTOPOSTER_VERSION = "0.0.0"


CACHE_DIR_PATH = os.environ.get("MAPTOPOSTER_CACHE_DIR", os.environ.get("CACHE_DIR", "cache"))
CACHE_DIR = Path(CACHE_DIR_PATH)
_CACHE_VERSION = "v2"

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_THEMES_DIR = PACKAGE_DIR / "themes"
THEMES_DIR = Path(os.environ.get("MAPTOPOSTER_THEMES_DIR", str(DEFAULT_THEMES_DIR)))
DEFAULT_POSTERS_DIR = "posters"
OUTPUT_DIR_ENV = "MAPTOPOSTER_OUTPUT_DIR"

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

_theme_cache: dict[str, dict[str, str]] = {}
_theme_cache_lock = threading.Lock()

_THEME_COLOR_KEYS: frozenset[str] = frozenset({
    "bg", "text", "gradient_color", "water", "parks",
    "road_motorway", "road_primary", "road_secondary",
    "road_tertiary", "road_residential", "road_default",
})

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_THEME_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class _Sentinel:
    """Marker for unloaded state."""


_UNLOADED = _Sentinel()
_FONTS: dict[str, str] | None | _Sentinel = _UNLOADED


def _get_fonts() -> dict[str, str] | None:
    """Lazy-load bundled fonts on first access."""
    global _FONTS
    if isinstance(_FONTS, _Sentinel):
        _FONTS = load_fonts()
    return _FONTS


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

    def __post_init__(self) -> None:
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
        if self.output_format not in {"png", "svg", "pdf"}:
            raise ValueError(
                f"output_format must be one of 'png', 'svg', 'pdf', got '{self.output_format}'"
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
            width, height = base_height, base_width
        else:
            width, height = base_width, base_height
        _emit_status(
            status_reporter,
            "paper_size",
            f"✓ Using {paper_size} ({orientation}): {width}\" x {height}\"",
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


def _resolve_theme_names(options: PosterGenerationOptions, available: Sequence[str]) -> list[str]:
    """Determine the list of themes to render for the current run."""

    if not available:
        raise ValueError(f"No themes found in '{THEMES_DIR}'.")

    if options.all_themes:
        return list(available)

    requested = list(options.themes) if options.themes else [options.theme or DEFAULT_THEME]
    for name in requested:
        if not _THEME_NAME_RE.match(name):
            raise ValueError(
                f"Invalid theme name '{name}': only alphanumeric, hyphens, underscores allowed"
            )
    missing = [theme for theme in requested if theme not in available]
    if missing:
        raise ValueError(f"Theme(s) not found: {', '.join(missing)}. Available themes: {', '.join(available)}")
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


def _cache_path(key: str) -> str:
    """
    Generate a safe cache file path from a cache key.

    Args:
        key: Cache key identifier

    Returns:
        Path to cache file with .pkl extension
    """
    safe = key.replace(os.sep, "_")
    return os.path.join(CACHE_DIR, f"{safe}_{_CACHE_VERSION}.pkl")


def _cache_hmac_key() -> bytes:
    """Machine-local HMAC key derived from MAC address."""
    return uuid.getnode().to_bytes(8, "big")


def _compute_file_hmac(path: str) -> str:
    """Compute HMAC-SHA256 hex digest for a file."""
    h = hmac.new(_cache_hmac_key(), digestmod=hashlib.sha256)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_get(key: str):
    """
    Retrieve a cached object by key.

    Args:
        key: Cache key identifier

    Returns:
        Cached object if found, None otherwise

    Raises:
        CacheError: If cache read operation fails
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(key)
        if not os.path.exists(path):
            return None
        sig_path = f"{path}.sig"
        if os.path.exists(sig_path):
            expected = Path(sig_path).read_text(encoding="utf-8").strip()
            actual = _compute_file_hmac(path)
            if not hmac.compare_digest(expected, actual):
                _logger.warning("Cache HMAC mismatch for '%s', treating as miss", key)
                return None
        else:
            _logger.warning("Cache signature missing for '%s', treating as miss", key)
            return None
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        raise CacheError(f"Cache read failed: {e}") from e


def cache_set(key: str, value):
    """
    Store an object in the cache.

    Args:
        key: Cache key identifier
        value: Object to cache (must be picklable)

    Raises:
        CacheError: If cache write operation fails
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(key)
        with open(path, "wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
        sig = _compute_file_hmac(path)
        Path(f"{path}.sig").write_text(sig, encoding="utf-8")
    except Exception as e:
        raise CacheError(f"Cache write failed: {e}") from e


# Font loading now handled by font_management.py module


def is_latin_script(text):
    """
    Check if text is primarily Latin script.
    Used to determine if letter-spacing should be applied to city names.

    :param text: Text to analyze
    :return: True if text is primarily Latin script, False otherwise
    """
    if not text:
        return True

    latin_count = 0
    total_alpha = 0

    for char in text:
        if char.isalpha():
            total_alpha += 1
            # Latin Unicode ranges:
            # - Basic Latin: U+0000 to U+007F
            # - Latin-1 Supplement: U+0080 to U+00FF
            # - Latin Extended-A: U+0100 to U+017F
            # - Latin Extended-B: U+0180 to U+024F
            if ord(char) < 0x250:
                latin_count += 1

    # If no alphabetic characters, default to Latin (numbers, symbols, etc.)
    if total_alpha == 0:
        return True

    # Consider it Latin if >80% of alphabetic characters are Latin
    return (latin_count / total_alpha) > 0.8


def generate_output_filename(
    city: str,
    theme_name: str,
    output_format: str,
    output_dir: str,
) -> str:
    """Generate unique output filename with city, theme, and datetime."""

    resolved_dir = Path(output_dir).resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_slug = re.sub(r"[^\w\-]", "_", city.lower()).strip("_")
    ext = output_format.lower()
    filename = f"{city_slug}_{theme_name}_{timestamp}.{ext}"
    return str(resolved_dir / filename)


def get_available_themes() -> list[str]:
    """Return available theme names from the configured directory."""

    if not THEMES_DIR.exists():
        THEMES_DIR.mkdir(parents=True, exist_ok=True)
        return []

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
            return dict(cached)

    theme_file = THEMES_DIR / f"{theme_name}.json"

    if not theme_file.exists():
        _emit_status(
            status_reporter,
            "theme.fallback",
            f"⚠ Theme file '{theme_file}' not found. Using default terracotta theme.",
            theme=theme_name,
        )
        return dict(_TERRACOTTA_DEFAULTS)

    with theme_file.open("r", encoding=FILE_ENCODING) as f:
        theme = json.load(f)

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
            raise ValueError(f"Theme '{theme_name}': invalid color for '{key}': {val!r}")

    description = theme.get("description")
    _emit_status(
        status_reporter,
        "theme.loaded",
        f"✓ Loaded theme: {theme.get('name', theme_name)}",
        theme=theme_name,
        description=description,
    )
    if not status_reporter and description:
        print(f"  {description}")

    with _theme_cache_lock:
        _theme_cache[theme_name] = dict(theme)
    return theme


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
    graph = f"graph_{lat}_{lon}_{dist}"
    cached = cache_get(graph)
    if cached is not None:
        _emit_status(
            status_reporter,
            "graph.cache_hit",
            "✓ Using cached street network",
            distance=dist,
        )
        return cast(MultiDiGraph, cached)

    try:
        _emit_status(
            status_reporter,
            "graph.download",
            "Downloading street network",
            distance=dist,
        )
        g = ox.graph_from_point(point, dist=dist, dist_type='bbox', network_type='all', truncate_by_edge=True)
        # Rate limit between requests
        time.sleep(0.5)
        try:
            cache_set(graph, g)
        except CacheError as e:
            _logger.warning("Failed to cache graph: %s", e)
        _emit_status(
            status_reporter,
            "graph.download.complete",
            "✓ Street network downloaded",
            distance=dist,
        )
        return g
    except (InsufficientResponseError, ResponseStatusCodeError, ValueError, ConnectionError) as e:
        _emit_status(
            status_reporter,
            "graph.download.error",
            f"OSMnx error while fetching graph: {e}",
            distance=dist,
        )
        return None


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
    tag_str = "_".join(tags.keys())
    features = f"{name}_{lat}_{lon}_{dist}_{tag_str}"
    cached = cache_get(features)
    if cached is not None:
        _emit_status(
            status_reporter,
            f"{name}.cache_hit",
            f"✓ Using cached {name}",
            distance=dist,
        )
        return cast(GeoDataFrame, cached)

    try:
        _emit_status(
            status_reporter,
            f"{name}.download",
            f"Downloading {name}",
            distance=dist,
        )
        data = ox.features_from_point(point, tags=tags, dist=dist)
        # Rate limit between requests
        time.sleep(0.3)
        try:
            cache_set(features, data)
        except CacheError as e:
            _logger.warning("Failed to cache %s: %s", name, e)
        _emit_status(
            status_reporter,
            f"{name}.download.complete",
            f"✓ {name.capitalize()} downloaded",
            distance=dist,
        )
        return data
    except (InsufficientResponseError, ResponseStatusCodeError, ValueError, ConnectionError) as e:
        _emit_status(
            status_reporter,
            f"{name}.download.error",
            f"OSMnx error while fetching {name}: {e}",
            distance=dist,
        )
        return None


def _fetch_map_data(
    point: tuple[float, float],
    dist: float,
    width: float,
    height: float,
    *,
    status_reporter: StatusReporter | None = None,
) -> tuple[MultiDiGraph, GeoDataFrame | None, GeoDataFrame | None, float]:
    """Fetch street network, water and park features with a progress bar."""
    compensated_dist = dist * (max(height, width) / min(height, width)) / 4

    with tqdm(
        total=3,
        desc="Fetching map data",
        unit="step",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
        disable=getattr(status_reporter, "json_mode", False),
    ) as pbar:
        pbar.set_description("Downloading street network")
        g = fetch_graph(point, compensated_dist, status_reporter=status_reporter)
        if g is None:
            raise RuntimeError("Failed to retrieve street network data.")
        if status_reporter:
            status_reporter.debug_log(
                "Graph fetched",
                nodes=g.number_of_nodes(),
                edges=g.number_of_edges(),
                compensated_dist=compensated_dist,
            )
        if g.number_of_nodes() < 10:
            _emit_status(
                status_reporter, "data.sparse_network",
                f"\u26a0 Road network has only {g.number_of_nodes()} nodes. "
                "The area may be remote or have limited data coverage.",
                nodes=g.number_of_nodes(),
            )
        pbar.update(1)

        pbar.set_description("Downloading water features")
        water = fetch_features(
            point,
            compensated_dist,
            tags={"natural": ["water", "bay", "strait"], "waterway": "riverbank"},
            name="water",
            status_reporter=status_reporter,
        )
        pbar.update(1)

        pbar.set_description("Downloading parks/green spaces")
        parks = fetch_features(
            point,
            compensated_dist,
            tags={"leisure": "park", "landuse": "grass"},
            name="parks",
            status_reporter=status_reporter,
        )
        pbar.update(1)

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
        status_reporter, "poster.save.start",
        f"Saving to {output_file}...", output_file=output_file,
    )

    fmt = output_format.lower()
    save_kwargs: dict[str, Any] = dict(
        facecolor=theme["bg"], bbox_inches="tight", pad_inches=0.05,
    )

    if fmt == "png":
        save_kwargs["dpi"] = dpi
        output_width_px = int(width * dpi)
        output_height_px = int(height * dpi)
        _emit_status(
            status_reporter, "poster.save.resolution",
            f"  Output resolution: {output_width_px} x {output_height_px} px ({dpi} DPI)",
        )
    else:
        MAX_VECTOR_DPI = 300
        effective_dpi = min(dpi, MAX_VECTOR_DPI)
        save_kwargs["dpi"] = effective_dpi
        if dpi > MAX_VECTOR_DPI:
            _emit_status(
                status_reporter, "poster.save.dpi_cap",
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

    plt.close(fig)

    if status_reporter:
        file_size = target.stat().st_size
        status_reporter.debug_log("File saved", output_file=output_file, file_size_bytes=file_size)

    _emit_status(
        status_reporter, "poster.save.complete",
        f"✓ Done! Poster saved as {output_file}", output_file=output_file,
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
):
    """
    Generate a complete map poster with roads, water, parks, and typography.

    Creates a high-quality poster by fetching OSM data, rendering map layers,
    applying the current theme, and adding text labels with coordinates.

    Args:
        city: City name for display on poster
        country: Country name for display on poster
        point: (latitude, longitude) tuple for map center
        dist: Map radius in meters
        output_file: Path where poster will be saved
        output_format: File format ('png', 'svg', or 'pdf')
        width: Poster width in inches (default: 12)
        height: Poster height in inches (default: 16)
        country_label: Optional override for country text on poster
        _name_label: Optional override for city name (unused, reserved for future use)

    Raises:
        RuntimeError: If street network data cannot be retrieved
    """
    display_city = display_city or name_label or city
    display_country = display_country or country_label or country

    _emit_status(
        status_reporter, "poster.start",
        f"Generating map for {city}, {country}...",
        city=city, country=country,
        theme=theme.get("name", theme.get("id", "")),
    )

    # 1. Fetch data
    g, water, parks, compensated_dist = _fetch_map_data(
        point, dist, width, height, status_reporter=status_reporter,
    )

    _emit_status(
        status_reporter, "poster.data.ready",
        "✓ All data retrieved successfully!",
        city=city, country=country,
    )

    # 2. Memory check + Setup figure
    mem = _estimate_memory(width, height, dpi)
    if mem > _MAX_MEMORY_BYTES:
        raise ValueError(
            f"Estimated figure memory {mem / 1024**3:.1f} GB exceeds 2 GB limit. "
            "Reduce dimensions or DPI."
        )
    if mem > _WARN_MEMORY_BYTES:
        _emit_status(
            status_reporter, "memory.warning",
            f"\u26a0 Estimated figure memory: {mem / 1024**2:.0f} MB", bytes=mem,
        )

    _emit_status(
        status_reporter, "poster.render", "Rendering map...",
        city=city, country=country,
    )
    fig, ax = _setup_figure(width, height, theme)
    try:
        g_proj = ox.project_graph(g)

        # 3. Render layers
        _render_layers(
            ax, g_proj, point, fig, compensated_dist,
            water, parks, theme, status_reporter=status_reporter,
        )

        # 4. Typography
        _apply_typography(
            fig, ax, display_city, display_country, point,
            theme, fonts, width, height,
            show_attribution=show_attribution,
        )

        # 5. Save
        _save_output(
            fig, output_file, output_format, theme,
            width, height, dpi, status_reporter=status_reporter,
        )
    finally:
        plt.close(fig)


def _atomic_write_text(target: Path, content: str) -> None:
    """Write *content* to *target* atomically via a temp file + rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding=FILE_ENCODING) as fh:
            fh.write(content)
        Path(tmp_path).replace(target)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _write_metadata(output_file: str, metadata: dict[str, Any]) -> str:
    metadata_path = Path(output_file).with_suffix(".json")
    _atomic_write_text(
        metadata_path,
        json.dumps(metadata, indent=2, ensure_ascii=False),
    )
    return str(metadata_path)


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
        _logger.warning("No custom or bundled fonts available; falling back to monospace")
    coords = _resolve_coordinates(options, reporter)

    reporter.debug_log(
        "Resolved config",
        width=width,
        height=height,
        dpi=dpi,
        themes=themes_to_generate,
        coords=list(coords),
    )

    if not reporter.json_mode:
        _emit_status(reporter, "run.banner", "=" * 50 + "\nCity Map Poster Generator\n" + "=" * 50)
    reporter.emit(
        "run.start",
        city=options.city,
        country=options.country,
        themes=themes_to_generate,
        output_format=options.output_format,
    )

    output_dir = options.output_dir or os.environ.get(OUTPUT_DIR_ENV) or DEFAULT_POSTERS_DIR

    outputs: list[str] = []
    failures: list[str] = []
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
                country_label=options.country_label,
                display_city=options.display_city,
                display_country=options.display_country,
                fonts=custom_fonts,
                show_attribution=options.show_attribution,
                status_reporter=reporter,
            )
        except Exception as exc:
            _logger.warning("Theme '%s' failed: %s", theme_name, exc)
            failures.append(theme_name)
            continue
        metadata = {
            "city": options.city,
            "country": options.country,
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
            reporter, "run.complete.banner",
            "\n" + "=" * 50 + "\n✓ Poster generation complete!\n" + "=" * 50,
        )
    reporter.emit("run.complete", outputs=outputs)
    return outputs


def print_examples():
    """Print usage examples."""
    print("""
City Map Poster Generator
=========================

Usage:
  python create_map_poster.py --city <city> --country <country> [options]

Examples:
  # Iconic grid patterns
  python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000           # Manhattan grid
  python create_map_poster.py -c "Barcelona" -C "Spain" -t warm_beige -d 8000   # Eixample district grid

  # Waterfront & canals
  python create_map_poster.py -c "Venice" -C "Italy" -t blueprint -d 4000       # Canal network
  python create_map_poster.py -c "Amsterdam" -C "Netherlands" -t ocean -d 6000  # Concentric canals
  python create_map_poster.py -c "Dubai" -C "UAE" -t midnight_blue -d 15000     # Palm & coastline

  # Radial patterns
  python create_map_poster.py -c "Paris" -C "France" -t pastel_dream -d 10000   # Haussmann boulevards
  python create_map_poster.py -c "Moscow" -C "Russia" -t noir -d 12000          # Ring roads

  # Organic old cities
  python create_map_poster.py -c "Tokyo" -C "Japan" -t japanese_ink -d 15000    # Dense organic streets
  python create_map_poster.py -c "Marrakech" -C "Morocco" -t terracotta -d 5000 # Medina maze
  python create_map_poster.py -c "Rome" -C "Italy" -t warm_beige -d 8000        # Ancient street layout

  # Coastal cities
  python create_map_poster.py -c "San Francisco" -C "USA" -t sunset -d 10000    # Peninsula grid
  python create_map_poster.py -c "Sydney" -C "Australia" -t ocean -d 12000      # Harbor city
  python create_map_poster.py -c "Mumbai" -C "India" -t contrast_zones -d 18000 # Coastal peninsula

  # River cities
  python create_map_poster.py -c "London" -C "UK" -t noir -d 15000              # Thames curves
  python create_map_poster.py -c "Budapest" -C "Hungary" -t copper_patina -d 8000  # Danube split

  # List themes
  python create_map_poster.py --list-themes

Options:
  --city, -c        City name (required)
  --country, -C     Country name (required)
  --country-label   Override country text displayed on poster
  --theme, -t       Theme name (default: terracotta)
  --all-themes      Generate posters for all themes
  --distance, -d    Map radius in meters (default: 18000)
  --list-themes     List all available themes

Distance guide:
  4000-6000m   Small/dense cities (Venice, Amsterdam old center)
  8000-12000m  Medium cities, focused downtown (Paris, Barcelona)
  15000-20000m Large metros, full city view (Tokyo, Mumbai)

Available themes ship with the package (override via MAPTOPOSTER_THEMES_DIR).
Generated posters are saved to 'posters/' directory.
""")


def list_themes():
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
        except (OSError, json.JSONDecodeError):
            display_name = theme_name
            description = ""
        print(f"  {theme_name}")
        print(f"    {display_name}")
        if description:
            print(f"    {description}")
        print()
