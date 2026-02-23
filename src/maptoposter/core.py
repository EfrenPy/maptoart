"""Core rendering helpers for the City Map Poster Generator."""

import asyncio
import json
import os
import pickle
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence, cast

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
from geopandas import GeoDataFrame
from geopy.geocoders import Nominatim
from matplotlib.font_manager import FontProperties
from networkx import MultiDiGraph
from shapely.geometry import Point
from tqdm import tqdm

from .font_management import load_fonts


class CacheError(Exception):
    """Raised when a cache operation fails."""


CACHE_DIR_PATH = os.environ.get("CACHE_DIR", "cache")
CACHE_DIR = Path(CACHE_DIR_PATH)
CACHE_DIR.mkdir(exist_ok=True)

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

FONTS = load_fonts()


class StatusReporter:
    """Lightweight status/event logger with optional JSON output."""

    def __init__(self, json_mode: bool = False) -> None:
        self.json_mode = json_mode

    def emit(self, event: str, message: str | None = None, **extra: Any) -> None:
        payload = {
            "event": event,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            **extra,
        }
        if message is not None:
            payload["message"] = message
        if self.json_mode:
            print(json.dumps(payload, ensure_ascii=False))
        elif message is not None:
            print(message)


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
    missing = [theme for theme in requested if theme not in available]
    if missing:
        raise ValueError(f"Theme(s) not found: {', '.join(missing)}. Available themes: {', '.join(available)}")
    return requested


