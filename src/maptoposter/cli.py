"""Command-line entry point for the City Map Poster Generator."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
import warnings
from dataclasses import fields
from pathlib import Path
from typing import Any, Sequence

from lat_lon_parser import parse
import yaml

from . import __version__
from ._util import MAX_INPUT_FILE_SIZE
from .core import (
    PosterGenerationOptions,
    StatusReporter,
    _apply_paper_size,
    _resolve_coordinates,
    _validate_dpi,
    generate_posters,
    get_available_themes,
    list_themes,
    print_examples,
)

OPTION_FIELD_NAMES = {field.name for field in fields(PosterGenerationOptions)}
CLI_TO_OPTION_FIELD = {
    "city": "city",
    "country": "country",
    "distance": "distance",
    "width": "width",
    "height": "height",
    "dpi": "dpi",
    "format": "output_format",
    "theme": "theme",
    "themes": "themes",
    "all_themes": "all_themes",
    "latitude": "latitude",
    "longitude": "longitude",
    "country_label": "country_label",
    "display_city": "display_city",
    "display_country": "display_country",
    "font_family": "font_family",
    "paper_size": "paper_size",
    "orientation": "orientation",
    "output_dir": "output_dir",
    "no_attribution": "show_attribution",
    "parallel_themes": "parallel_themes",
}
CONFIG_KEY_ALIASES = {
    "format": "output_format",
}


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="maptoposter-cli",
        description=(
            f"City Map Poster Generator v{__version__}\n"
            "\n"
            "Generate beautiful, minimalist map posters for any city in the world.\n"
            "\n"
            "Key features:\n"
            "  - Output formats: PNG (raster), SVG and PDF (vector, scale to any size)\n"
            "  - 17 built-in color themes with fuzzy name matching (typos auto-corrected)\n"
            "  - Multilingual text: Google Fonts auto-downloaded for CJK, Arabic, Thai, etc.\n"
            "  - Batch processing: CSV/JSON input, generate hundreds of posters in one run\n"
            "  - Parallel rendering: multiprocessing for multi-theme and batch workflows\n"
            "  - HTML gallery: self-contained page with CSS grid and metadata cards\n"
            "  - Config files: JSON or YAML, all CLI options as snake_case keys\n"
            "  - Paper sizes: A0-A4 presets with portrait/landscape orientation\n"
            "  - Custom coordinates: override geocoding with --latitude/--longitude (DMS ok)\n"
            "  - Dry-run mode: preview config, dimensions, and estimated file size\n"
            "  - Cache management: OSM data cached with TTL (7d map, 30d coords), HMAC integrity\n"
            "  - Metadata sidecar: each poster gets a .json with coords, theme, DPI, timestamps\n"
            "  - Structured logging: --log-format json for machine-readable event streams\n"
            "  - Memory safety: auto DPI reduction if render would exceed 2 GB\n"
            "  - Docker image: ghcr.io/efrenpy/maptoposter (multi-stage, slim build)\n"
            "\n"
            "Install:  pip install maptoposter\n"
            "Docker:   docker pull ghcr.io/efrenpy/maptoposter\n"
            "Docs:     https://github.com/EfrenPy/maptoposter\n"
            "PyPI:     https://pypi.org/project/maptoposter/"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Quick start:
  maptoposter-cli -c "Paris" -C "France"
  maptoposter-cli -c "Tokyo" -C "Japan" -t japanese_ink -d 15000
  maptoposter-cli -c "New York" -C "USA" -t noir -p A2 --dpi 600

Multiple themes:
  maptoposter-cli -c Paris -C France --themes noir terracotta sunset
  maptoposter-cli -c Tokyo -C Japan --all-themes
  maptoposter-cli -c London -C UK --all-themes --parallel-themes

Output formats (PNG, SVG, PDF):
  maptoposter-cli -c Paris -C France -f svg               # vector, infinite zoom
  maptoposter-cli -c Paris -C France -f pdf -p A3         # print-ready PDF
  maptoposter-cli -c Paris -C France --output-dir prints/  # custom output directory

Custom coordinates (decimal or DMS):
  maptoposter-cli -c "New York" -C USA -lat 40.7128 -long -74.0060
  maptoposter-cli -c "Paris" -C France -lat "48d51m24s" -long "2d21m8s"

Multilingual (non-Latin scripts):
  maptoposter-cli -c Tokyo -C Japan -dc "東京" -dC "日本" --font-family "Noto Sans JP"
  maptoposter-cli -c Dubai -C UAE -dc "دبي" -dC "الإمارات" --font-family "Cairo"
  maptoposter-cli -c Seoul -C "South Korea" -dc "서울" -dC "대한민국" --font-family "Noto Sans KR"

Batch processing & gallery:
  maptoposter-cli --batch cities.csv                       # CSV with city,country columns
  maptoposter-cli --batch cities.json --gallery            # JSON input + HTML gallery
  maptoposter-cli --batch cities.csv --dpi 150 --output-dir posters/

Parallel rendering (multiprocessing, since v0.5.0):
  maptoposter-cli -c Tokyo -C Japan --all-themes --parallel-themes
  maptoposter-cli --batch cities.csv --parallel --max-workers 8
  maptoposter-cli --batch cities.csv --parallel --parallel-themes --gallery

Config file (JSON or YAML — all CLI options as snake_case keys):
  maptoposter-cli --config poster.yaml
  maptoposter-cli --config poster.yaml --dpi 600    # CLI flags override config

Structured logging & metadata:
  maptoposter-cli -c Paris -C France --log-format json     # machine-readable events
  maptoposter-cli -c Paris -C France --debug               # verbose DEBUG output
  # Every poster generates a .json sidecar with coords, theme, DPI, timestamps

Utilities:
  maptoposter-cli --list-themes                      # show all 17 themes
  maptoposter-cli -c London -C UK --dry-run          # preview without generating
  maptoposter-cli --cache-info                       # show cache statistics
  maptoposter-cli --cache-clear                      # delete cached OSM data

Docker:
  docker run --rm -v "$PWD/posters:/home/maptoposter/posters" \\
    ghcr.io/efrenpy/maptoposter -c "Paris" -C "France"

Paper sizes: A0 (33.1x46.8"), A1 (23.4x33.1"), A2 (16.5x23.4"),
             A3 (11.7x16.5"), A4 (8.3x11.7"). Use -o landscape to flip.

DPI guide:  72 (screen) | 150 (draft) | 300 (print) | 600 (pro) | 1200 (archival)
            Auto-reduced if memory would exceed 2 GB. Capped at 300 for PDF/SVG.

Distance guide:
   3000-6000m   Small/dense (Venice canals, Amsterdam center, old medinas)
   8000-12000m  Medium cities (Paris boulevards, Barcelona Eixample)
  15000-20000m  Large metros (Tokyo, Mumbai, New York full view)
  Max: 100000m (100 km)

Environment variables:
  MAPTOPOSTER_OUTPUT_DIR       Output directory (default: posters/)
  MAPTOPOSTER_CACHE_DIR        OSM data cache (default: cache/)
  MAPTOPOSTER_THEMES_DIR       Custom themes directory
  MAPTOPOSTER_FONTS_DIR        Bundled font files directory
  MAPTOPOSTER_FONTS_CACHE      Google Fonts cache (~/.cache/maptoposter/fonts)
  MAPTOPOSTER_NOMINATIM_DELAY  Geocoding rate-limit in seconds (default: 1)

Themes (17 built-in):
  autumn, blueprint, contrast_zones, copper_patina, emerald, forest,
  gradient_roads, japanese_ink, midnight_blue, monochrome_blue,
  neon_cyberpunk, noir, ocean, pastel_dream, sunset, terracotta, warm_beige
        """,
    )


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    # -- Required (unless using --batch) --
    req = parser.add_argument_group("required arguments (unless using --batch)")
    req.add_argument("--city", "-c", type=str,
                     help="City name for geocoding (e.g., 'Paris', 'Tokyo')")
    req.add_argument("--country", "-C", type=str,
                     help="Country name for geocoding (e.g., 'France', 'Japan')")

    # -- Map & layout --
    layout = parser.add_argument_group("map & layout")
    layout.add_argument("--theme", "-t", type=str, default=None,
                        help="Color theme (default: terracotta). See --list-themes for all 17 options."
                             " Misspelled names are auto-corrected")
    layout.add_argument("--themes", nargs="+", default=None,
                        help="Generate multiple themes in one run (e.g., --themes noir terracotta sunset)")
    layout.add_argument("--all-themes", dest="all_themes", action="store_true",
                        help="Generate a poster for every available theme")
    layout.add_argument("--parallel-themes", dest="parallel_themes", action="store_true",
                        help="Render multiple themes in parallel using multiprocessing."
                             " Use with --themes or --all-themes for best effect")
    layout.add_argument("--distance", "-d", type=int, default=None,
                        help="Map radius in meters (default: 18000, max: 100000)."
                             " 3000-6000 for small cities, 15000-20000 for large metros")
    layout.add_argument("--width", "-W", type=float, default=None,
                        help="Poster width in inches (default: 12, max: 20)")
    layout.add_argument("--height", "-H", type=float, default=None,
                        help="Poster height in inches (default: 16, max: 20)")
    layout.add_argument("--paper-size", "-p", type=str, choices=["A0", "A1", "A2", "A3", "A4"],
                        help="Standard paper size (overrides --width/--height)")
    layout.add_argument("--orientation", "-o", type=str, choices=["portrait", "landscape"],
                        default=None, help="Paper orientation (default: portrait)")
    layout.add_argument("--latitude", "-lat", dest="latitude", type=str,
                        help="Override latitude (decimal or DMS, e.g., '48.8566' or '48d51m24s')")
    layout.add_argument("--longitude", "-long", dest="longitude", type=str,
                        help="Override longitude (decimal or DMS, e.g., '2.3522' or '2d21m8s')")

    # -- Output --
    out = parser.add_argument_group("output")
    out.add_argument("--format", "-f", default=None, choices=["png", "svg", "pdf"],
                     help="Output format (default: png). SVG/PDF are vector and scale to any size")
    out.add_argument("--dpi", type=int, default=None,
                     help="Resolution in dots per inch (default: 300). Auto-reduced if memory"
                          " would exceed 2 GB. Capped at 300 for vector formats (PDF/SVG)")
    out.add_argument("--output-dir", type=str,
                     help="Destination directory for posters and metadata sidecar files"
                          " (default: posters/ or $MAPTOPOSTER_OUTPUT_DIR)")
    out.add_argument("--no-attribution", dest="no_attribution", action="store_true",
                     help="Hide the OpenStreetMap attribution text on the poster")

    # -- Multilingual (i18n) --
    i18n = parser.add_argument_group("multilingual support")
    i18n.add_argument("--display-city", "-dc", type=str,
                      help="Custom city text on poster, e.g., '東京' for Tokyo in Japanese")
    i18n.add_argument("--display-country", "-dC", type=str,
                      help="Custom country text on poster, e.g., '日本' for Japan in Japanese")
    i18n.add_argument("--country-label", dest="country_label", type=str,
                      help="Override country text displayed (alias for --display-country)")
    i18n.add_argument("--font-family", type=str,
                      help="Google Fonts family name for non-Latin scripts (e.g., 'Noto Sans JP',"
                           " 'Cairo', 'Noto Sans KR'). Auto-downloaded and cached locally."
                           " Defaults to bundled Roboto")

    # -- Batch & gallery --
    batch_grp = parser.add_argument_group("batch processing & gallery")
    batch_grp.add_argument("--batch", type=str,
                           help="CSV or JSON file with multiple cities. CSV columns: city, country,"
                                " theme, distance, dpi, width, height, format, display_city,"
                                " display_country, font_family. Does not require --city/--country")
    batch_grp.add_argument("--parallel", action="store_true",
                           help="Process batch cities in parallel using multiprocessing."
                                " Combine with --parallel-themes for maximum throughput")
    batch_grp.add_argument("--max-workers", type=int, default=4,
                           help="Maximum number of parallel workers for batch processing (default: 4)")
    batch_grp.add_argument("--gallery", action="store_true",
                           help="Generate a self-contained HTML gallery page (index.html)"
                                " with CSS grid layout and metadata cards")

    # -- Config & logging --
    config_grp = parser.add_argument_group("configuration & logging")
    config_grp.add_argument("--config", type=str,
                            help="JSON or YAML config file. All CLI options can be set as"
                                 " snake_case keys. CLI flags override config values")
    config_grp.add_argument("--log-format", type=str, choices=["text", "json"], default="text",
                            help="Output format for status events (default: text)."
                                 " Use 'json' for machine-readable newline-delimited JSON")
    config_grp.add_argument("--debug", action="store_true",
                            help="Enable verbose DEBUG-level logging output")

    # -- Utilities --
    util = parser.add_argument_group("utilities")
    util.add_argument("--list-themes", action="store_true",
                      help="List all 17 built-in themes with descriptions and exit")
    util.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="Preview configuration, coordinates, dimensions, and estimated"
                           " file size without generating any posters")
    util.add_argument("--cache-info", dest="cache_info", action="store_true",
                      help="Show cache statistics (file count, total size, TTL) and exit")
    util.add_argument("--cache-clear", dest="cache_clear", action="store_true",
                      help="Delete all cached OSM data, coordinates, and fonts, then exit")
    util.add_argument("--version", action="version", version=f"%(prog)s {__version__}")


