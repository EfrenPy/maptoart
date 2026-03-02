"""Matplotlib rendering logic for the City Map Poster Generator."""

import logging
from typing import Any

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
from geopandas import GeoDataFrame
from matplotlib.font_manager import FontProperties
from networkx import MultiDiGraph
from shapely.geometry import Point

from ._util import StatusReporter, _emit_status, is_latin_script
from .font_management import _get_fonts

_logger = logging.getLogger(__name__)

_ZORDER = {"water": 0.5, "parks": 0.8, "gradient": 10, "text": 11}

_GRADIENT_VALS = np.linspace(0, 1, 256).reshape(-1, 1)
_GRADIENT_HSTACK = np.hstack((_GRADIENT_VALS, _GRADIENT_VALS))

_MAX_MEMORY_BYTES = 2 * 1024**3  # 2 GB hard limit
_WARN_MEMORY_BYTES = 500 * 1024**2  # 500 MB warning

# Font sizes (points, before scaling)
_BASE_FONT_CITY = 60
_BASE_FONT_COUNTRY = 22
_BASE_FONT_COORDS = 14
_BASE_FONT_ATTR = 8

# Vertical positions (fraction of axes height)
_POS_CITY_Y = 0.14
_POS_COUNTRY_Y = 0.10
_POS_COORDS_Y = 0.07
_POS_DIVIDER_Y = 0.125

# Gradient fade extent
_GRADIENT_BOTTOM_END = 0.25
_GRADIENT_TOP_START = 0.75

# City name scaling threshold
_CITY_NAME_SCALE_THRESHOLD = 10


def _estimate_memory(width: float, height: float, dpi: int) -> int:
    """Estimate figure memory in bytes (RGBA @ 4 bytes/pixel)."""
    return int(width * dpi * height * dpi * 4)


def _setup_figure(
    width: float,
    height: float,
    theme: dict[str, str],
) -> tuple[Any, Any]:
    """Create figure and axes with background color from *theme*."""
    fig, ax = plt.subplots(figsize=(width, height), facecolor=theme["bg"])
    ax.set_facecolor(theme["bg"])
    ax.set_position((0.0, 0.0, 1.0, 1.0))
    return fig, ax


def create_gradient_fade(ax: Any, color: str, location: str = "bottom", zorder: float = 10) -> None:
    """Creates a fade effect at the top or bottom of the map."""
    gradient = _GRADIENT_HSTACK

    rgb = mcolors.to_rgb(color)
    my_colors = np.zeros((256, 4))
    my_colors[:, 0] = rgb[0]
    my_colors[:, 1] = rgb[1]
    my_colors[:, 2] = rgb[2]

    if location == "bottom":
        my_colors[:, 3] = np.linspace(1, 0, 256)
        extent_y_start = 0.0
        extent_y_end = _GRADIENT_BOTTOM_END
    else:
        my_colors[:, 3] = np.linspace(0, 1, 256)
        extent_y_start = _GRADIENT_TOP_START
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


# Road hierarchy: maps OSM highway tags to (theme color key, line width).
# Ordered from most to least important road type.
_ROAD_HIERARCHY: list[tuple[frozenset[str], str, float]] = [
    (frozenset({"motorway", "motorway_link"}), "road_motorway", 1.2),
    (frozenset({"trunk", "trunk_link", "primary", "primary_link"}), "road_primary", 1.0),
    (frozenset({"secondary", "secondary_link"}), "road_secondary", 0.8),
    (frozenset({"tertiary", "tertiary_link"}), "road_tertiary", 0.6),
    (frozenset({"residential", "living_street", "unclassified"}), "road_residential", 0.4),
]
_DEFAULT_ROAD_COLOR_KEY = "road_default"
_DEFAULT_ROAD_WIDTH = 0.4


_UNKNOWN_HIGHWAY = ""  # sentinel: falls through _ROAD_HIERARCHY to road_default


