"""Tests for HTML gallery generation."""

from __future__ import annotations

import json
from pathlib import Path

from maptoart.gallery import generate_gallery


class TestGenerateGallery:
    """Tests for generate_gallery."""

    def test_generates_html_with_images(self, tmp_path: Path) -> None:
        # Create mock poster files
        (tmp_path / "paris_terracotta_20260225.png").write_bytes(b"\x89PNG fake")
        (tmp_path / "paris_terracotta_20260225.json").write_text(
            json.dumps({"city": "Paris", "country": "France", "theme": "terracotta"})
        )
        (tmp_path / "tokyo_noir_20260225.png").write_bytes(b"\x89PNG fake")
        (tmp_path / "tokyo_noir_20260225.json").write_text(
            json.dumps({"city": "Tokyo", "country": "Japan", "theme": "noir"})
        )

        result = generate_gallery(str(tmp_path))
        html = Path(result).read_text(encoding="utf-8")
        assert "<img" in html
        assert "Paris" in html
        assert "Tokyo" in html
        assert "gallery" in html

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = generate_gallery(str(tmp_path))
        html = Path(result).read_text(encoding="utf-8")
        assert "No posters found" in html

    def test_xss_escaped_in_metadata(self, tmp_path: Path) -> None:
        """Metadata with script tags should be HTML-escaped (#2)."""
        (tmp_path / "xss_test_20260225.png").write_bytes(b"\x89PNG fake")
        (tmp_path / "xss_test_20260225.json").write_text(
            json.dumps({
                "city": '<script>alert(1)</script>',
                "country": '<img onerror="xss">',
                "theme": '"><script>x</script>',
            })
        )

        result = generate_gallery(str(tmp_path))
        output = Path(result).read_text(encoding="utf-8")
        assert "<script>" not in output
        assert "&lt;script&gt;" in output
        # onerror attribute should be escaped, not raw
        assert 'onerror="xss"' not in output

    def test_pdf_card_rendering(self, tmp_path: Path) -> None:
        """PDF files should show a placeholder card (#13)."""
        (tmp_path / "paris_terracotta_20260225.pdf").write_bytes(b"%PDF fake")
        (tmp_path / "paris_terracotta_20260225.json").write_text(
            json.dumps({"city": "Paris", "country": "France", "theme": "terracotta"})
        )

        result = generate_gallery(str(tmp_path))
        html = Path(result).read_text(encoding="utf-8")
        assert "<img" not in html  # No <img> tag for PDFs
        assert "Paris" in html

    def test_missing_metadata_uses_stem(self, tmp_path: Path) -> None:
        """Without a JSON sidecar, the title should be derived from the filename (#13)."""
        (tmp_path / "london_noir_20260225.png").write_bytes(b"\x89PNG fake")
        # No .json sidecar

        result = generate_gallery(str(tmp_path))
        html = Path(result).read_text(encoding="utf-8")
        assert "London" in html  # Derived from stem

    def test_corrupt_metadata_json_falls_back(self, tmp_path: Path) -> None:
        """Corrupt JSON sidecar should not crash gallery; falls back to stem (#R16-4)."""
        (tmp_path / "rome_warm_20260225.png").write_bytes(b"\x89PNG fake")
        (tmp_path / "rome_warm_20260225.json").write_text("NOT VALID JSON {{{")

        result = generate_gallery(str(tmp_path))
        html = Path(result).read_text(encoding="utf-8")
        assert "Rome" in html  # Falls back to stem-derived title
        assert "<img" in html
