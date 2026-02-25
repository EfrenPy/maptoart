"""Tests for font management (load_fonts, download_google_font)."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from maptoposter import font_management


class TestLoadFonts:
    """Tests for load_fonts()."""

    def test_default_roboto_when_available(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        for weight in ("Bold", "Regular", "Light"):
            (fonts_dir / f"Roboto-{weight}.ttf").write_bytes(b"fake")

        monkeypatch.setattr(font_management, "FONTS_DIR", fonts_dir)

        result = font_management.load_fonts()
        assert result is not None
        assert "bold" in result
        assert "regular" in result
        assert "light" in result

    def test_returns_none_when_roboto_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        # No font files present

        monkeypatch.setattr(font_management, "FONTS_DIR", fonts_dir)

        result = font_management.load_fonts()
        assert result is None

    def test_missing_font_logs_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()

        monkeypatch.setattr(font_management, "FONTS_DIR", fonts_dir)

        with caplog.at_level(logging.WARNING, logger="maptoposter.font_management"):
            font_management.load_fonts()

        assert any("Font not found" in record.message for record in caplog.records)


class TestDownloadGoogleFontErrorCategories:
    """Tests for error categorization in download_google_font()."""

    @patch("maptoposter.font_management.requests.get")
    def test_connection_error_message(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_get.side_effect = requests.ConnectionError("Connection refused")
        with caplog.at_level(logging.WARNING, logger="maptoposter.font_management"):
            result = font_management.download_google_font("FakeFont")
        assert result is None
        assert any("Network error" in r.message for r in caplog.records)
        assert any("Check your internet connection" in r.message for r in caplog.records)

    @patch("maptoposter.font_management.requests.get")
    def test_timeout_error_message(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_get.side_effect = requests.Timeout("Request timed out")
        with caplog.at_level(logging.WARNING, logger="maptoposter.font_management"):
            result = font_management.download_google_font("FakeFont")
        assert result is None
        assert any("Timeout" in r.message for r in caplog.records)

    @patch("maptoposter.font_management.requests.get")
    def test_404_error_message(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)
        mock_get.return_value = mock_response
        with caplog.at_level(logging.WARNING, logger="maptoposter.font_management"):
            result = font_management.download_google_font("NonExistentFont")
        assert result is None
        assert any("not found on Google Fonts" in r.message for r in caplog.records)


class TestDownloadGoogleFontDownloadPath:
    """Tests for download_google_font() full download path."""

    @patch("maptoposter.font_management.requests.get")
    def test_successful_download(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", cache_dir)

        css = """