def _classify_highway(data: dict[str, Any]) -> str:
    """Extract and normalize the highway tag from an edge data dict."""
    highway = data.get("highway", _UNKNOWN_HIGHWAY)
    if isinstance(highway, list):
        highway = highway[0] if highway else _UNKNOWN_HIGHWAY
    return highway


def get_edge_styles(
    g: MultiDiGraph, theme: dict[str, str],
) -> tuple[list[str], list[float]]:
    """Classify edges once, returning (colors, widths) in a single pass."""
    colors: list[str] = []
    widths: list[float] = []
    for _u, _v, data in g.edges(data=True):
        highway = _classify_highway(data)
        color = theme[_DEFAULT_ROAD_COLOR_KEY]
        width = _DEFAULT_ROAD_WIDTH
        for tags, color_key, w in _ROAD_HIERARCHY:
            if highway in tags:
                color = theme[color_key]
                width = w
                break
        colors.append(color)
        widths.append(width)
    return colors, widths


# Backward-compatible wrappers
def get_edge_colors_by_type(g: MultiDiGraph, theme: dict[str, str]) -> list[str]:
    """Assigns colors to edges based on road type hierarchy."""
    return get_edge_styles(g, theme)[0]


def get_edge_widths_by_type(g: MultiDiGraph) -> list[float]:
    """Assigns line widths to edges based on road type."""
    # Dummy theme — colors are discarded, only widths returned.
    _dummy = {_DEFAULT_ROAD_COLOR_KEY: "#000000"}
    for tags, color_key, _w in _ROAD_HIERARCHY:
        _dummy[color_key] = "#000000"
    return get_edge_styles(g, _dummy)[1]


