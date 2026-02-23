"""Tests for CLI configuration merging and overrides."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from maptoposter import cli


def _build_parser() -> argparse.ArgumentParser:
    parser = cli._build_parser()
    cli._add_arguments(parser)
    return parser


def _prepare_args(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace:
    args = parser.parse_args(argv)
    args.latitude = cli._parse_coordinates(args.latitude)
    args.longitude = cli._parse_coordinates(args.longitude)
    return args


def test_build_options_from_config(tmp_path: Path) -> None:
    config = {
        "city": "Paris",
        "country": "France",
        "themes": ["terracotta", "neon_cyberpunk"],
        "distance": 9000,
        "width": 10,
        "height": 14,
        "output_dir": str(tmp_path / "out"),
    }
    cfg = tmp_path / "poster.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")

    parser = _build_parser()
    args = _prepare_args(parser, ["--config", str(cfg)])
    options = cli._build_options_from_sources(parser, args)

    assert options.city == "Paris"
    assert options.country == "France"
    assert options.themes == config["themes"]
    assert options.distance == 9000
    assert options.width == 10
    assert options.output_dir.endswith("out")


def test_cli_overrides_config_values(tmp_path: Path) -> None:
    config = {
        "city": "Paris",
        "country": "France",
        "width": 12,
        "height": 18,
        "no_attribution": True,
    }
    cfg = tmp_path / "poster.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")

    parser = _build_parser()
    args = _prepare_args(
        parser,
        [
            "--config",
            str(cfg),
            "--width",
            "15",
            "--themes",
            "emerald",
            "forest",
        ],
    )
    options = cli._build_options_from_sources(parser, args)

    assert options.width == 15  # CLI override
    assert options.height == 18  # config preserved
    assert options.themes == ["emerald", "forest"]
    assert options.show_attribution is False  # config no_attribution


def test_config_parses_lat_lon_strings(tmp_path: Path) -> None:
    config = {
        "city": "Paris",
        "country": "France",
        "latitude": "48.8566",
        "longitude": "2.3522",
    }
    cfg = tmp_path / "poster.yaml"
    cfg.write_text(json.dumps(config), encoding="utf-8")

    parser = _build_parser()
    args = _prepare_args(parser, ["--config", str(cfg)])
    options = cli._build_options_from_sources(parser, args)

    assert options.latitude == pytest.approx(48.8566)
    assert options.longitude == pytest.approx(2.3522)
