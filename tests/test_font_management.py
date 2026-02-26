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
        assert any("Network error" in r.message for r in caplog.records)

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

        # Font download fails with an HTTP error
        font_fail = MagicMock()
        font_fail.raise_for_status.side_effect = requests.HTTPError("Download failed")

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
        mock_get.side_effect = requests.ConnectionError("Connection refused")
        result = font_management.download_google_font("FakeFont")
        assert result is None

    @patch("tenacity.nap.time.sleep")
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

    @patch("tenacity.nap.time.sleep")
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


class TestGetActiveFonts:
    """Tests for get_active_fonts() introspection API."""

    def test_bundled_roboto_when_available(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        for weight in ("Bold", "Regular", "Light"):
            (fonts_dir / f"Roboto-{weight}.ttf").write_bytes(b"fake")
        monkeypatch.setattr(font_management, "FONTS_DIR", fonts_dir)

        info = font_management.get_active_fonts()
        assert info["source"] == "bundled"
        assert info["family"] == "Roboto"
        assert info["available"] is True
        assert "regular" in info["paths"]

    def test_monospace_fallback_when_no_fonts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        monkeypatch.setattr(font_management, "FONTS_DIR", fonts_dir)

        info = font_management.get_active_fonts()
        assert info["source"] == "monospace_fallback"
        assert info["available"] is False

    def test_google_font_from_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "noto_sans_jp_regular.woff2").write_bytes(b"fake")
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", cache_dir)

        info = font_management.get_active_fonts("Noto Sans JP")
        assert info["source"] == "google"
        assert info["family"] == "Noto Sans JP"
        assert info["available"] is True
        assert "regular" in info["paths"]

    def test_unknown_google_font_falls_back_to_bundled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        for weight in ("Bold", "Regular", "Light"):
            (fonts_dir / f"Roboto-{weight}.ttf").write_bytes(b"fake")
        monkeypatch.setattr(font_management, "FONTS_DIR", fonts_dir)
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", tmp_path / "empty")

        info = font_management.get_active_fonts("UnknownFont")
        assert info["source"] == "bundled"


class TestDownloadFontFileRetryableHTTP:
    """Test _download_font_file raises _RetryableHTTPError on 500 (#R17-7)."""

    @patch("tenacity.nap.time.sleep")
    @patch("maptoposter.font_management.requests.get")
    def test_http_500_raises_retryable(self, mock_get: MagicMock, mock_sleep: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 500
        mock_get.return_value = resp
        with pytest.raises(font_management._RetryableHTTPError, match="HTTP 500"):
            font_management._download_font_file("https://example.com/font.woff2")


class TestFontFileWriteOSError:
    """Test font file write OSError is caught per-weight (#R17-8)."""

    @patch("maptoposter.font_management._download_font_file", return_value=b"fontdata")
    @patch("maptoposter.font_management.requests.get")
    def test_write_oserror_skips_weight(
        self,
        mock_get: MagicMock,
        mock_download: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
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
        mock_get.return_value = css_response

        # Make write_bytes raise OSError
        with patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
            result = font_management.download_google_font("TestFont")
        assert result is None


class TestCSSBlockWithoutFontWeight:
    """Test CSS @font-face block without font-weight is skipped (#R18-9)."""

    @patch("maptoposter.font_management.requests.get")
    def test_block_without_weight_skipped(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", cache_dir)

        # CSS with one block missing font-weight and one valid block
        css = """
@font-face {
  src: url(https://fonts.example.com/test_no_weight.woff2);
}
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


class TestLoadFontsGoogleFontFallback:
    """Test load_fonts falls back to Roboto when Google Font fails (#R18-10)."""

    @patch("maptoposter.font_management.download_google_font", return_value=None)
    def test_custom_font_failure_falls_back_to_roboto(
        self,
        mock_download: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        for weight in ("Bold", "Regular", "Light"):
            (fonts_dir / f"Roboto-{weight}.ttf").write_bytes(b"fake")
        monkeypatch.setattr(font_management, "FONTS_DIR", fonts_dir)

        result = font_management.load_fonts("CustomFont")
        assert result is not None
        assert "bold" in result
        assert "regular" in result
        assert "light" in result
        # Verify the fallback is Roboto paths
        assert "Roboto-Bold.ttf" in result["bold"]

    @patch("maptoposter.font_management.download_google_font", return_value=None)
    def test_custom_font_failure_and_no_roboto(
        self,
        mock_download: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        # No Roboto files
        monkeypatch.setattr(font_management, "FONTS_DIR", fonts_dir)

        with caplog.at_level(logging.WARNING, logger="maptoposter.font_management"):
            result = font_management.load_fonts("CustomFont")
        assert result is None
        assert any("falling back" in r.message.lower() for r in caplog.records)


class TestFontRequestException:
    """Test generic RequestException is caught (#R18-9b, lines 191-193)."""

    @patch("maptoposter.font_management.requests.get")
    def test_request_exception_returns_none(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_get.side_effect = requests.RequestException("Something weird")
        with caplog.at_level(logging.WARNING, logger="maptoposter.font_management"):
            result = font_management.download_google_font("FakeFont")
        assert result is None
        assert any("Error downloading" in r.message for r in caplog.records)

    @patch("maptoposter.font_management.requests.get")
    def test_non_404_http_error_returns_none(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)
        mock_get.return_value = mock_response
        with caplog.at_level(logging.WARNING, logger="maptoposter.font_management"):
            result = font_management.download_google_font("FakeFont")
        assert result is None
        assert any("HTTP error downloading" in r.message for r in caplog.records)


class TestRegularWeightFallback:
    """Test 'regular' weight populated from first available when missing (#R20-1)."""

    @patch("maptoposter.font_management.requests.get")
    def test_only_bold_weight_becomes_regular(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.setattr(font_management, "FONTS_CACHE_DIR", cache_dir)

        # CSS only has weight 700 (bold)
        css = """
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

        # Request only weight 700 so only "bold" key is populated
        mock_get.side_effect = [css_response, font_response]

        with caplog.at_level(logging.INFO, logger="maptoposter.font_management"):
            result = font_management.download_google_font("TestFont", weights=[700])

        assert result is not None
        # "regular" should be populated from "bold" (first available)
        assert "regular" in result
        assert "bold" in result
        assert "light" in result
        # Verify the fallback log was emitted
        assert any("Using" in r.message and "regular" in r.message for r in caplog.records)


class TestLoadFontsGoogleFontSuccessLog:
    """Test load_fonts logs success when Google Font downloads OK (#R20-2)."""

    @patch("maptoposter.font_management.download_google_font")
    def test_success_log_emitted(
        self,
        mock_download: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_download.return_value = {
            "regular": "/tmp/custom_regular.woff2",
            "bold": "/tmp/custom_bold.woff2",
            "light": "/tmp/custom_light.woff2",
        }
        with caplog.at_level(logging.INFO, logger="maptoposter.font_management"):
            result = font_management.load_fonts("CustomFont")
        assert result is not None
        assert any("loaded successfully" in r.message for r in caplog.records)