def _resolve_coordinates(
    options: PosterGenerationOptions,
    status_reporter: StatusReporter | None,
) -> tuple[float, float]:
    """Return the map center (lat, lon) based on overrides or geocode lookup."""

    if options.latitude is not None and options.longitude is not None:
        coords = (options.latitude, options.longitude)
        _emit_status(
            status_reporter,
            "coordinates.override",
            f"✓ Coordinates: {coords[0]}, {coords[1]}",
            latitude=coords[0],
            longitude=coords[1],
        )
        return coords
    if (options.latitude is None) ^ (options.longitude is None):
        raise ValueError("Both latitude and longitude must be provided together.")
    return get_coordinates(
        options.city,
        options.country,
        status_reporter=status_reporter,
    )


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
    return os.path.join(CACHE_DIR, f"{safe}.pkl")


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
        path = _cache_path(key)
        if not os.path.exists(path):
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
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
        path = _cache_path(key)
        with open(path, "wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
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

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_slug = city.lower().replace(" ", "_")
    ext = output_format.lower()
    filename = f"{city_slug}_{theme_name}_{timestamp}.{ext}"
    return str(Path(output_dir) / filename)


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

    theme_file = THEMES_DIR / f"{theme_name}.json"

    if not theme_file.exists():
        _emit_status(
            status_reporter,
            "theme.fallback",
            f"⚠ Theme file '{theme_file}' not found. Using default terracotta theme.",
            theme=theme_name,
        )
        return {
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

    with theme_file.open("r", encoding=FILE_ENCODING) as f:
        theme = json.load(f)
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
        return theme


# Load theme (can be changed via command line or input)


def create_gradient_fade(ax, color, location="bottom", zorder=10):
    """
    Creates a fade effect at the top or bottom of the map.
    """
    vals = np.linspace(0, 1, 256).reshape(-1, 1)
    gradient = np.hstack((vals, vals))

    rgb = mcolors.to_rgb(color)
    my_colors = np.zeros((256, 4))
    my_colors[:, 0] = rgb[0]
    my_colors[:, 1] = rgb[1]
    my_colors[:, 2] = rgb[2]

    if location == "bottom":
        my_colors[:, 3] = np.linspace(1, 0, 256)
        extent_y_start = 0
        extent_y_end = 0.25
    else:
        my_colors[:, 3] = np.linspace(0, 1, 256)
        extent_y_start = 0.75
        extent_y_end = 1.0

    custom_cmap = mcolors.ListedColormap(my_colors)

    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    y_range = ylim[1] - ylim[0]

    y_bottom = ylim[0] + y_range * extent_y_start
    y_top = ylim[0] + y_range * extent_y_end

    ax.imshow(
        gradient,
        extent=[xlim[0], xlim[1], y_bottom, y_top],
        aspect="auto",
        cmap=custom_cmap,
        zorder=zorder,
        origin="lower",
    )


def get_edge_colors_by_type(g, theme):
    """
    Assigns colors to edges based on road type hierarchy.
    Returns a list of colors corresponding to each edge in the graph.
    """
    edge_colors = []

    for _u, _v, data in g.edges(data=True):
        # Get the highway type (can be a list or string)
        highway = data.get('highway', 'unclassified')

        # Handle list of highway types (take the first one)
        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'

        # Assign color based on road type
        if highway in ["motorway", "motorway_link"]:
            color = theme["road_motorway"]
        elif highway in ["trunk", "trunk_link", "primary", "primary_link"]:
            color = theme["road_primary"]
        elif highway in ["secondary", "secondary_link"]:
            color = theme["road_secondary"]
        elif highway in ["tertiary", "tertiary_link"]:
            color = theme["road_tertiary"]
        elif highway in ["residential", "living_street", "unclassified"]:
            color = theme["road_residential"]
        else:
            color = theme['road_default']

        edge_colors.append(color)

    return edge_colors


def get_edge_widths_by_type(g):
    """
    Assigns line widths to edges based on road type.
    Major roads get thicker lines.
    """
    edge_widths = []

    for _u, _v, data in g.edges(data=True):
        highway = data.get('highway', 'unclassified')

        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'

        # Assign width based on road importance
        if highway in ["motorway", "motorway_link"]:
            width = 1.2
        elif highway in ["trunk", "trunk_link", "primary", "primary_link"]:
            width = 1.0
        elif highway in ["secondary", "secondary_link"]:
            width = 0.8
        elif highway in ["tertiary", "tertiary_link"]:
            width = 0.6
        else:
            width = 0.4

        edge_widths.append(width)

    return edge_widths


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
    coords = f"coords_{city.lower()}_{country.lower()}"
    cached = cache_get(coords)
    if cached:
        _emit_status(
            status_reporter,
            "geocode.cache_hit",
            f"✓ Using cached coordinates for {city}, {country}",
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
    geolocator = Nominatim(user_agent="city_map_poster", timeout=10)

    # Add a small delay to respect Nominatim's usage policy
    time.sleep(1)

    try:
        location = geolocator.geocode(f"{city}, {country}")
    except Exception as e:
        raise ValueError(f"Geocoding failed for {city}, {country}: {e}") from e

    # If geocode returned a coroutine in some environments, run it to get the result.
    if asyncio.iscoroutine(location):
        try:
            location = asyncio.run(location)
        except RuntimeError as exc:
            # If an event loop is already running, try using it to complete the coroutine.
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Running event loop in the same thread; raise a clear error.
                raise RuntimeError(
                    "Geocoder returned a coroutine while an event loop is already running. "
                    "Run this script in a synchronous environment."
                ) from exc
            location = loop.run_until_complete(location)

    if location:
        # Use getattr to safely access address (helps static analyzers)
        addr = getattr(location, "address", None)
        message = f"✓ Found: {addr}" if addr else "✓ Found location (address not available)"
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
            f"✓ Coordinates: {location.latitude}, {location.longitude}",
            city=city,
            country=country,
            latitude=location.latitude,
            longitude=location.longitude,
        )
        try:
            cache_set(coords, (location.latitude, location.longitude))
        except CacheError as e:
            print(e)
        return (location.latitude, location.longitude)

    _emit_status(
        status_reporter,
        "geocode.error",
        f"✗ Could not find coordinates for {city}, {country}",
        city=city,
        country=country,
    )
    raise ValueError(f"Could not find coordinates for {city}, {country}")


def get_crop_limits(g_proj, center_lat_lon, fig, dist):
    """
    Crop inward to preserve aspect ratio while guaranteeing
    full coverage of the requested radius.
    """
    lat, lon = center_lat_lon

    # Project center point into graph CRS
    center = (
        ox.projection.project_geometry(
            Point(lon, lat),
            crs="EPSG:4326",
            to_crs=g_proj.graph["crs"]
        )[0]
    )
    center_x, center_y = center.x, center.y

    fig_width, fig_height = fig.get_size_inches()
    aspect = fig_width / fig_height

    # Start from the *requested* radius
    half_x = dist
    half_y = dist

    # Cut inward to match aspect
    if aspect > 1:  # landscape → reduce height
        half_y = half_x / aspect
    else:  # portrait → reduce width
        half_x = half_y * aspect

    return (
        (center_x - half_x, center_x + half_x),
        (center_y - half_y, center_y + half_y),
    )


def fetch_graph(
    point,
    dist,
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
            print(e)
        _emit_status(
            status_reporter,
            "graph.download.complete",
            "✓ Street network downloaded",
            distance=dist,
        )
        return g
    except Exception as e:
        _emit_status(
            status_reporter,
            "graph.download.error",
            f"OSMnx error while fetching graph: {e}",
            distance=dist,
        )
        return None


def fetch_features(
    point,
    dist,
    tags,
    name,
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
            print(e)
        _emit_status(
            status_reporter,
            f"{name}.download.complete",
            f"✓ {name.capitalize()} downloaded",
            distance=dist,
        )
        return data
    except Exception as e:
        _emit_status(
            status_reporter,
            f"{name}.download.error",
            f"OSMnx error while fetching {name}: {e}",
            distance=dist,
        )
        return None


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
    # Handle display names for i18n support
    # Priority: display_city/display_country > name_label/country_label > city/country
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

    # Progress bar for data fetching
    with tqdm(
        total=3,
        desc="Fetching map data",
        unit="step",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
    ) as pbar:
        # 1. Fetch Street Network
        pbar.set_description("Downloading street network")
        compensated_dist = dist * (max(height, width) / min(height, width)) / 4  # To compensate for viewport crop
        g = fetch_graph(point, compensated_dist, status_reporter=status_reporter)
        if g is None:
            raise RuntimeError("Failed to retrieve street network data.")
        pbar.update(1)

        # 2. Fetch Water Features
        pbar.set_description("Downloading water features")
        water = fetch_features(
            point,
            compensated_dist,
            tags={"natural": ["water", "bay", "strait"], "waterway": "riverbank"},
            name="water",
            status_reporter=status_reporter,
        )
        pbar.update(1)

        # 3. Fetch Parks
        pbar.set_description("Downloading parks/green spaces")
        parks = fetch_features(
            point,
            compensated_dist,
            tags={"leisure": "park", "landuse": "grass"},
            name="parks",
            status_reporter=status_reporter,
        )
        pbar.update(1)

    _emit_status(
        status_reporter,
        "poster.data.ready",
        "✓ All data retrieved successfully!",
        city=city,
        country=country,
    )

    # 2. Setup Plot
    _emit_status(
        status_reporter,
        "poster.render",
        "Rendering map...",
        city=city,
        country=country,
    )
    fig, ax = plt.subplots(figsize=(width, height), facecolor=theme["bg"])
    ax.set_facecolor(theme["bg"])
    ax.set_position((0.0, 0.0, 1.0, 1.0))

    # Project graph to a metric CRS so distances and aspect are linear (meters)
    g_proj = ox.project_graph(g)

    # 3. Plot Layers
    # Layer 1: Polygons (filter to only plot polygon/multipolygon geometries, not points)
    if water is not None and not water.empty:
        # Filter to only polygon/multipolygon geometries to avoid point features showing as dots
        water_polys = water[water.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not water_polys.empty:
            # Project water features in the same CRS as the graph
            try:
                water_polys = ox.projection.project_gdf(water_polys)
            except Exception:
                water_polys = water_polys.to_crs(g_proj.graph['crs'])
            water_polys.plot(ax=ax, facecolor=theme['water'], edgecolor='none', zorder=0.5)

    if parks is not None and not parks.empty:
        # Filter to only polygon/multipolygon geometries to avoid point features showing as dots
        parks_polys = parks[parks.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not parks_polys.empty:
            # Project park features in the same CRS as the graph
            try:
                parks_polys = ox.projection.project_gdf(parks_polys)
            except Exception:
                parks_polys = parks_polys.to_crs(g_proj.graph['crs'])
            parks_polys.plot(ax=ax, facecolor=theme['parks'], edgecolor='none', zorder=0.8)
    # Layer 2: Roads with hierarchy coloring
    _emit_status(
        status_reporter,
        "poster.roads",
        "Applying road hierarchy colors...",
    )
    edge_colors = get_edge_colors_by_type(g_proj, theme)
    edge_widths = get_edge_widths_by_type(g_proj)

    # Determine cropping limits to maintain the poster aspect ratio
    crop_xlim, crop_ylim = get_crop_limits(g_proj, point, fig, compensated_dist)
    # Plot the projected graph and then apply the cropped limits
    ox.plot_graph(
        g_proj, ax=ax, bgcolor=theme['bg'],
        node_size=0,
        edge_color=edge_colors,
        edge_linewidth=edge_widths,
        show=False,
        close=False,
    )
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(crop_xlim)
    ax.set_ylim(crop_ylim)

    # Layer 3: Gradients (Top and Bottom)
    create_gradient_fade(ax, theme['gradient_color'], location='bottom', zorder=10)
    create_gradient_fade(ax, theme['gradient_color'], location='top', zorder=10)

    # Calculate scale factor based on smaller dimension (reference 12 inches)
    # This ensures text scales properly for both portrait and landscape orientations
    scale_factor = min(height, width) / 12.0

    # Base font sizes (at 12 inches width)
    base_main = 60
    base_sub = 22
    base_coords = 14
    base_attr = 8

    # 4. Typography - use custom fonts if provided, otherwise use default FONTS
    active_fonts = fonts or FONTS
    if active_fonts:
        # font_main is calculated dynamically later based on length
        font_sub = FontProperties(
            fname=active_fonts["light"], size=base_sub * scale_factor
        )
        font_coords = FontProperties(
            fname=active_fonts["regular"], size=base_coords * scale_factor
        )
        font_attr = FontProperties(
            fname=active_fonts["light"], size=base_attr * scale_factor
        )
    else:
        # Fallback to system fonts
        font_sub = FontProperties(
            family="monospace", weight="normal", size=base_sub * scale_factor
        )
        font_coords = FontProperties(
            family="monospace", size=base_coords * scale_factor
        )
        font_attr = FontProperties(family="monospace", size=base_attr * scale_factor)

    # Format city name based on script type
    # Latin scripts: apply uppercase and letter spacing for aesthetic
    # Non-Latin scripts (CJK, Thai, Arabic, etc.): no spacing, preserve case structure
    if is_latin_script(display_city):
        # Latin script: uppercase with letter spacing (e.g., "P  A  R  I  S")
        spaced_city = "  ".join(list(display_city.upper()))
    else:
        # Non-Latin script: no spacing, no forced uppercase
        # For scripts like Arabic, Thai, Japanese, etc.
        spaced_city = display_city

    # Dynamically adjust font size based on city name length to prevent truncation
    # We use the already scaled "main" font size as the starting point.
    base_adjusted_main = base_main * scale_factor
    city_char_count = len(display_city)

    # Heuristic: If length is > 10, start reducing.
    if city_char_count > 10:
        length_factor = 10 / city_char_count
        adjusted_font_size = max(base_adjusted_main * length_factor, 10 * scale_factor)
    else:
        adjusted_font_size = base_adjusted_main

    if active_fonts:
        font_main_adjusted = FontProperties(
            fname=active_fonts["bold"], size=adjusted_font_size
        )
    else:
        font_main_adjusted = FontProperties(
            family="monospace", weight="bold", size=adjusted_font_size
        )

    # --- BOTTOM TEXT ---
    ax.text(
        0.5,
        0.14,
        spaced_city,
        transform=ax.transAxes,
        color=theme["text"],
        ha="center",
        fontproperties=font_main_adjusted,
        zorder=11,
    )

    ax.text(
        0.5,
        0.10,
        display_country.upper(),
        transform=ax.transAxes,
        color=theme["text"],
        ha="center",
        fontproperties=font_sub,
        zorder=11,
    )

    lat, lon = point
    coords = (
        f"{lat:.4f}° N / {lon:.4f}° E"
        if lat >= 0
        else f"{abs(lat):.4f}° S / {lon:.4f}° E"
    )
    if lon < 0:
        coords = coords.replace("E", "W")

    ax.text(
        0.5,
        0.07,
        coords,
        transform=ax.transAxes,
        color=theme["text"],
        alpha=0.7,
        ha="center",
        fontproperties=font_coords,
        zorder=11,
    )

    ax.plot(
        [0.4, 0.6],
        [0.125, 0.125],
        transform=ax.transAxes,
        color=theme["text"],
        linewidth=1 * scale_factor,
        zorder=11,
    )

    # --- ATTRIBUTION (bottom right) ---
    if show_attribution:
        if FONTS:
            font_attr = FontProperties(fname=FONTS["light"], size=8)
        else:
            font_attr = FontProperties(family="monospace", size=8)

        ax.text(
            0.98,
            0.02,
            "© OpenStreetMap contributors",
            transform=ax.transAxes,
            color=theme["text"],
            alpha=0.5,
            ha="right",
            va="bottom",
            fontproperties=font_attr,
            zorder=11,
        )

    # 5. Save
    _emit_status(
        status_reporter,
        "poster.save.start",
        f"Saving to {output_file}...",
        output_file=output_file,
    )

    fmt = output_format.lower()
    save_kwargs = dict(
        facecolor=theme["bg"],
        bbox_inches="tight",
        pad_inches=0.05,
    )

    if fmt == "png":
        save_kwargs["dpi"] = dpi
        output_width_px = int(width * dpi)
        output_height_px = int(height * dpi)
        print(f"  Output resolution: {output_width_px} x {output_height_px} px ({dpi} DPI)")
    else:
        # Vector formats (PDF, SVG) are resolution-independent.
        # DPI only affects rasterized elements (fills, gradients).
        # Cap at 300 to avoid excessive memory usage.
        MAX_VECTOR_DPI = 300
        effective_dpi = min(dpi, MAX_VECTOR_DPI)
        save_kwargs["dpi"] = effective_dpi
        if dpi > MAX_VECTOR_DPI:
            print(
                f"  Using {effective_dpi} DPI for {fmt.upper()} (vector format is resolution-independent, "
                f"high DPI not needed)"
            )

    plt.savefig(output_file, format=fmt, **save_kwargs)

    plt.close()
    _emit_status(
        status_reporter,
        "poster.save.complete",
        f"✓ Done! Poster saved as {output_file}",
        output_file=output_file,
    )


def _write_metadata(output_file: str, metadata: dict[str, Any]) -> str:
    metadata_path = Path(output_file).with_suffix(".json")
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding=FILE_ENCODING,
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
    coords = _resolve_coordinates(options, reporter)

    if not reporter.json_mode:
        print("=" * 50)
        print("City Map Poster Generator")
        print("=" * 50)
    reporter.emit(
        "run.start",
        city=options.city,
        country=options.country,
        themes=themes_to_generate,
        output_format=options.output_format,
    )

    output_dir = options.output_dir or os.environ.get(OUTPUT_DIR_ENV) or DEFAULT_POSTERS_DIR

    outputs: list[str] = []
    for theme_name in themes_to_generate:
        theme = load_theme(theme_name, status_reporter=reporter)
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
            country_label=options.country_label,
            display_city=options.display_city,
            display_country=options.display_country,
            fonts=custom_fonts,
            show_attribution=options.show_attribution,
            status_reporter=reporter,
        )
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
            "generated_at": datetime.utcnow().isoformat() + "Z",
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

    if not reporter.json_mode:
        print("\n" + "=" * 50)
        print("✓ Poster generation complete!")
        print("=" * 50)
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
