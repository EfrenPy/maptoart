"""Unit tests for lightweight helpers in maptoposter.core."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import networkx as nx
import pytest

import maptoposter.core as core
from maptoposter.core import PosterGenerationOptions


@pytest.fixture
def silent_reporter() -> core.StatusReporter:
    class _SilentReporter(core.StatusReporter):
        def __init__(self) -> None:
            super().__init__(json_mode=True)

        def emit(self, event: str, message: str | None = None, **extra):  # type: ignore[override]
            return None

    return _SilentReporter()


@pytest.fixture
def sample_theme_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Provide a temporary directory for theme JSON files."""

    monkeypatch.setattr(core, "THEMES_DIR", tmp_path)
    yield tmp_path


@pytest.fixture
def sample_theme(sample_theme_dir: Path) -> dict[str, str]:
    theme_data = {
        "name": "Custom",
        "description": "Test palette",
        "bg": "#111111",
        "text": "#eeeeee",
        "gradient_color": "#222222",
        "water": "#0000ff",
        "parks": "#00ff00",
        "road_motorway": "#ff0000",
        "road_primary": "#ff8800",
        "road_secondary": "#ffff00",
        "road_tertiary": "#00ffff",
        "road_residential": "#ffffff",
        "road_default": "#888888",
    }
    (sample_theme_dir / "custom.json").write_text(
        json.dumps(theme_data), encoding=core.FILE_ENCODING
    )
    return theme_data


@pytest.fixture
def sample_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    graph.add_edge("a", "b", highway="motorway")
    graph.add_edge("b", "c", highway="primary")
    graph.add_edge("c", "d", highway="secondary")
    graph.add_edge("d", "e", highway="tertiary")
    graph.add_edge("e", "f", highway="residential")
    graph.add_edge("f", "g", highway="service")  # falls back to default
    return graph


def test_is_latin_script_handles_latin_and_non_latin() -> None:
    assert core.is_latin_script("Paris") is True
    assert core.is_latin_script("东京") is False


def test_load_theme_from_json(sample_theme: dict[str, str]) -> None:
    loaded = core.load_theme("custom")
    assert loaded["name"] == sample_theme["name"]
    assert loaded["bg"] == sample_theme["bg"]


def test_load_theme_missing_returns_fallback() -> None:
    fallback = core.load_theme("does_not_exist")
    assert fallback["name"] == "Terracotta"
    assert fallback["bg"].startswith("#")


def test_resolve_coordinates_prefers_overrides(
    monkeypatch: pytest.MonkeyPatch,
    silent_reporter: core.StatusReporter,
) -> None:
    options = PosterGenerationOptions(
        city="Paris",
        country="France",
        latitude=10.0,
        longitude=20.0,
    )

    def fake_get_coordinates(*_args, **_kwargs):  # pragma: no cover - should not run
        raise AssertionError("get_coordinates should not be called when overrides provided")

    monkeypatch.setattr(core, "get_coordinates", fake_get_coordinates)
    coords = core._resolve_coordinates(options, silent_reporter)
    assert coords == (10.0, 20.0)


def test_resolve_coordinates_calls_geocode(
    monkeypatch: pytest.MonkeyPatch,
    silent_reporter: core.StatusReporter,
) -> None:
    options = PosterGenerationOptions(city="Paris", country="France")

    def fake_get_coordinates(city: str, country: str, *, status_reporter=None):  # noqa: ARG001
        assert (city, country) == ("Paris", "France")
        return (1.23, 4.56)

    monkeypatch.setattr(core, "get_coordinates", fake_get_coordinates)
    coords = core._resolve_coordinates(options, silent_reporter)
    assert coords == (1.23, 4.56)


def test_resolve_theme_names_honors_custom_list() -> None:
    options = PosterGenerationOptions(city="Paris", country="France", themes=["a", "b"])
    result = core._resolve_theme_names(options, ["a", "b", "c"])
    assert result == ["a", "b"]


def test_get_edge_colors_by_type_uses_theme_palette(
    sample_graph: nx.MultiDiGraph,
    sample_theme: dict[str, str],
) -> None:
    colors = core.get_edge_colors_by_type(sample_graph, sample_theme)
    assert colors == [
        sample_theme["road_motorway"],
        sample_theme["road_primary"],
        sample_theme["road_secondary"],
        sample_theme["road_tertiary"],
        sample_theme["road_residential"],
        sample_theme["road_default"],
    ]
