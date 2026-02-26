"""Performance regression test for poster rendering."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import maptoposter.core as core

from conftest import build_synthetic_graph


@pytest.mark.integration
class TestRenderPerformance:
    """Performance regression tests."""

    @patch("maptoposter.core.fetch_features", return_value=None)
    @patch("maptoposter.core.fetch_graph")
    def test_render_completes_within_budget(
        self,
        mock_graph: MagicMock,
        mock_features: MagicMock,
        tmp_path: Path,
        sample_theme: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        """Synthetic render should complete within 30s."""
        mock_graph.return_value = build_synthetic_graph()

        output_file = str(tmp_path / "perf_test.png")
        start = time.monotonic()
        core.create_poster(
            "Paris", "France", (48.8566, 2.3522), 5000,
            output_file, "png",
            theme=sample_theme,
            width=6, height=8, dpi=72,
            status_reporter=silent_reporter,
        )
        elapsed = time.monotonic() - start

        assert Path(output_file).exists(), "Output file was not created"
        assert elapsed < 30, f"Render took {elapsed:.1f}s, expected <30s"
