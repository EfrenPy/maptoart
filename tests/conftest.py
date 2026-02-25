"""Shared test fixtures for the maptoposter test suite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import networkx as nx
import pytest

import maptoposter.core as core
from maptoposter.core import PosterGenerationOptions


@pytest.fixture(autouse=True)
def _clear_theme_cache() -> Iterator[None]:
    """Clear the theme cache before every test to prevent cross-test pollution."""
    with core._theme_cache_lock:
        core._theme_cache.clear()
    yield
    with core._theme_cache_lock:
        core._theme_cache.clear()


@pytest.fixture
def silent_reporter() -> core.StatusReporter:
    class _SilentReporter(core.StatusReporter):
        def __init__(self) -> None:
            super().__init__(json_mode=True)

        def emit(self, event: str, message: str | None = None, **extra):  # type: ignore[override]
            return None

    return _SilentReporter()


SAMPLE_THEME_DATA: dict[str, str] = {
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


@pytest.fixture
def sample_theme_data() -> dict[str, str]:
    return dict(SAMPLE_THEME_DATA)


@pytest.fixture
def sample_theme_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Provide a temporary directory for theme JSON files."""
    monkeypatch.setattr(core, "THEMES_DIR", tmp_path)
    yield tmp_path


@pytest.fixture
def sample_theme(sample_theme_dir: Path, sample_theme_data: dict[str, str]) -> dict[str, str]:
    (sample_theme_dir / "custom.json").write_text(
        json.dumps(sample_theme_data), encoding=core.FILE_ENCODING
    )
    return sample_theme_data


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


@pytest.fixture
def base_options() -> PosterGenerationOptions:
    return PosterGenerationOptions(city="Paris", country="France")
