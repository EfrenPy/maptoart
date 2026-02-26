"""End-to-end integration test with real matplotlib rendering."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import maptoposter.core as core

from conftest import build_synthetic_graph


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
        mock_graph.return_value = build_synthetic_graph()

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

    @patch("maptoposter.core.fetch_features", return_value=None)
    @patch("maptoposter.core.fetch_graph")
    def test_create_poster_svg_output(
        self,
        mock_graph: MagicMock,
        mock_features: MagicMock,
        tmp_path: Path,
        sample_theme: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        """SVG output should contain valid SVG/XML header."""
        mock_graph.return_value = build_synthetic_graph()

        output_file = str(tmp_path / "paris_test.svg")
        core.create_poster(
            "Paris", "France", (48.8566, 2.3522), 5000,
            output_file, "svg",
            theme=sample_theme,
            width=6, height=8, dpi=72,
            status_reporter=silent_reporter,
        )

        result = Path(output_file)
        assert result.exists(), "SVG file was not created"
        content = result.read_text(encoding="utf-8")
        assert "<?xml" in content or "<svg" in content, "Not a valid SVG file"

    @patch("maptoposter.core.fetch_features", return_value=None)
    @patch("maptoposter.core.fetch_graph")
    def test_create_poster_pdf_output(
        self,
        mock_graph: MagicMock,
        mock_features: MagicMock,
        tmp_path: Path,
        sample_theme: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        """PDF output should contain %PDF- header."""
        mock_graph.return_value = build_synthetic_graph()

        output_file = str(tmp_path / "paris_test.pdf")
        core.create_poster(
            "Paris", "France", (48.8566, 2.3522), 5000,
            output_file, "pdf",
            theme=sample_theme,
            width=6, height=8, dpi=72,
            status_reporter=silent_reporter,
        )

        result = Path(output_file)
        assert result.exists(), "PDF file was not created"
        header = result.read_bytes()[:5]
        assert header == b"%PDF-", f"Invalid PDF header: {header!r}"

    @patch("maptoposter.core.fetch_features", return_value=None)
    @patch("maptoposter.core.fetch_graph")
    def test_vector_format_dpi_capped(
        self,
        mock_graph: MagicMock,
        mock_features: MagicMock,
        tmp_path: Path,
        sample_theme: dict[str, str],
    ) -> None:
        """SVG with DPI=600 should emit dpi_cap event."""
        mock_graph.return_value = build_synthetic_graph()

        events: list[str] = []

        class _TrackingReporter(core.StatusReporter):
            def __init__(self):
                super().__init__(json_mode=True)

            def emit(self, event, message=None, **extra):
                events.append(event)

        output_file = str(tmp_path / "paris_capped.svg")
        core.create_poster(
            "Paris", "France", (48.8566, 2.3522), 5000,
            output_file, "svg",
            theme=sample_theme,
            width=6, height=8, dpi=600,
            status_reporter=_TrackingReporter(),
        )

        assert "poster.save.dpi_cap" in events
