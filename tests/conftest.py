"""Shared test fixtures for the maptoart test suite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import networkx as nx
import pytest

import maptoart.core as core
from maptoart.core import PosterGenerationOptions


@pytest.fixture(autouse=True)
def _clear_theme_cache() -> Iterator[None]:
    """Clear the theme cache before every test to prevent cross-test pollution."""
    with core._theme_cache_lock:
        core._theme_cache.clear()
    core._get_available_themes_cached.cache_clear()
    yield
    with core._theme_cache_lock:
        core._theme_cache.clear()
    core._get_available_themes_cached.cache_clear()


@pytest.fixture(autouse=True)
def _patch_geocoding_sleep() -> Iterator[None]:
    """Eliminate the 1-second Nominatim rate-limit delay in all tests (#14)."""
    with patch("maptoart.geocoding.time.sleep"):
        with patch("tenacity.nap.time.sleep"):
            yield


@pytest.fixture
def silent_reporter() -> core.StatusReporter:
    class _SilentReporter(core.StatusReporter):
        def __init__(self) -> None:
            super().__init__(json_mode=True)

        def emit(self, event: str, message: str | None = None, **extra: Any) -> None:
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


def build_synthetic_graph() -> nx.MultiDiGraph:
    """Build a small synthetic graph for integration/performance tests."""
    g = nx.MultiDiGraph()
    g.graph["crs"] = "EPSG:4326"
    nodes = [
        (1, {"x": 2.3500, "y": 48.8550}),
        (2, {"x": 2.3510, "y": 48.8560}),
        (3, {"x": 2.3520, "y": 48.8570}),
        (4, {"x": 2.3530, "y": 48.8580}),
        (5, {"x": 2.3540, "y": 48.8590}),
    ]
    g.add_nodes_from(nodes)
    g.add_edge(1, 2, highway="primary", length=100)
    g.add_edge(2, 3, highway="secondary", length=100)
    g.add_edge(3, 4, highway="residential", length=100)
    g.add_edge(4, 5, highway="residential", length=100)
    return g