def _load_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file '{path}' not found")
    file_size = path.stat().st_size
    if file_size > MAX_INPUT_FILE_SIZE:
        raise ValueError(
            f"Config file '{path}' is too large ({file_size} bytes, max {MAX_INPUT_FILE_SIZE})"
        )
    text = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text or "{}")
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ValueError(f"Config file '{path}' has invalid syntax: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON/YAML object")
    return data


def _normalize_config_data(raw: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in raw if k not in OPTION_FIELD_NAMES
               and k not in CONFIG_KEY_ALIASES and k != "no_attribution"]
    if unknown:
        warnings.warn(
            f"Unknown config keys ignored: {', '.join(sorted(unknown))}",
            stacklevel=2,
        )
    _INT_FIELDS = {"distance", "dpi"}
    _FLOAT_FIELDS = {"width", "height", "latitude", "longitude"}
    _STR_FIELDS = {"city", "country", "display_city", "display_country",
                   "country_label", "font_family", "theme", "output_format",
                   "paper_size", "orientation", "output_dir"}

    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "no_attribution":
            normalized["show_attribution"] = not bool(value)
            continue
        target = CONFIG_KEY_ALIASES.get(key, key)
        if target not in OPTION_FIELD_NAMES:
            continue
        if target in {"latitude", "longitude"} and isinstance(value, str):
            normalized[target] = parse(value)
        elif target == "themes" and isinstance(value, str):
            normalized[target] = [value]
        elif target in _INT_FIELDS and isinstance(value, str):
            normalized[target] = int(value)
        elif target in _FLOAT_FIELDS and isinstance(value, str):
            normalized[target] = float(value)
        elif target in _STR_FIELDS and not isinstance(value, str) and value is not None:
            normalized[target] = str(value)
        else:
            normalized[target] = value
    return normalized


