"""Command-line entry point for the City Map Poster Generator."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import fields
from pathlib import Path
from typing import Any, Sequence

from lat_lon_parser import parse
import yaml

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

_MAX_CONFIG_SIZE = 1_048_576  # 1 MB

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
}
CONFIG_KEY_ALIASES = {
    "format": "output_format",
}


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Generate beautiful map posters for any city",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  maptoposter-cli --city "New York" --country "USA"
  maptoposter-cli --city "Paris" --country "France" --theme noir --distance 15000
  maptoposter-cli --city Tokyo --country Japan --all-themes
  maptoposter-cli --city London --country UK --dry-run
  maptoposter-cli --list-themes
        """,
    )


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=str,
        help="Path to a JSON/YAML config file with default options",
    )
    parser.add_argument("--city", "-c", type=str, help="City name")
    parser.add_argument("--country", "-C", type=str, help="Country name")
    parser.add_argument(
        "--latitude",
        "-lat",
        dest="latitude",
        type=str,
        help="Override latitude center point",
    )
    parser.add_argument(
        "--longitude",
        "-long",
        dest="longitude",
        type=str,
        help="Override longitude center point",
    )
    parser.add_argument(
        "--country-label",
        dest="country_label",
        type=str,
        help="Override country text displayed on poster",
    )
    parser.add_argument(
        "--theme",
        "-t",
        type=str,
        default="terracotta",
        help="Theme name (default: terracotta). Use --list-themes to see all options",
    )
    parser.add_argument(
        "--themes",
        nargs="+",
        default=None,
        help="List of theme names to generate (overrides --theme)",
    )
    parser.add_argument(
        "--all-themes",
        dest="all_themes",
        action="store_true",
        help="Generate posters for all themes",
    )
    parser.add_argument(
        "--distance",
        "-d",
        type=int,
        default=18000,
        help="Map radius in meters (default: 18000, max: 100000)",
    )
    parser.add_argument(
        "--width",
        "-W",
        type=float,
        default=12,
        help="Image width in inches (default: 12, max: 20)",
    )
    parser.add_argument(
        "--height",
        "-H",
        type=float,
        default=16,
        help="Image height in inches (default: 16, max: 20)",
    )
    parser.add_argument(
        "--list-themes",
        action="store_true",
        help="List all available themes",
    )
    parser.add_argument(
        "--display-city",
        "-dc",
        type=str,
        help="Custom display name for city (for i18n support)",
    )
    parser.add_argument(
        "--display-country",
        "-dC",
        type=str,
        help="Custom display name for country (for i18n support)",
    )
    parser.add_argument(
        "--font-family",
        type=str,
        help='Google Fonts family name (e.g., "Noto Sans JP", "Open Sans"). If not specified, uses local Roboto fonts.',
    )
    parser.add_argument(
        "--format",
        "-f",
        default="png",
        choices=["png", "svg", "pdf"],
        help="Output format for the poster (default: png)",
    )
    parser.add_argument(
        "--no-attribution",
        dest="no_attribution",
        action="store_true",
        help="Hide the OpenStreetMap attribution text",
    )
    parser.add_argument(
        "--paper-size",
        "-p",
        type=str,
        choices=["A0", "A1", "A2", "A3", "A4"],
        help="Paper size preset (overrides --width and --height)",
    )
    parser.add_argument(
        "--orientation",
        "-o",
        type=str,
        choices=["portrait", "landscape"],
        default="portrait",
        help="Paper orientation (default: portrait)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Output DPI (default: 300). Typical values: 72 (screen), 150 (draft), 300 (print)."
             " Capped at 300 for vector formats (PDF, SVG)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Destination directory for posters and metadata (overrides env)",
    )
    parser.add_argument(
        "--log-format",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Status log format (text for humans, json for automation)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output (verbose diagnostics)",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show configuration summary and estimated output size without generating posters",
    )


def _load_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file '{path}' not found")
    file_size = path.stat().st_size
    if file_size > _MAX_CONFIG_SIZE:
        raise ValueError(
            f"Config file '{path}' is too large ({file_size} bytes, max {_MAX_CONFIG_SIZE})"
        )
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text or "{}")
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON/YAML object")
    return data


def _normalize_config_data(raw: dict[str, Any]) -> dict[str, Any]:
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
        else:
            normalized[target] = value
    return normalized


def _collect_cli_overrides(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for cli_name, option_field in CLI_TO_OPTION_FIELD.items():
        value = getattr(args, cli_name, None)
        default = parser.get_default(cli_name)
        if cli_name == "themes":
            if value:
                overrides[option_field] = value
            continue
        if cli_name == "no_attribution":
            if value != default:
                overrides["show_attribution"] = not value
            continue
        if value is not None and value != default:
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
    # Estimate PNG size: ~4 bytes/pixel (RGBA) compressed ~10:1
    estimated_bytes = int(px_w * px_h * 4 / 10)
    if estimated_bytes > 1_048_576:
        size_str = f"{estimated_bytes / 1_048_576:.1f} MB"
    else:
        size_str = f"{estimated_bytes / 1024:.0f} KB"

    print("\n--- Dry Run Summary ---")
    print(f"City:        {options.city}")
    print(f"Country:     {options.country}")
    print(f"Coordinates: {coords[0]:.4f}, {coords[1]:.4f}")
    print(f"Distance:    {options.distance} m")
    print(f"Size:        {width}\" x {height}\" @ {dpi} DPI ({px_w} x {px_h} px)")
    print(f"Format:      {options.output_format}")
    print(f"Themes:      {', '.join(themes)}")
    print(f"Est. size:   ~{size_str} per poster (PNG)")
    print("--- No posters generated ---\n")
    return 0


def _should_show_examples(argv: Sequence[str] | None) -> bool:
    return (argv is None and len(sys.argv) == 1) or (argv is not None and len(argv) == 0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    _add_arguments(parser)
    args = parser.parse_args(argv)

    if _should_show_examples(argv):
        print_examples()
        return 0

    if args.list_themes:
        list_themes()
        return 0

    args.latitude = _parse_coordinates(args.latitude)
    args.longitude = _parse_coordinates(args.longitude)

    try:
        options = _build_options_from_sources(parser, args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"✗ {exc}\n")
        if not args.config:
            print_examples()
        return 1

    if getattr(args, "dry_run", False):
        return _handle_dry_run(options)

    reporter = StatusReporter(
        json_mode=args.log_format == "json",
        debug=getattr(args, "debug", False),
    )

    try:
        generate_posters(options, status_reporter=reporter)
    except ValueError as exc:
        print(f"\n✗ Configuration error: {exc}")
        return 1
    except Exception as exc:  # pragma: no cover - surface fatal errors to users
        print(f"\n✗ Error: {exc}")
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