@font-face {
  font-weight: 300;
  src: url(https://fonts.example.com/test_300.woff2);
}
@font-face {
  font-weight: 400;
  src: url(https://fonts.example.com/test_400.woff2);
}
@font-face {
  font-weight: 700;
  src: url(https://fonts.example.com/test_700.woff2);
}
"""
        css_response = MagicMock()
        css_response.text = css
        css_response.raise_for_status = MagicMock()

        font_response = MagicMock()
        font_response.content = b"fake font data"
        font_response.raise_for_status = MagicMock()

        mock_get.side_effect = [css_response, font_response, font_response, font_response]

        result = font_management.download_google_font("TestFont")
        assert result is not None
        assert "regular" in result
        assert "bold" in result
        assert "light" in result
        # CSS + 3 font downloads = 4 calls
        assert mock_get.call_count == 4

    @patch("maptoposter.font_management.requests.get")
    def test_missing_weight_uses_closest(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", cache_dir)

        # Only weight 400 available in CSS
        css = """
@font-face {
  font-weight: 400;
  src: url(https://fonts.example.com/test_400.woff2);
}
"""
        css_response = MagicMock()
        css_response.text = css
        css_response.raise_for_status = MagicMock()

        font_response = MagicMock()
        font_response.content = b"fake font data"
        font_response.raise_for_status = MagicMock()

        mock_get.side_effect = [css_response, font_response, font_response, font_response]

        result = font_management.download_google_font("TestFont")
        assert result is not None
        assert "regular" in result

    @patch("maptoposter.font_management.requests.get")
    def test_individual_font_download_failure(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test when individual font weight download fails (lines 110-117)."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", cache_dir)

        css = """
@font-face {
  font-weight: 400;
  src: url(https://fonts.example.com/test_400.woff2);
}
"""
        css_response = MagicMock()
        css_response.text = css
        css_response.raise_for_status = MagicMock()

        # Font download fails
        font_fail = MagicMock()
        font_fail.raise_for_status.side_effect = Exception("Download failed")

        mock_get.side_effect = [css_response, font_fail, font_fail, font_fail]

        result = font_management.download_google_font("TestFont")
        assert result is None  # No fonts successfully downloaded

    @patch("maptoposter.font_management.requests.get")
    def test_no_font_face_blocks(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test CSS with no @font-face blocks (covers line 70)."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", cache_dir)

        css_response = MagicMock()
        css_response.text = "/* empty CSS */"
        css_response.raise_for_status = MagicMock()
        mock_get.return_value = css_response

        result = font_management.download_google_font("TestFont")
        assert result is None


class TestDownloadGoogleFont:
    """Tests for download_google_font() with mocked HTTP."""

    @patch("maptoposter.font_management.requests.get")
    def test_network_error_returns_none(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = Exception("Connection refused")
        result = font_management.download_google_font("FakeFont")
        assert result is None

    @patch("maptoposter.font_management.time.sleep")
    @patch("maptoposter.font_management.requests.get")
    def test_retry_on_connection_error(
        self,
        mock_get: MagicMock,
        mock_sleep: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test that transient connection errors are retried."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", cache_dir)

        css = """
@font-face {
  font-weight: 400;
  src: url(https://fonts.example.com/test_400.woff2);
}
"""
        css_response = MagicMock()
        css_response.text = css
        css_response.raise_for_status = MagicMock()

        font_response = MagicMock()
        font_response.content = b"fake font data"
        font_response.raise_for_status = MagicMock()

        # CSS succeeds, first font download fails, second retry succeeds
        mock_get.side_effect = [
            css_response,
            requests.ConnectionError("connection refused"),
            font_response,
            font_response,
            font_response,
        ]

        result = font_management.download_google_font("TestFont", weights=[400])
        assert result is not None
        assert "regular" in result

    @patch("maptoposter.font_management.time.sleep")
    @patch("maptoposter.font_management.requests.get")
    def test_retry_exhaustion_skips_weight(
        self,
        mock_get: MagicMock,
        mock_sleep: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test that weight is skipped after exhausting retries."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", cache_dir)

        css = """
@font-face {
  font-weight: 400;
  src: url(https://fonts.example.com/test_400.woff2);
}
"""
        css_response = MagicMock()
        css_response.text = css
        css_response.raise_for_status = MagicMock()

        # CSS succeeds, all font download retries fail
        mock_get.side_effect = [
            css_response,
            requests.ConnectionError("fail 1"),
            requests.ConnectionError("fail 2"),
            requests.ConnectionError("fail 3"),
        ]

        result = font_management.download_google_font("TestFont", weights=[400])
        assert result is None  # No weights downloaded successfully

    @patch("maptoposter.font_management.requests.get")
    def test_caching_skips_redownload(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", cache_dir)

        # Pre-populate cached files
        for weight_key in ("light", "regular", "bold"):
            (cache_dir / f"testfont_{weight_key}.woff2").write_bytes(b"cached")

        # CSS response mapping weights to URLs
        css = """
@font-face {
  font-weight: 300;
  src: url(https://fonts.example.com/test_300.woff2);
}
@font-face {
  font-weight: 400;
  src: url(https://fonts.example.com/test_400.woff2);
}
@font-face {
  font-weight: 700;
  src: url(https://fonts.example.com/test_700.woff2);
}
"""
        mock_response = MagicMock()
        mock_response.text = css
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = font_management.download_google_font("TestFont")
        assert result is not None
        # Should only call requests.get once (for CSS), not for individual fonts
        assert mock_get.call_count == 1