def _collect_cli_overrides(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict[str, Any]:
    """Collect CLI flags that the user explicitly passed (non-None values).

    All overridable arguments default to ``None`` in argparse, so a non-None
    value means the user explicitly provided it.  This avoids the classic
    problem where ``--distance 18000`` (the dataclass default) would be
    indistinguishable from "the user didn't pass --distance."
    """
    overrides: dict[str, Any] = {}
    for cli_name, option_field in CLI_TO_OPTION_FIELD.items():
        value = getattr(args, cli_name, None)
        if cli_name == "themes":
            if value:
                overrides[option_field] = value
            continue
        if cli_name == "no_attribution":
            if value:
                overrides["show_attribution"] = not value
            continue
        if cli_name == "parallel_themes":
            if value:
                overrides["parallel_themes"] = True
            continue
        if value is not None:
            overrides[option_field] = value
    return overrides


def _build_options_from_sources(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> PosterGenerationOptions:
    config_data: dict[str, Any] = {}
    if args.config:
        config_data = _normalize_config_data(_load_config_file(Path(args.config)))
    overrides = _collect_cli_overrides(parser, args)
    merged = {**config_data, **overrides}
    missing = [field for field in ("city", "country") if field not in merged]
    if missing:
        raise ValueError("--city/--country (or config equivalents) are required")
    return PosterGenerationOptions(**merged)


def _parse_coordinates(value: str | None) -> float | None:
    """Parse a coordinate string to float using lat-lon-parser.

    Accepts decimal degrees (``"48.8566"``) and DMS notation
    (``"48°51'N"``).  Returns ``None`` when *value* is ``None``.
    """
    if value is None:
        return None
    return parse(value)


def _handle_dry_run(options: PosterGenerationOptions) -> int:
    """Print a configuration summary and estimated output size without generating."""
    reporter = StatusReporter(json_mode=False)
    width, height = _apply_paper_size(
        options.width, options.height, options.paper_size, options.orientation, reporter,
    )
    dpi = _validate_dpi(options.dpi, reporter)
    available = get_available_themes()
    from .core import _resolve_theme_names
    themes = _resolve_theme_names(options, available)
    coords = _resolve_coordinates(options, reporter)

    px_w, px_h = int(width * dpi), int(height * dpi)

    fmt = options.output_format
    print("\n--- Dry Run Summary ---")
    print(f"City:        {options.city}")
    print(f"Country:     {options.country}")
    print(f"Coordinates: {coords[0]:.4f}, {coords[1]:.4f}")
    print(f"Distance:    {options.distance} m")
    print(f"Size:        {width}\" x {height}\" @ {dpi} DPI ({px_w} x {px_h} px)")
    print(f"Format:      {fmt}")
    print(f"Themes:      {', '.join(themes)}")
    if fmt in ("svg", "pdf"):
        print("Est. size:   (varies for vector formats)")
    else:
        # Estimate PNG size: ~4 bytes/pixel (RGBA) compressed ~10:1
        estimated_bytes = int(px_w * px_h * 4 / 10)
        if estimated_bytes > 1_048_576:
            size_str = f"{estimated_bytes / 1_048_576:.1f} MB"
        else:
            size_str = f"{estimated_bytes / 1024:.0f} KB"
        print(f"Est. size:   ~{size_str} per poster")
    print("--- No posters generated ---\n")
    return 0


def _should_show_examples(argv: Sequence[str] | None) -> bool:
    return (argv is None and len(sys.argv) == 1) or (argv is not None and len(argv) == 0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    _add_arguments(parser)
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if getattr(args, "debug", False) else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if _should_show_examples(argv):
        print_examples()
        return 0

    if args.list_themes:
        list_themes()
        return 0

    if getattr(args, "cache_clear", False):
        from .core import cache_clear
        count = cache_clear()
        if count:
            print(f"Cleared {count} cache files.")
        else:
            print("Cache is already empty.")
        return 0

    if getattr(args, "cache_info", False):
        from .core import cache_info
        info = cache_info()
        print(f"Cache files: {info['total_files']}")
        print(f"Total size:  {info['total_bytes'] / 1024:.1f} KB")
        for entry in info["entries"]:
            ttl_str = f", TTL={entry['ttl']}s" if entry.get("ttl") else ""
            print(f"  {entry['key']} ({entry['size_bytes']} bytes{ttl_str})")
        return 0

    args.latitude = _parse_coordinates(args.latitude)
    args.longitude = _parse_coordinates(args.longitude)

    reporter = StatusReporter(
        json_mode=args.log_format == "json",
        debug=getattr(args, "debug", False),
    )

    # Batch mode does not require --city/--country; handle before option parsing
    if getattr(args, "batch", None):
        from .batch import run_batch
        overrides = _collect_cli_overrides(parser, args)
        if "city" in overrides or "country" in overrides:
            print("Note: --city/--country are ignored in batch mode (each batch entry defines its own).")
        if overrides.get("all_themes"):
            print("Note: --all-themes applies to every batch entry; per-entry theme fields will be ignored.")
        overrides.pop("city", None)
        overrides.pop("country", None)
        result = run_batch(
            Path(args.batch),
            global_overrides=overrides,
            status_reporter=reporter,
            dry_run=getattr(args, "dry_run", False),
            parallel=getattr(args, "parallel", False),
            max_workers=getattr(args, "max_workers", 4),
        )
        gallery_outputs = result.get("successes", [])
        if getattr(args, "gallery", False) and gallery_outputs:
            from .gallery import generate_gallery
            output_dir = str(Path(gallery_outputs[0]).parent) if gallery_outputs else "posters"
            gallery_path = generate_gallery(output_dir)
            print(f"Gallery: {gallery_path}")
        return 1 if result["failures"] else 0

    try:
        options = _build_options_from_sources(parser, args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"✗ {exc}\n")
        if not args.config:
            print_examples()
        return 1

    if getattr(args, "dry_run", False):
        return _handle_dry_run(options)

    try:
        outputs = generate_posters(options, status_reporter=reporter)
    except ValueError as exc:
        print(f"\n✗ Configuration error: {exc}")
        return 1
    except Exception as exc:  # pragma: no cover - surface fatal errors to users
        print(f"\n✗ Error: {exc}")
        traceback.print_exc()
        return 1

    if getattr(args, "gallery", False) and outputs:
        from .gallery import generate_gallery
        output_dir = str(Path(outputs[0]).parent) if outputs else "posters"
        gallery_path = generate_gallery(output_dir)
        print(f"Gallery: {gallery_path}")

    return 0


def _entry() -> None:
    """Console script entry point (ensures exit code is propagated)."""
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    _entry()
