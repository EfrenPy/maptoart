"""End-to-end integration test with real matplotlib rendering."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

import maptoposter.core as core


@pytest.mark.integration
class TestIntegration:
    """Integration tests that exercise real matplotlib rendering."""

    @patch("maptoposter.core.fetch_features", return_value=None)
    @patch("maptoposter.core.fetch_graph")
    @patch("maptoposter.core.get_coordinates", return_value=(48.8566, 2.3522))
    def test_create_poster_png_output(
        self,
        mock_coords: MagicMock,
        mock_graph: MagicMock,
        mock_features: MagicMock,
        tmp_path: Path,
        sample_theme: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        """Full pipeline with mocked network but real matplotlib."""
        # Build a small synthetic graph
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
        mock_graph.return_value = g

        output_file = str(tmp_path / "paris_test.png")
        core.create_poster(
            "Paris", "France", (48.8566, 2.3522), 5000,
            output_file, "png",
            theme=sample_theme,
            width=6, height=8, dpi=72,
            status_reporter=silent_reporter,
        )

        result = Path(output_file)
        assert result.exists(), "PNG file was not created"
        assert result.stat().st_size > 0, "PNG file is empty"
        header = result.read_bytes()[:4]
        assert header == b"\x89PNG", f"Invalid PNG header: {header!r}"