def get_crop_limits(
    g_proj: MultiDiGraph,
    center_lat_lon: tuple[float, float],
    fig: Any,
    dist: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Crop inward to preserve aspect ratio while guaranteeing full coverage."""
    lat, lon = center_lat_lon

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

    half_x = dist
    half_y = dist

    if aspect > 1:
        half_y = half_x / aspect
    else:
        half_x = half_y * aspect

    return (
        (center_x - half_x, center_x + half_x),
        (center_y - half_y, center_y + half_y),
    )


def _project_and_plot_layer(
    gdf: GeoDataFrame | None,
    target_crs: Any,
    ax: Any,
    color: str,
    zorder: float,
    label: str,
) -> None:
    """Project polygon features to *target_crs* and plot them on *ax*.

    Filters for Polygon/MultiPolygon geometries, attempts osmnx projection
    with a fallback to direct CRS transform, then plots.
    """
    if gdf is None or gdf.empty:
        return
    polys = gdf[gdf.geometry.type.isin(["Polygon", "MultiPolygon"])]
    if polys.empty:
        return
    try:
        polys = ox.projection.project_gdf(polys)
    except (ValueError, RuntimeError):
        _logger.debug("osmnx projection failed for %s; falling back to to_crs", label)
        polys = polys.to_crs(target_crs)
    polys.plot(ax=ax, facecolor=color, edgecolor="none", zorder=zorder)


def _render_layers(
    ax: Any,
    g_proj: MultiDiGraph,
    point: tuple[float, float],
    fig: Any,
    compensated_dist: float,
    water: GeoDataFrame | None,
    parks: GeoDataFrame | None,
    theme: dict[str, str],
    *,
    status_reporter: StatusReporter | None = None,
) -> None:
    """Project graph, plot water/parks/roads/gradient layers."""
    target_crs = g_proj.graph["crs"]
    _project_and_plot_layer(water, target_crs, ax, theme["water"], _ZORDER["water"], "water")
    _project_and_plot_layer(parks, target_crs, ax, theme["parks"], _ZORDER["parks"], "parks")

    _emit_status(status_reporter, "poster.roads", "Applying road hierarchy colors...")
    edge_colors, edge_widths = get_edge_styles(g_proj, theme)

    crop_xlim, crop_ylim = get_crop_limits(g_proj, point, fig, compensated_dist)
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

    create_gradient_fade(ax, theme['gradient_color'], location='bottom', zorder=_ZORDER["gradient"])
    create_gradient_fade(ax, theme['gradient_color'], location='top', zorder=_ZORDER["gradient"])


def _apply_typography(
    fig: Any,
    ax: Any,
    display_city: str,
    display_country: str,
    point: tuple[float, float],
    theme: dict[str, str],
    fonts: dict[str, str] | None,
    width: float,
    height: float,
    *,
    show_attribution: bool = True,
) -> None:
    """Scale fonts, render city/country/coords/attribution text."""
    scale_factor = min(height, width) / 12.0
    base_main = _BASE_FONT_CITY
    base_sub = _BASE_FONT_COUNTRY
    base_coords = _BASE_FONT_COORDS
    base_attr = _BASE_FONT_ATTR

    active_fonts = fonts or _get_fonts()
    _required_weights = ("light", "regular", "bold")
    if active_fonts and all(k in active_fonts for k in _required_weights):
        font_sub = FontProperties(fname=active_fonts["light"], size=base_sub * scale_factor)
        font_coords = FontProperties(fname=active_fonts["regular"], size=base_coords * scale_factor)
        font_attr = FontProperties(fname=active_fonts["light"], size=base_attr * scale_factor)
    else:
        active_fonts = None  # ensure monospace path is used for city font too
        font_sub = FontProperties(family="monospace", weight="normal", size=base_sub * scale_factor)
        font_coords = FontProperties(family="monospace", size=base_coords * scale_factor)
        font_attr = FontProperties(family="monospace", size=base_attr * scale_factor)

    if is_latin_script(display_city):
        spaced_city = "  ".join(list(display_city.upper()))
    else:
        spaced_city = display_city

    base_adjusted_main = base_main * scale_factor
    city_char_count = len(display_city)
    if city_char_count > _CITY_NAME_SCALE_THRESHOLD:
        length_factor = _CITY_NAME_SCALE_THRESHOLD / city_char_count
        adjusted_font_size = max(base_adjusted_main * length_factor, 10 * scale_factor)
    else:
        adjusted_font_size = base_adjusted_main

    if active_fonts:
        font_main_adjusted = FontProperties(fname=active_fonts["bold"], size=adjusted_font_size)
    else:
        font_main_adjusted = FontProperties(family="monospace", weight="bold", size=adjusted_font_size)

    ax.text(
        0.5, _POS_CITY_Y, spaced_city,
        transform=ax.transAxes, color=theme["text"],
        ha="center", fontproperties=font_main_adjusted, zorder=_ZORDER["text"],
    )
    ax.text(
        0.5, _POS_COUNTRY_Y, display_country.upper(),
        transform=ax.transAxes, color=theme["text"],
        ha="center", fontproperties=font_sub, zorder=_ZORDER["text"],
    )

    lat, lon = point
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    coords = f"{abs(lat):.4f}\u00b0 {lat_dir} / {abs(lon):.4f}\u00b0 {lon_dir}"
    ax.text(
        0.5, _POS_COORDS_Y, coords,
        transform=ax.transAxes, color=theme["text"], alpha=0.7,
        ha="center", fontproperties=font_coords, zorder=_ZORDER["text"],
    )

    ax.plot(
        [0.4, 0.6], [_POS_DIVIDER_Y, _POS_DIVIDER_Y],
        transform=ax.transAxes, color=theme["text"],
        linewidth=1 * scale_factor, zorder=_ZORDER["text"],
    )

    if show_attribution:
        ax.text(
            0.98, 0.02, "\u00a9 OpenStreetMap contributors",
            transform=ax.transAxes, color=theme["text"], alpha=0.5,
            ha="right", va="bottom", fontproperties=font_attr, zorder=_ZORDER["text"],
        )
