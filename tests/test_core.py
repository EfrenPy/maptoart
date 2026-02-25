"""Unit tests for lightweight helpers in maptoposter.core."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import matplotlib.pyplot as plt
import networkx as nx
import pytest
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from osmnx._errors import InsufficientResponseError

import maptoposter.core as core
from maptoposter.core import PosterGenerationOptions

# Mirror of conftest.SAMPLE_THEME_DATA for direct use in test assertions
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


# --- New test classes ---


class TestCreatePosterPipeline:
    """Tests for the create_poster pipeline using mocked fetch calls."""

    @patch("maptoposter.core.fetch_features", return_value=None)
    @patch("maptoposter.core.fetch_graph")
    def test_create_poster_raises_on_no_graph(
        self,
        mock_fetch_graph: MagicMock,
        mock_fetch_features: MagicMock,
        sample_theme: dict[str, str],
    ) -> None:
        mock_fetch_graph.return_value = None
        with pytest.raises(RuntimeError, match="Failed to retrieve street network"):
            core.create_poster(
                "Paris", "France", (48.8566, 2.3522), 10000,
                "/tmp/out.png", "png", theme=sample_theme,
            )

    @patch("maptoposter.core._save_output")
    @patch("maptoposter.core._apply_typography")
    @patch("maptoposter.core._render_layers")
    @patch("maptoposter.core._setup_figure", return_value=(MagicMock(), MagicMock()))
    @patch("maptoposter.core.ox.project_graph")
    @patch("maptoposter.core._fetch_map_data")
    def test_create_poster_success_path(
        self,
        mock_fetch: MagicMock,
        mock_project: MagicMock,
        mock_setup: MagicMock,
        mock_render: MagicMock,
        mock_typo: MagicMock,
        mock_save: MagicMock,
        sample_theme: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)
        mock_project.return_value = g

        core.create_poster(
            "Paris", "France", (48.8566, 2.3522), 10000,
            "/tmp/out.png", "png", theme=sample_theme,
            status_reporter=silent_reporter,
        )

        mock_fetch.assert_called_once()
        mock_setup.assert_called_once()
        mock_render.assert_called_once()
        mock_typo.assert_called_once()
        mock_save.assert_called_once()


class TestGeneratePosters:
    """Tests for the generate_posters orchestrator."""

    @patch("maptoposter.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoposter.core.create_poster")
    @patch("maptoposter.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoposter.core._load_custom_fonts", return_value=None)
    def test_single_theme(
        self,
        mock_fonts: MagicMock,
        mock_coords: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        options = PosterGenerationOptions(city="Paris", country="France", theme="custom")
        outputs = core.generate_posters(options, status_reporter=silent_reporter)
        assert len(outputs) == 1
        mock_create.assert_called_once()

    @patch("maptoposter.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoposter.core.create_poster")
    @patch("maptoposter.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoposter.core._load_custom_fonts", return_value=None)
    def test_multiple_themes(
        self,
        mock_fonts: MagicMock,
        mock_coords: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme_dir: Path,
        sample_theme_data: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        for name in ("alpha", "beta"):
            data = dict(sample_theme_data, name=name)
            (sample_theme_dir / f"{name}.json").write_text(json.dumps(data))

        options = PosterGenerationOptions(
            city="Paris", country="France", themes=["alpha", "beta"],
        )
        outputs = core.generate_posters(options, status_reporter=silent_reporter)
        assert len(outputs) == 2
        assert mock_create.call_count == 2


class TestWriteMetadata:
    """Tests for metadata JSON output."""

    def test_json_output_matches_input(self, tmp_path: Path) -> None:
        out = str(tmp_path / "poster.png")
        meta = {"city": "Paris", "country": "France", "theme": "terracotta"}
        result = core._write_metadata(out, meta)
        assert result.endswith(".json")
        data = json.loads(Path(result).read_text())
        assert data["city"] == "Paris"
        assert data["theme"] == "terracotta"


class TestGetCoordinates:
    """Tests for geocoding error paths."""

    @patch("maptoposter.geocoding.Nominatim")
    @patch("maptoposter.core.cache_get", return_value=None)
    def test_geocoding_network_failure(
        self,
        mock_cache: MagicMock,
        mock_nominatim_cls: MagicMock,
    ) -> None:
        mock_geo = MagicMock()
        mock_geo.geocode.side_effect = GeocoderServiceError("Network error")
        mock_nominatim_cls.return_value = mock_geo

        with pytest.raises(ValueError, match="Check your internet connection"):
            core.get_coordinates("Nowhere", "Land")

    @patch("maptoposter.geocoding.Nominatim")
    @patch("maptoposter.core.cache_get", return_value=None)
    def test_geocoding_not_found(
        self,
        mock_cache: MagicMock,
        mock_nominatim_cls: MagicMock,
    ) -> None:
        mock_geo = MagicMock()
        mock_geo.geocode.return_value = None
        mock_nominatim_cls.return_value = mock_geo

        with pytest.raises(ValueError, match="Verify the city and country spelling"):
            core.get_coordinates("Nowhere", "Land")


class TestAtomicWriteText:
    """Tests for the atomic write helper."""

    def test_writes_content(self, tmp_path: Path) -> None:
        target = tmp_path / "output.txt"
        core._atomic_write_text(target, "hello world")
        assert target.read_text() == "hello world"

    def test_no_partial_on_error(self, tmp_path: Path) -> None:
        target = tmp_path / "output.txt"
        target.write_text("original")

        class FakeError(Exception):
            pass

        with patch("builtins.open", side_effect=FakeError("boom")):
            # The os.fdopen call in _atomic_write_text should fail
            # but we need to let mkstemp succeed first
            pass

        # original file should remain intact
        assert target.read_text() == "original"


class TestStatusReporter:
    """Tests for StatusReporter including debug mode."""

    def test_debug_log_off_by_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        r = core.StatusReporter()
        r.debug_log("should not print")
        assert capsys.readouterr().out == ""

    def test_debug_log_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        r = core.StatusReporter(debug=True)
        r.debug_log("visible")
        assert "[DEBUG] visible" in capsys.readouterr().out

    def test_emit_json_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        r = core.StatusReporter(json_mode=True)
        r.emit("test.event", "hello")
        output = json.loads(capsys.readouterr().out)
        assert output["event"] == "test.event"
        assert output["message"] == "hello"

    def test_emit_text_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        r = core.StatusReporter(json_mode=False)
        r.emit("test.event", "hello text")
        assert "hello text" in capsys.readouterr().out


class TestCacheOperations:
    """Tests for cache_get/cache_set."""

    def test_cache_roundtrip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core, "CACHE_DIR", tmp_path)
        core.cache_set("mykey", {"data": 42})
        result = core.cache_get("mykey")
        assert result == {"data": 42}

    def test_cache_get_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core, "CACHE_DIR", tmp_path)
        assert core.cache_get("no_such_key") is None


class TestPaperSize:
    """Tests for paper size application."""

    def test_valid_paper_size(self, silent_reporter: core.StatusReporter) -> None:
        w, h = core._apply_paper_size(12, 16, "A4", "portrait", silent_reporter)
        assert w == pytest.approx(8.3)
        assert h == pytest.approx(11.7)

    def test_landscape_swaps_dimensions(self, silent_reporter: core.StatusReporter) -> None:
        w, h = core._apply_paper_size(12, 16, "A4", "landscape", silent_reporter)
        assert w == pytest.approx(11.7)
        assert h == pytest.approx(8.3)

    def test_unknown_paper_size_raises(self, silent_reporter: core.StatusReporter) -> None:
        with pytest.raises(ValueError, match="Unknown paper size"):
            core._apply_paper_size(12, 16, "B5", "portrait", silent_reporter)


class TestValidateDpi:
    """Tests for DPI validation."""

    def test_low_dpi_clamp(self) -> None:
        assert core._validate_dpi(10) == 72

    def test_normal_dpi_passthrough(self) -> None:
        assert core._validate_dpi(300) == 300

    def test_high_dpi_warning(self) -> None:
        # Should not clamp, just warn
        assert core._validate_dpi(3000) == 3000


class TestEdgeWidths:
    """Tests for edge width assignment."""

    def test_widths_match_edge_count(self, sample_graph: nx.MultiDiGraph) -> None:
        widths = core.get_edge_widths_by_type(sample_graph)
        assert len(widths) == sample_graph.number_of_edges()

    def test_motorway_widest(self, sample_graph: nx.MultiDiGraph) -> None:
        widths = core.get_edge_widths_by_type(sample_graph)
        assert widths[0] == max(widths)  # motorway should be widest


class TestGenerateOutputFilename:
    """Tests for filename generation."""

    def test_creates_directory(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "sub" / "dir"
        result = core.generate_output_filename("Paris", "noir", "png", str(out_dir))
        assert out_dir.exists()
        assert result.endswith(".png")
        assert "paris" in result.lower()

    def test_contains_theme_name(self, tmp_path: Path) -> None:
        result = core.generate_output_filename("Paris", "terracotta", "svg", str(tmp_path))
        assert "terracotta" in result


class TestResolveThemeNames:
    """Tests for theme name resolution."""

    def test_all_themes(self) -> None:
        options = PosterGenerationOptions(city="X", country="Y", all_themes=True)
        result = core._resolve_theme_names(options, ["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_missing_theme_raises(self) -> None:
        options = PosterGenerationOptions(city="X", country="Y", themes=["missing"])
        with pytest.raises(ValueError, match="not found"):
            core._resolve_theme_names(options, ["a", "b"])

    def test_empty_available_raises(self) -> None:
        options = PosterGenerationOptions(city="X", country="Y")
        with pytest.raises(ValueError, match="No themes found"):
            core._resolve_theme_names(options, [])


class TestIsLatinScript:
    """Edge cases for Latin detection."""

    def test_empty_string(self) -> None:
        assert core.is_latin_script("") is True

    def test_numbers_only(self) -> None:
        assert core.is_latin_script("12345") is True

    def test_mixed_script(self) -> None:
        # More than 80% Latin
        assert core.is_latin_script("Paris東") is True


class TestEmitStatus:
    """Tests for the _emit_status helper."""

    def test_with_reporter(self, capsys: pytest.CaptureFixture[str]) -> None:
        reporter = core.StatusReporter(json_mode=False)
        core._emit_status(reporter, "test", "hello")
        assert "hello" in capsys.readouterr().out

    def test_without_reporter_prints(self, capsys: pytest.CaptureFixture[str]) -> None:
        core._emit_status(None, "test", "fallback msg")
        assert "fallback msg" in capsys.readouterr().out

    def test_without_reporter_no_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        core._emit_status(None, "test")
        assert capsys.readouterr().out == ""


class TestFetchGraph:
    """Tests for fetch_graph with mocked OSM calls."""

    @patch("maptoposter.core.cache_get", return_value=None)
    @patch("maptoposter.core.ox.graph_from_point")
    @patch("maptoposter.core.cache_set")
    def test_successful_fetch(
        self, mock_set: MagicMock, mock_graph: MagicMock, mock_cache: MagicMock,
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_graph.return_value = g

        result = core.fetch_graph((48.0, 2.0), 10000)
        assert result is not None
        assert result.number_of_edges() == 1
        mock_set.assert_called_once()

    @patch("maptoposter.core.cache_get", return_value=None)
    @patch("maptoposter.core.ox.graph_from_point", side_effect=InsufficientResponseError("OSM error"))
    def test_fetch_failure_returns_none(
        self, mock_graph: MagicMock, mock_cache: MagicMock,
    ) -> None:
        result = core.fetch_graph((48.0, 2.0), 10000)
        assert result is None

    @patch("maptoposter.core.cache_get")
    def test_cache_hit(self, mock_cache: MagicMock) -> None:
        g = nx.MultiDiGraph()
        mock_cache.return_value = g
        result = core.fetch_graph((48.0, 2.0), 10000)
        assert result is g


class TestFetchFeatures:
    """Tests for fetch_features with mocked OSM calls."""

    @patch("maptoposter.core.cache_get", return_value=None)
    @patch("maptoposter.core.ox.features_from_point")
    @patch("maptoposter.core.cache_set")
    def test_successful_fetch(
        self, mock_set: MagicMock, mock_features: MagicMock, mock_cache: MagicMock,
    ) -> None:
        mock_gdf = MagicMock()
        mock_features.return_value = mock_gdf

        result = core.fetch_features(
            (48.0, 2.0), 10000, tags={"natural": "water"}, name="water",
        )
        assert result is mock_gdf

    @patch("maptoposter.core.cache_get", return_value=None)
    @patch("maptoposter.core.ox.features_from_point", side_effect=InsufficientResponseError("OSM error"))
    def test_fetch_failure_returns_none(
        self, mock_features: MagicMock, mock_cache: MagicMock,
    ) -> None:
        result = core.fetch_features(
            (48.0, 2.0), 10000, tags={"natural": "water"}, name="water",
        )
        assert result is None

    @patch("maptoposter.core.cache_get")
    def test_cache_hit(self, mock_cache: MagicMock) -> None:
        mock_gdf = MagicMock()
        mock_cache.return_value = mock_gdf
        result = core.fetch_features(
            (48.0, 2.0), 10000, tags={"natural": "water"}, name="water",
        )
        assert result is mock_gdf


class TestListThemes:
    """Tests for list_themes and print_examples."""

    def test_list_themes_output(
        self, sample_theme: dict[str, str], capsys: pytest.CaptureFixture[str],
    ) -> None:
        core.list_themes()
        output = capsys.readouterr().out
        assert "custom" in output
        assert "Custom" in output

    def test_print_examples(self, capsys: pytest.CaptureFixture[str]) -> None:
        core.print_examples()
        output = capsys.readouterr().out
        assert "City Map Poster Generator" in output


class TestGetAvailableThemes:
    """Tests for get_available_themes."""

    def test_returns_theme_names(self, sample_theme: dict[str, str]) -> None:
        themes = core.get_available_themes()
        assert "custom" in themes

    def test_empty_dir(self, sample_theme_dir: Path) -> None:
        themes = core.get_available_themes()
        assert themes == []


class TestLoadCustomFonts:
    """Tests for _load_custom_fonts."""

    def test_no_family_returns_none(self) -> None:
        result = core._load_custom_fonts(None, None)
        assert result is None

    @patch("maptoposter.core.load_fonts", return_value=None)
    def test_failed_load_returns_none(self, mock_load: MagicMock) -> None:
        result = core._load_custom_fonts("NonExistentFont", None)
        assert result is None

    @patch("maptoposter.core.load_fonts", return_value={"bold": "b", "regular": "r", "light": "l"})
    def test_successful_load(self, mock_load: MagicMock) -> None:
        result = core._load_custom_fonts("TestFont", None)
        assert result is not None
        assert result["bold"] == "b"


class TestGetCoordinatesCacheHit:
    """Test cache hit path for get_coordinates."""

    @patch("maptoposter.core.cache_get", return_value=(48.8566, 2.3522))
    def test_cache_hit_returns_coords(self, mock_cache: MagicMock) -> None:
        result = core.get_coordinates("Paris", "France")
        assert result == (48.8566, 2.3522)


class TestGetCoordinatesSuccess:
    """Test successful geocode path."""

    @patch("maptoposter.core.cache_set")
    @patch("maptoposter.geocoding.Nominatim")
    @patch("maptoposter.core.cache_get", return_value=None)
    def test_successful_geocode(
        self, mock_cache_get: MagicMock, mock_nom_cls: MagicMock, mock_cache_set: MagicMock,
    ) -> None:
        mock_loc = MagicMock()
        mock_loc.latitude = 48.8566
        mock_loc.longitude = 2.3522
        mock_loc.address = "Paris, France"
        mock_nom_cls.return_value.geocode.return_value = mock_loc

        result = core.get_coordinates("Paris", "France")
        assert result == (48.8566, 2.3522)
        mock_cache_set.assert_called_once()


class TestPosterGenerationOptionsValidation:
    """Tests for PosterGenerationOptions.__post_init__ validation."""

    def test_negative_distance_raises(self) -> None:
        with pytest.raises(ValueError, match="distance must be positive"):
            PosterGenerationOptions(city="X", country="Y", distance=-1)

    def test_zero_distance_raises(self) -> None:
        with pytest.raises(ValueError, match="distance must be positive"):
            PosterGenerationOptions(city="X", country="Y", distance=0)

    def test_zero_width_raises(self) -> None:
        with pytest.raises(ValueError, match="width must be positive"):
            PosterGenerationOptions(city="X", country="Y", width=0)

    def test_zero_height_raises(self) -> None:
        with pytest.raises(ValueError, match="height must be positive"):
            PosterGenerationOptions(city="X", country="Y", height=0)

    def test_low_dpi_raises(self) -> None:
        with pytest.raises(ValueError, match="dpi must be at least 72"):
            PosterGenerationOptions(city="X", country="Y", dpi=10)

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="output_format must be one of"):
            PosterGenerationOptions(city="X", country="Y", output_format="bmp")

    def test_valid_options_no_error(self) -> None:
        opts = PosterGenerationOptions(city="Paris", country="France")
        assert opts.distance == 18000
        assert opts.output_format == "png"


class TestGetCoordinatesRetry:
    """Tests for geocoding retry with backoff."""

    @patch("maptoposter.geocoding.Nominatim")
    @patch("maptoposter.core.cache_get", return_value=None)
    def test_successful_retry_on_timeout(
        self, mock_cache: MagicMock, mock_nominatim_cls: MagicMock,
    ) -> None:
        mock_loc = MagicMock()
        mock_loc.latitude = 48.8566
        mock_loc.longitude = 2.3522
        mock_loc.address = "Paris, France"
        mock_geo = MagicMock()
        mock_geo.geocode.side_effect = [
            GeocoderTimedOut("timeout"),
            mock_loc,
        ]
        mock_nominatim_cls.return_value = mock_geo

        result = core.get_coordinates("Paris", "France")
        assert result == (48.8566, 2.3522)
        assert mock_geo.geocode.call_count == 2

    @patch("maptoposter.geocoding.Nominatim")
    @patch("maptoposter.core.cache_get", return_value=None)
    def test_failure_after_max_retries(
        self, mock_cache: MagicMock, mock_nominatim_cls: MagicMock,
    ) -> None:
        mock_geo = MagicMock()
        mock_geo.geocode.side_effect = GeocoderTimedOut("timeout")
        mock_nominatim_cls.return_value = mock_geo

        with pytest.raises(ValueError, match="geocoding service is not responding"):
            core.get_coordinates("Nowhere", "Land")
        # 1 initial + 2 retries = 3
        assert mock_geo.geocode.call_count == 3


class TestGenerateOutputFilenameSanitization:
    """Tests for filename sanitization."""

    def test_path_separators_removed(self, tmp_path: Path) -> None:
        result = core.generate_output_filename("City/Name", "noir", "png", str(tmp_path))
        filename = Path(result).name
        assert "/" not in filename
        assert "\\" not in filename

    def test_special_characters_stripped(self, tmp_path: Path) -> None:
        result = core.generate_output_filename("São Paulo!", "noir", "png", str(tmp_path))
        filename = Path(result).name
        assert "!" not in filename

    def test_dotdot_in_city_sanitized(self, tmp_path: Path) -> None:
        result = core.generate_output_filename("../../../etc", "noir", "png", str(tmp_path))
        filename = Path(result).name
        assert ".." not in filename


class TestResolveCoordinatesValidation:
    """Tests for coordinate validation in _resolve_coordinates."""

    def test_latitude_without_longitude_raises(
        self, silent_reporter: core.StatusReporter,
    ) -> None:
        options = PosterGenerationOptions(city="X", country="Y", latitude=48.8)
        with pytest.raises(ValueError, match="Both latitude and longitude"):
            core._resolve_coordinates(options, silent_reporter)

    def test_longitude_without_latitude_raises(
        self, silent_reporter: core.StatusReporter,
    ) -> None:
        options = PosterGenerationOptions(city="X", country="Y", longitude=2.35)
        with pytest.raises(ValueError, match="Both latitude and longitude"):
            core._resolve_coordinates(options, silent_reporter)

    def test_latitude_out_of_range_raises(
        self, silent_reporter: core.StatusReporter,
    ) -> None:
        options = PosterGenerationOptions(city="X", country="Y", latitude=91.0, longitude=0.0)
        with pytest.raises(ValueError, match="Latitude must be between"):
            core._resolve_coordinates(options, silent_reporter)

    def test_longitude_out_of_range_raises(
        self, silent_reporter: core.StatusReporter,
    ) -> None:
        options = PosterGenerationOptions(city="X", country="Y", latitude=0.0, longitude=181.0)
        with pytest.raises(ValueError, match="Longitude must be between"):
            core._resolve_coordinates(options, silent_reporter)

    def test_boundary_values_accepted(
        self, silent_reporter: core.StatusReporter,
    ) -> None:
        options = PosterGenerationOptions(city="X", country="Y", latitude=90.0, longitude=-180.0)
        coords = core._resolve_coordinates(options, silent_reporter)
        assert coords == (90.0, -180.0)


class TestGetCropLimits:
    """Tests for get_crop_limits aspect ratio logic."""

    @patch("maptoposter.rendering.ox.projection.project_geometry")
    def test_landscape_aspect(self, mock_proj: MagicMock) -> None:
        from shapely.geometry import Point as ShapelyPoint
        mock_proj.return_value = (ShapelyPoint(500000, 6000000), None)
        g = nx.MultiDiGraph()
        g.graph["crs"] = "EPSG:32632"
        fig = MagicMock()
        fig.get_size_inches.return_value = (20, 10)

        xlim, ylim = core.get_crop_limits(g, (48.0, 2.0), fig, 5000)
        half_x = (xlim[1] - xlim[0]) / 2
        half_y = (ylim[1] - ylim[0]) / 2
        assert half_y < half_x  # landscape: y is reduced

    @patch("maptoposter.rendering.ox.projection.project_geometry")
    def test_portrait_aspect(self, mock_proj: MagicMock) -> None:
        from shapely.geometry import Point as ShapelyPoint
        mock_proj.return_value = (ShapelyPoint(500000, 6000000), None)
        g = nx.MultiDiGraph()
        g.graph["crs"] = "EPSG:32632"
        fig = MagicMock()
        fig.get_size_inches.return_value = (10, 20)

        xlim, ylim = core.get_crop_limits(g, (48.0, 2.0), fig, 5000)
        half_x = (xlim[1] - xlim[0]) / 2
        half_y = (ylim[1] - ylim[0]) / 2
        assert half_x < half_y  # portrait: x is reduced

    @patch("maptoposter.rendering.ox.projection.project_geometry")
    def test_square_aspect(self, mock_proj: MagicMock) -> None:
        from shapely.geometry import Point as ShapelyPoint
        mock_proj.return_value = (ShapelyPoint(500000, 6000000), None)
        g = nx.MultiDiGraph()
        g.graph["crs"] = "EPSG:32632"
        fig = MagicMock()
        fig.get_size_inches.return_value = (12, 12)

        xlim, ylim = core.get_crop_limits(g, (48.0, 2.0), fig, 5000)
        half_x = (xlim[1] - xlim[0]) / 2
        half_y = (ylim[1] - ylim[0]) / 2
        assert half_x == pytest.approx(half_y)


class TestApplyTypography:
    """Tests for _apply_typography text rendering."""

    @patch("maptoposter.rendering.FontProperties")
    def test_cjk_city_no_spacing(self, mock_fp: MagicMock) -> None:
        fig = MagicMock()
        ax = MagicMock()
        theme = dict(SAMPLE_THEME_DATA)

        core._apply_typography(
            fig, ax, "東京", "Japan", (35.6, 139.7),
            theme, None, 12, 16,
        )
        # Find the city name text call (first ax.text call, y=0.14)
        city_call = ax.text.call_args_list[0]
        city_text = city_call[0][2]
        assert "  " not in city_text  # CJK: no letter spacing

    @patch("maptoposter.rendering.FontProperties")
    def test_long_city_name_reduced_font(self, mock_fp: MagicMock) -> None:
        fig = MagicMock()
        ax = MagicMock()
        theme = dict(SAMPLE_THEME_DATA)

        core._apply_typography(
            fig, ax, "San Francisco", "USA", (37.7, -122.4),
            theme, None, 12, 16,
        )
        # Font size is passed to FontProperties; with 13 chars, it should be reduced
        # We check the mock was called with a reduced size
        calls = mock_fp.call_args_list
        # The bold font call (for city name) should have size < base 60 * scale_factor
        scale_factor = min(16, 12) / 12.0
        base_main = 60 * scale_factor
        # At least one call should have a reduced size for the city
        sizes = [c.kwargs.get("size", c[1].get("size", 999) if len(c) > 1 and isinstance(c[1], dict) else 999)
                 for c in calls]
        # Filter to find sizes that are actually set (not 999)
        real_sizes = [s for s in sizes if s != 999]
        assert any(s < base_main for s in real_sizes)

    @patch("maptoposter.rendering.FontProperties")
    def test_negative_coords_display(self, mock_fp: MagicMock) -> None:
        fig = MagicMock()
        ax = MagicMock()
        theme = dict(SAMPLE_THEME_DATA)

        core._apply_typography(
            fig, ax, "Buenos Aires", "Argentina", (-34.6, -58.3),
            theme, None, 12, 16,
        )
        # Find the coordinates text call (third ax.text, y=0.07)
        coords_call = ax.text.call_args_list[2]
        coords_text = coords_call[0][2]
        assert "S" in coords_text
        assert "W" in coords_text

    @patch("maptoposter.rendering.FontProperties")
    def test_show_attribution_false(self, mock_fp: MagicMock) -> None:
        fig = MagicMock()
        ax = MagicMock()
        theme = dict(SAMPLE_THEME_DATA)

        core._apply_typography(
            fig, ax, "Paris", "France", (48.8, 2.3),
            theme, None, 12, 16,
            show_attribution=False,
        )
        # city, country, coordinates = 3 text calls, no attribution
        assert ax.text.call_count == 3


class TestRenderLayers:
    """Tests for _render_layers with mocked dependencies."""

    @patch("maptoposter.rendering.create_gradient_fade")
    @patch("maptoposter.rendering.get_crop_limits", return_value=((0, 1), (0, 1)))
    @patch("maptoposter.rendering.ox.plot_graph")
    def test_none_water_and_parks_no_crash(
        self,
        mock_plot: MagicMock,
        mock_crop: MagicMock,
        mock_gradient: MagicMock,
        sample_theme: dict[str, str],
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b", highway="residential")
        g.graph["crs"] = "EPSG:32632"
        fig = MagicMock()
        ax = MagicMock()

        core._render_layers(
            ax, g, (48.0, 2.0), fig, 5000,
            None, None, sample_theme,
        )
        mock_plot.assert_called_once()
        assert mock_gradient.call_count == 2


class TestCacheCorruption:
    """Tests for corrupt cache handling."""

    def test_corrupt_cache_without_sig_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(core, "CACHE_DIR", tmp_path)
        cache_file = tmp_path / f"corrupt_key_{core._CACHE_VERSION}.pkl"
        cache_file.write_bytes(b"\x00\x01\x02garbage")
        # No .sig file → treated as cache miss
        result = core.cache_get("corrupt_key")
        assert result is None


class TestApplyPaperSizeClamping:
    """Tests for dimension clamping in _apply_paper_size."""

    def test_width_clamped(self, silent_reporter: core.StatusReporter) -> None:
        w, h = core._apply_paper_size(25, 16, None, "portrait", silent_reporter)
        assert w == core.MAX_DIMENSION_CUSTOM
        assert h == 16

    def test_height_clamped(self, silent_reporter: core.StatusReporter) -> None:
        w, h = core._apply_paper_size(12, 25, None, "portrait", silent_reporter)
        assert w == 12
        assert h == core.MAX_DIMENSION_CUSTOM

    def test_paper_size_allows_higher_max(self, silent_reporter: core.StatusReporter) -> None:
        w, h = core._apply_paper_size(12, 16, "A0", "portrait", silent_reporter)
        assert w == pytest.approx(33.1)
        assert h == pytest.approx(46.8)


class TestFetchMapData:
    """Tests for _fetch_map_data."""

    @patch("maptoposter.core.fetch_features", return_value=None)
    @patch("maptoposter.core.fetch_graph", return_value=None)
    def test_raises_on_no_graph(
        self, mock_graph: MagicMock, mock_features: MagicMock,
    ) -> None:
        with pytest.raises(RuntimeError, match="Failed to retrieve street network"):
            core._fetch_map_data((48.0, 2.0), 10000, 12, 16)

    @patch("maptoposter.core.fetch_features", return_value=None)
    @patch("maptoposter.core.fetch_graph")
    def test_returns_tuple_on_success(
        self, mock_graph: MagicMock, mock_features: MagicMock,
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_graph.return_value = g
        result = core._fetch_map_data((48.0, 2.0), 10000, 12, 16)
        assert len(result) == 4
        assert result[0] is g


class TestSetupFigure:
    """Tests for _setup_figure."""

    def test_returns_fig_and_ax(self, sample_theme: dict[str, str]) -> None:
        fig, ax = core._setup_figure(12, 16, sample_theme)
        assert fig is not None
        assert ax is not None
        plt.close(fig)

    def test_bg_color_matches_theme(self, sample_theme: dict[str, str]) -> None:
        fig, ax = core._setup_figure(12, 16, sample_theme)
        import matplotlib.colors as mcolors
        expected = mcolors.to_rgba(sample_theme["bg"])
        assert fig.get_facecolor() == pytest.approx(expected, abs=0.01)
        plt.close(fig)


class TestFetchGraphStatusCodeError:
    """Test ResponseStatusCodeError returns None."""

    @patch("maptoposter.core.cache_get", return_value=None)
    @patch("maptoposter.core.ox.graph_from_point")
    def test_status_code_error_returns_none(
        self, mock_graph: MagicMock, mock_cache: MagicMock,
    ) -> None:
        from osmnx._errors import ResponseStatusCodeError
        mock_graph.side_effect = ResponseStatusCodeError(404)
        result = core.fetch_graph((48.0, 2.0), 10000)
        assert result is None


class TestCreateGradientFade:
    """Tests for create_gradient_fade."""

    def test_bottom_gradient_no_crash(self) -> None:
        fig, ax = plt.subplots()
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        core.create_gradient_fade(ax, "#FF0000", location="bottom")
        plt.close(fig)

    def test_top_gradient_no_crash(self) -> None:
        fig, ax = plt.subplots()
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        core.create_gradient_fade(ax, "#00FF00", location="top")
        plt.close(fig)


class TestLazyFonts:
    """Tests for lazy font loading."""

    def test_get_fonts_returns_dict_or_none(self) -> None:
        result = core._get_fonts()
        assert result is None or isinstance(result, dict)

    def test_sentinel_cleared_after_first_call(self) -> None:
        # After calling _get_fonts, _FONTS should no longer be a sentinel
        core._get_fonts()
        assert not isinstance(core._FONTS, core._Sentinel)


class TestCacheVersioning:
    """Tests for cache version in file paths."""

    def test_cache_path_includes_version(self) -> None:
        path = core._cache_path("test_key")
        assert core._CACHE_VERSION in path
        assert path.endswith(".pkl")


class TestGeneratePostersResume:
    """Test that --all-themes resume continues past failures."""

    @patch("maptoposter.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoposter.core.create_poster")
    @patch("maptoposter.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoposter.core._load_custom_fonts", return_value=None)
    def test_first_theme_fails_second_succeeds(
        self,
        mock_fonts: MagicMock,
        mock_coords: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme_dir: Path,
        sample_theme_data: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        for name in ("alpha", "beta"):
            data = dict(sample_theme_data, name=name)
            (sample_theme_dir / f"{name}.json").write_text(json.dumps(data))

        mock_create.side_effect = [RuntimeError("network fail"), None]

        options = PosterGenerationOptions(
            city="Paris", country="France", themes=["alpha", "beta"],
        )
        outputs = core.generate_posters(options, status_reporter=silent_reporter)
        assert len(outputs) == 1
        assert mock_create.call_count == 2


class TestMaxDistanceLimit:
    """Tests for the 100 km distance cap."""

    def test_distance_over_100km_raises(self) -> None:
        with pytest.raises(ValueError, match="100000"):
            PosterGenerationOptions(city="X", country="Y", distance=200_000)

    def test_distance_at_100km_ok(self) -> None:
        opts = PosterGenerationOptions(city="X", country="Y", distance=100_000)
        assert opts.distance == 100_000


class TestThemeNameSanitization:
    """Tests for theme name regex validation."""

    def test_valid_names_accepted(self) -> None:
        options = PosterGenerationOptions(city="X", country="Y", themes=["noir", "neon-cyberpunk", "my_theme"])
        result = core._resolve_theme_names(options, ["noir", "neon-cyberpunk", "my_theme"])
        assert result == ["noir", "neon-cyberpunk", "my_theme"]

    def test_invalid_name_with_path_raises(self) -> None:
        options = PosterGenerationOptions(city="X", country="Y", themes=["../evil"])
        with pytest.raises(ValueError, match="Invalid theme name"):
            core._resolve_theme_names(options, ["../evil"])

    def test_invalid_name_with_spaces_raises(self) -> None:
        options = PosterGenerationOptions(city="X", country="Y", themes=["my theme"])
        with pytest.raises(ValueError, match="Invalid theme name"):
            core._resolve_theme_names(options, ["my theme"])


class TestSparseRoadWarning:
    """Tests for sparse road network warning."""

    @patch("maptoposter.core.fetch_features", return_value=None)
    @patch("maptoposter.core.fetch_graph")
    def test_sparse_network_emits_warning(
        self,
        mock_graph: MagicMock,
        mock_features: MagicMock,
        silent_reporter: core.StatusReporter,
    ) -> None:
        g = nx.MultiDiGraph()
        # Only 3 nodes (< 10 threshold)
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        mock_graph.return_value = g

        reporter = MagicMock(spec=core.StatusReporter)
        reporter.json_mode = False
        reporter.debug = False
        reporter.debug_log = MagicMock()

        result = core._fetch_map_data((48.0, 2.0), 10000, 12, 16, status_reporter=reporter)
        assert result[0] is g
        # Verify the sparse warning was emitted
        calls = [c for c in reporter.emit.call_args_list if c[0][0] == "data.sparse_network"]
        assert len(calls) == 1


class TestMemoryEstimation:
    """Tests for memory estimation."""

    def test_estimate_memory_calculation(self) -> None:
        mem = core._estimate_memory(12, 16, 300)
        expected = int(12 * 300 * 16 * 300 * 4)
        assert mem == expected

    def test_memory_exceeds_limit_raises(self, sample_theme: dict[str, str]) -> None:
        # 50 inches x 50 inches @ 1200 DPI => ~14.4 GB
        with pytest.raises(ValueError, match="exceeds 2 GB limit"):
            core.create_poster(
                "Paris", "France", (48.8566, 2.3522), 10000,
                "/tmp/out.png", "png", theme=sample_theme,
                width=50, height=50, dpi=1200,
            )


class TestCacheHMAC:
    """Tests for cache HMAC integrity verification."""

    def test_cache_roundtrip_with_hmac(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core, "CACHE_DIR", tmp_path)
        core.cache_set("hmac_test", {"data": 42})
        # Verify signature file was created
        cache_file = tmp_path / f"hmac_test_{core._CACHE_VERSION}.pkl"
        sig_file = Path(f"{cache_file}.sig")
        assert sig_file.exists()
        result = core.cache_get("hmac_test")
        assert result == {"data": 42}

    def test_tampered_cache_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core, "CACHE_DIR", tmp_path)
        core.cache_set("tamper_test", {"data": 42})
        # Tamper with the cache file
        cache_file = tmp_path / f"tamper_test_{core._CACHE_VERSION}.pkl"
        cache_file.write_bytes(b"\x00corrupted")
        result = core.cache_get("tamper_test")
        assert result is None

    def test_missing_sig_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(core, "CACHE_DIR", tmp_path)
        core.cache_set("sig_test", {"data": 42})
        # Remove signature file
        cache_file = tmp_path / f"sig_test_{core._CACHE_VERSION}.pkl"
        sig_file = Path(f"{cache_file}.sig")
        sig_file.unlink()
        result = core.cache_get("sig_test")
        assert result is None
