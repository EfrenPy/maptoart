"""Matplotlib rendering logic for the City Map Poster Generator."""

from typing import Any

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
from geopandas import GeoDataFrame
from matplotlib.font_manager import FontProperties
from networkx import MultiDiGraph
from shapely.geometry import Point

from ._util import StatusReporter, _emit_status

_ZORDER = {"water": 0.5, "parks": 0.8, "gradient": 10, "text": 11}

_GRADIENT_VALS = np.linspace(0, 1, 256).reshape(-1, 1)
_GRADIENT_HSTACK = np.hstack((_GRADIENT_VALS, _GRADIENT_VALS))

_MAX_MEMORY_BYTES = 2 * 1024**3  # 2 GB hard limit
_WARN_MEMORY_BYTES = 500 * 1024**2  # 500 MB warning


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


def get_edge_colors_by_type(g: MultiDiGraph, theme: dict[str, str]) -> list[str]:
    """Assigns colors to edges based on road type hierarchy."""
    edge_colors = []

    for _u, _v, data in g.edges(data=True):
        highway = data.get('highway', 'unclassified')
        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'

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


def get_edge_widths_by_type(g: MultiDiGraph) -> list[float]:
    """Assigns line widths to edges based on road type."""
    edge_widths = []

    for _u, _v, data in g.edges(data=True):
        highway = data.get('highway', 'unclassified')
        if isinstance(highway, list):
            highway = highway[0] if highway else 'unclassified'

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
    if water is not None and not water.empty:
        water_polys = water[water.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not water_polys.empty:
            try:
                water_polys = ox.projection.project_gdf(water_polys)
            except (ValueError, RuntimeError):
                water_polys = water_polys.to_crs(g_proj.graph['crs'])
            water_polys.plot(ax=ax, facecolor=theme['water'], edgecolor='none', zorder=_ZORDER["water"])

    if parks is not None and not parks.empty:
        parks_polys = parks[parks.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not parks_polys.empty:
            try:
                parks_polys = ox.projection.project_gdf(parks_polys)
            except (ValueError, RuntimeError):
                parks_polys = parks_polys.to_crs(g_proj.graph['crs'])
            parks_polys.plot(ax=ax, facecolor=theme['parks'], edgecolor='none', zorder=_ZORDER["parks"])

    _emit_status(status_reporter, "poster.roads", "Applying road hierarchy colors...")
    edge_colors = get_edge_colors_by_type(g_proj, theme)
    edge_widths = get_edge_widths_by_type(g_proj)

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
    from .core import _get_fonts, is_latin_script

    scale_factor = min(height, width) / 12.0
    base_main, base_sub, base_coords, base_attr = 60, 22, 14, 8

    active_fonts = fonts or _get_fonts()
    if active_fonts:
        font_sub = FontProperties(fname=active_fonts["light"], size=base_sub * scale_factor)
        font_coords = FontProperties(fname=active_fonts["regular"], size=base_coords * scale_factor)
        font_attr = FontProperties(fname=active_fonts["light"], size=base_attr * scale_factor)
    else:
        font_sub = FontProperties(family="monospace", weight="normal", size=base_sub * scale_factor)
        font_coords = FontProperties(family="monospace", size=base_coords * scale_factor)
        font_attr = FontProperties(family="monospace", size=base_attr * scale_factor)

    if is_latin_script(display_city):
        spaced_city = "  ".join(list(display_city.upper()))
    else:
        spaced_city = display_city

    base_adjusted_main = base_main * scale_factor
    city_char_count = len(display_city)
    if city_char_count > 10:
        length_factor = 10 / city_char_count
        adjusted_font_size = max(base_adjusted_main * length_factor, 10 * scale_factor)
    else:
        adjusted_font_size = base_adjusted_main

    if active_fonts:
        font_main_adjusted = FontProperties(fname=active_fonts["bold"], size=adjusted_font_size)
    else:
        font_main_adjusted = FontProperties(family="monospace", weight="bold", size=adjusted_font_size)

    ax.text(
        0.5, 0.14, spaced_city,
        transform=ax.transAxes, color=theme["text"],
        ha="center", fontproperties=font_main_adjusted, zorder=_ZORDER["text"],
    )
    ax.text(
        0.5, 0.10, display_country.upper(),
        transform=ax.transAxes, color=theme["text"],
        ha="center", fontproperties=font_sub, zorder=_ZORDER["text"],
    )

    lat, lon = point
    coords = (
        f"{lat:.4f}\u00b0 N / {lon:.4f}\u00b0 E"
        if lat >= 0
        else f"{abs(lat):.4f}\u00b0 S / {lon:.4f}\u00b0 E"
    )
    if lon < 0:
        coords = coords.replace("E", "W")
    ax.text(
        0.5, 0.07, coords,
        transform=ax.transAxes, color=theme["text"], alpha=0.7,
        ha="center", fontproperties=font_coords, zorder=_ZORDER["text"],
    )

    ax.plot(
        [0.4, 0.6], [0.125, 0.125],
        transform=ax.transAxes, color=theme["text"],
        linewidth=1 * scale_factor, zorder=_ZORDER["text"],
    )

    if show_attribution:
        fonts_for_attr = _get_fonts()
        if fonts_for_attr:
            font_attr = FontProperties(fname=fonts_for_attr["light"], size=8)
        else:
            font_attr = FontProperties(family="monospace", size=8)
        ax.text(
            0.98, 0.02, "\u00a9 OpenStreetMap contributors",
            transform=ax.transAxes, color=theme["text"], alpha=0.5,
            ha="right", va="bottom", fontproperties=font_attr, zorder=_ZORDER["text"],
        )
