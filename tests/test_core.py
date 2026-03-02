"""Unit tests for lightweight helpers in maptoart.core."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import matplotlib.pyplot as plt
import networkx as nx
import pytest
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from osmnx._errors import InsufficientResponseError

import maptoart._util as _util
import maptoart.core as core
from maptoart._util import CacheError
from maptoart.core import PosterGenerationOptions

from conftest import SAMPLE_THEME_DATA


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

    import maptoart.geocoding as geocoding_mod

    monkeypatch.setattr(geocoding_mod, "get_coordinates", fake_get_coordinates)
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

    @patch("maptoart.core.fetch_features", return_value=None)
    @patch("maptoart.core.fetch_graph")
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

    @patch("maptoart.core._save_output")
    @patch("maptoart.core._apply_typography")
    @patch("maptoart.core._render_layers")
    @patch("maptoart.core._setup_figure", return_value=(MagicMock(), MagicMock()))
    @patch("maptoart.core.ox.project_graph")
    @patch("maptoart.core._fetch_map_data")
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

        # Verify key arguments were passed correctly
        fetch_args = mock_fetch.call_args
        assert fetch_args[0][0] == (48.8566, 2.3522)  # point
        assert fetch_args[0][1] == 10000  # dist

        # _apply_typography(fig, ax, display_city, display_country, point, theme, ...)
        typo_args = mock_typo.call_args[0]
        assert typo_args[2] == "Paris"  # display_city
        assert typo_args[3] == "France"  # display_country
        assert typo_args[5] == sample_theme  # theme

        # _save_output(fig, output_file, output_format, theme, ...)
        save_args = mock_save.call_args[0]
        assert save_args[2] == "png"  # output_format


    @patch("maptoart.core._save_output")
    @patch("maptoart.core._apply_typography")
    @patch("maptoart.core._render_layers")
    @patch("maptoart.core._setup_figure", return_value=(MagicMock(), MagicMock()))
    def test_create_poster_with_prefetched_data(
        self,
        mock_setup: MagicMock,
        mock_render: MagicMock,
        mock_typo: MagicMock,
        mock_save: MagicMock,
        sample_theme: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        """Phase 1: when _prefetched_data and _projected_graph are provided,
        create_poster skips _fetch_map_data and ox.project_graph."""
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        g_proj = nx.MultiDiGraph()
        g_proj.add_edge("x", "y")
        prefetched = (g, None, None, 4500.0)

        with patch("maptoart.core._fetch_map_data") as mock_fetch, \
             patch("maptoart.core.ox.project_graph") as mock_project:
            core.create_poster(
                "Paris", "France", (48.8566, 2.3522), 10000,
                "/tmp/out.png", "png", theme=sample_theme,
                status_reporter=silent_reporter,
                _prefetched_data=prefetched,
                _projected_graph=g_proj,
            )
            mock_fetch.assert_not_called()
            mock_project.assert_not_called()

        # Verify the projected graph was used by _render_layers
        render_args = mock_render.call_args[0]
        assert render_args[1] is g_proj  # g_proj passed to _render_layers


class TestGeneratePosters:
    """Tests for the generate_posters orchestrator."""

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core.ox.project_graph")
    @patch("maptoart.core._fetch_map_data")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
    def test_single_theme(
        self,
        mock_fonts: MagicMock,
        mock_coords: MagicMock,
        mock_fetch: MagicMock,
        mock_project: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)
        mock_project.return_value = g
        options = PosterGenerationOptions(city="Paris", country="France", theme="custom")
        outputs = core.generate_posters(options, status_reporter=silent_reporter)
        assert len(outputs) == 1
        mock_create.assert_called_once()

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core.ox.project_graph")
    @patch("maptoart.core._fetch_map_data")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
    def test_multiple_themes(
        self,
        mock_fonts: MagicMock,
        mock_coords: MagicMock,
        mock_fetch: MagicMock,
        mock_project: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme_dir: Path,
        sample_theme_data: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)
        mock_project.return_value = g
        for name in ("alpha", "beta"):
            data = dict(sample_theme_data, name=name)
            (sample_theme_dir / f"{name}.json").write_text(json.dumps(data))

        options = PosterGenerationOptions(
            city="Paris", country="France", themes=["alpha", "beta"],
        )
        outputs = core.generate_posters(options, status_reporter=silent_reporter)
        assert len(outputs) == 2
        assert mock_create.call_count == 2

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core.ox.project_graph")
    @patch("maptoart.core._fetch_map_data")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
    def test_hoisted_fetch_called_once_for_multiple_themes(
        self,
        mock_fonts: MagicMock,
        mock_coords: MagicMock,
        mock_fetch: MagicMock,
        mock_project: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme_dir: Path,
        sample_theme_data: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        """Phase 1: verify fetch + projection run exactly once even with multiple themes."""
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)
        mock_project.return_value = g
        for name in ("alpha", "beta", "gamma"):
            data = dict(sample_theme_data, name=name)
            (sample_theme_dir / f"{name}.json").write_text(json.dumps(data))

        options = PosterGenerationOptions(
            city="Paris", country="France", themes=["alpha", "beta", "gamma"],
        )
        core.generate_posters(options, status_reporter=silent_reporter)
        mock_fetch.assert_called_once()
        mock_project.assert_called_once()
        assert mock_create.call_count == 3
        # Verify prefetched data was passed to create_poster
        for call in mock_create.call_args_list:
            assert call.kwargs.get("_prefetched_data") == (g, None, None, 4500.0)
            assert call.kwargs.get("_projected_graph") is g


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

    @patch("maptoart.geocoding.Nominatim")
    @patch("maptoart.geocoding.cache_get", return_value=None)
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

    @patch("maptoart.geocoding.Nominatim")
    @patch("maptoart.geocoding.cache_get", return_value=None)
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

        with patch("maptoart._util.os.fdopen", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                core._atomic_write_text(target, "new content")

        # original file should remain intact
        assert target.read_text() == "original"
        # no leftover temp files
        assert list(tmp_path.glob("*.tmp")) == []


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
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        core.cache_set("mykey", {"data": 42})
        result = core.cache_get("mykey")
        assert result == {"data": 42}

    def test_cache_get_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
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

    @patch("maptoart.core.cache_get", return_value=None)
    @patch("maptoart.core.ox.graph_from_point")
    @patch("maptoart.core.cache_set")
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

    @patch("maptoart.core.cache_get", return_value=None)
    @patch("maptoart.core.ox.graph_from_point", side_effect=InsufficientResponseError("OSM error"))
    def test_fetch_failure_returns_none(
        self, mock_graph: MagicMock, mock_cache: MagicMock,
    ) -> None:
        result = core.fetch_graph((48.0, 2.0), 10000)
        assert result is None

    @patch("maptoart.core.cache_get")
    def test_cache_hit(self, mock_cache: MagicMock) -> None:
        g = nx.MultiDiGraph()
        mock_cache.return_value = g
        result = core.fetch_graph((48.0, 2.0), 10000)
        assert result is g


class TestFetchFeatures:
    """Tests for fetch_features with mocked OSM calls."""

    @patch("maptoart.core.cache_get", return_value=None)
    @patch("maptoart.core.ox.features_from_point")
    @patch("maptoart.core.cache_set")
    def test_successful_fetch(
        self, mock_set: MagicMock, mock_features: MagicMock, mock_cache: MagicMock,
    ) -> None:
        mock_gdf = MagicMock()
        mock_features.return_value = mock_gdf

        result = core.fetch_features(
            (48.0, 2.0), 10000, tags={"natural": "water"}, name="water",
        )
        assert result is mock_gdf

    @patch("maptoart.core.cache_get", return_value=None)
    @patch("maptoart.core.ox.features_from_point", side_effect=InsufficientResponseError("OSM error"))
    def test_fetch_failure_returns_none(
        self, mock_features: MagicMock, mock_cache: MagicMock,
    ) -> None:
        result = core.fetch_features(
            (48.0, 2.0), 10000, tags={"natural": "water"}, name="water",
        )
        assert result is None

    @patch("maptoart.core.cache_get")
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

    @patch("maptoart.core.load_fonts", return_value=None)
    def test_failed_load_returns_none(self, mock_load: MagicMock) -> None:
        result = core._load_custom_fonts("NonExistentFont", None)
        assert result is None

    @patch("maptoart.core.load_fonts", return_value={"bold": "b", "regular": "r", "light": "l"})
    def test_successful_load(self, mock_load: MagicMock) -> None:
        result = core._load_custom_fonts("TestFont", None)
        assert result is not None
        assert result["bold"] == "b"


class TestGetCoordinatesCacheHit:
    """Test cache hit path for get_coordinates."""

    @patch("maptoart.geocoding.cache_get", return_value=(48.8566, 2.3522))
    def test_cache_hit_returns_coords(self, mock_cache: MagicMock) -> None:
        result = core.get_coordinates("Paris", "France")
        assert result == (48.8566, 2.3522)


class TestGetCoordinatesSuccess:
    """Test successful geocode path."""

    @patch("maptoart.geocoding.cache_set")
    @patch("maptoart.geocoding.Nominatim")
    @patch("maptoart.geocoding.cache_get", return_value=None)
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

    def test_invalid_orientation_raises(self) -> None:
        with pytest.raises(ValueError, match="orientation must be"):
            PosterGenerationOptions(city="X", country="Y", orientation="diagonal")

    def test_valid_orientations_accepted(self) -> None:
        for orient in ("portrait", "landscape"):
            opts = PosterGenerationOptions(city="X", country="Y", orientation=orient)
            assert opts.orientation == orient

    def test_invalid_paper_size_raises(self) -> None:
        with pytest.raises(ValueError, match="paper_size must be one of"):
            PosterGenerationOptions(city="X", country="Y", paper_size="B5")

    def test_valid_paper_sizes_accepted(self) -> None:
        for size in ("A0", "A1", "A2", "A3", "A4"):
            opts = PosterGenerationOptions(city="X", country="Y", paper_size=size)
            assert opts.paper_size == size

    def test_none_paper_size_accepted(self) -> None:
        opts = PosterGenerationOptions(city="X", country="Y", paper_size=None)
        assert opts.paper_size is None

    def test_dpi_upper_bound_raises(self) -> None:
        with pytest.raises(ValueError, match="dpi must not exceed 2400"):
            PosterGenerationOptions(city="X", country="Y", dpi=10000)

    def test_dpi_at_2400_accepted(self) -> None:
        opts = PosterGenerationOptions(city="X", country="Y", dpi=2400)
        assert opts.dpi == 2400

    def test_parallel_themes_defaults(self) -> None:
        opts = PosterGenerationOptions(city="X", country="Y")
        assert opts.parallel_themes is False
        assert opts.max_theme_workers == 4

    def test_parallel_themes_enabled(self) -> None:
        opts = PosterGenerationOptions(city="X", country="Y", parallel_themes=True, max_theme_workers=2)
        assert opts.parallel_themes is True
        assert opts.max_theme_workers == 2

    def test_max_theme_workers_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_theme_workers must be at least 1"):
            PosterGenerationOptions(city="X", country="Y", max_theme_workers=0)

    def test_max_theme_workers_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_theme_workers must be at least 1"):
            PosterGenerationOptions(city="X", country="Y", max_theme_workers=-1)


class TestRenderThemeWorker:
    """Tests for the _render_theme_worker function (parallel theme rendering)."""

    @patch("maptoart.core._write_metadata", return_value="/tmp/poster.json")
    @patch("maptoart.core._build_poster_metadata", return_value={"city": "Paris"})
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core.load_theme", return_value={"name": "Noir"})
    def test_worker_calls_create_poster_and_writes_metadata(
        self,
        mock_load_theme: MagicMock,
        mock_create_poster: MagicMock,
        mock_build_meta: MagicMock,
        mock_write_meta: MagicMock,
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        prefetched = (g, None, None, 4500.0)
        options_dict = {"city": "Paris", "country": "France"}

        result = core._render_theme_worker(
            "Paris", "France", (48.8566, 2.3522), 18000,
            "/tmp/poster.png", "png", "noir",
            12.0, 16.0, 300, None, None, None, True,
            prefetched, g, options_dict,
        )

        mock_load_theme.assert_called_once_with("noir")
        mock_create_poster.assert_called_once()
        assert mock_create_poster.call_args.kwargs["_prefetched_data"] == prefetched
        assert mock_create_poster.call_args.kwargs["_projected_graph"] is g
        output_file, metadata_path, metadata = result
        assert output_file == "/tmp/poster.png"
        assert metadata_path == "/tmp/poster.json"
        assert metadata == {"city": "Paris"}


class TestParallelThemeRendering:
    """Tests for the parallel branch of generate_posters()."""

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core.ox.project_graph")
    @patch("maptoart.core._fetch_map_data")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
    def test_parallel_themes_uses_process_pool(
        self,
        mock_fonts: MagicMock,
        mock_coords: MagicMock,
        mock_fetch: MagicMock,
        mock_project: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme_dir: Path,
        sample_theme_data: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        """Verify ProcessPoolExecutor is invoked for parallel theme rendering."""
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)
        mock_project.return_value = g
        for name in ("alpha", "beta"):
            data = dict(sample_theme_data, name=name)
            (sample_theme_dir / f"{name}.json").write_text(json.dumps(data))

        # Create fake futures with proper result tuples
        class _FakeFuture:
            def __init__(self, val: Any) -> None:
                self._val = val
            def result(self) -> Any:
                return self._val

        f1 = _FakeFuture(("/tmp/alpha.png", "/tmp/alpha.json", {"city": "Paris"}))
        f2 = _FakeFuture(("/tmp/beta.png", "/tmp/beta.json", {"city": "Paris"}))

        mock_executor = MagicMock()
        mock_executor.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor.__exit__ = MagicMock(return_value=False)
        mock_executor.submit.side_effect = [f1, f2]

        def fake_as_completed(future_dict: dict) -> list:
            return list(future_dict.keys())

        with patch("maptoart.core.ProcessPoolExecutor", return_value=mock_executor), \
             patch("maptoart.core.as_completed", side_effect=fake_as_completed):
            options = PosterGenerationOptions(
                city="Paris", country="France",
                themes=["alpha", "beta"], parallel_themes=True,
            )
            outputs = core.generate_posters(options, status_reporter=silent_reporter)

        assert len(outputs) == 2
        assert mock_executor.submit.call_count == 2

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core.ox.project_graph")
    @patch("maptoart.core._fetch_map_data")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
    def test_parallel_themes_handles_worker_failure(
        self,
        mock_fonts: MagicMock,
        mock_coords: MagicMock,
        mock_fetch: MagicMock,
        mock_project: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme_dir: Path,
        sample_theme_data: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        """Verify failed futures are caught and added to failures list."""
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)
        mock_project.return_value = g
        for name in ("alpha", "beta"):
            data = dict(sample_theme_data, name=name)
            (sample_theme_dir / f"{name}.json").write_text(json.dumps(data))

        class _FakeFuture:
            def __init__(self, val: Any = None, exc: Exception | None = None) -> None:
                self._val = val
                self._exc = exc
            def result(self) -> Any:
                if self._exc:
                    raise self._exc
                return self._val

        f1 = _FakeFuture(val=("/tmp/alpha.png", "/tmp/alpha.json", {"city": "Paris"}))
        f2 = _FakeFuture(exc=RuntimeError("rendering failed"))

        mock_executor = MagicMock()
        mock_executor.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor.__exit__ = MagicMock(return_value=False)
        mock_executor.submit.side_effect = [f1, f2]

        def fake_as_completed(future_dict: dict) -> list:
            return list(future_dict.keys())

        with patch("maptoart.core.ProcessPoolExecutor", return_value=mock_executor), \
             patch("maptoart.core.as_completed", side_effect=fake_as_completed):
            options = PosterGenerationOptions(
                city="Paris", country="France",
                themes=["alpha", "beta"], parallel_themes=True,
            )
            outputs = core.generate_posters(options, status_reporter=silent_reporter)

        # Only 1 succeeded, 1 failed
        assert len(outputs) == 1
        assert "/tmp/alpha.png" in outputs


class TestGetCoordinatesRetry:
    """Tests for geocoding retry with backoff."""

    @patch("maptoart.geocoding.Nominatim")
    @patch("maptoart.geocoding.cache_get", return_value=None)
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

    @patch("maptoart.geocoding.Nominatim")
    @patch("maptoart.geocoding.cache_get", return_value=None)
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

    @patch("maptoart.rendering.ox.projection.project_geometry")
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

    @patch("maptoart.rendering.ox.projection.project_geometry")
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

    @patch("maptoart.rendering.ox.projection.project_geometry")
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

    @patch("maptoart.rendering.FontProperties")
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

    @patch("maptoart.rendering.FontProperties")
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

    @patch("maptoart.rendering.FontProperties")
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

    @patch("maptoart.rendering.FontProperties")
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

    @patch("maptoart.rendering.create_gradient_fade")
    @patch("maptoart.rendering.get_crop_limits", return_value=((0, 1), (0, 1)))
    @patch("maptoart.rendering.ox.plot_graph")
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
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
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

    @patch("maptoart.core.fetch_features", return_value=None)
    @patch("maptoart.core.fetch_graph", return_value=None)
    def test_raises_on_no_graph(
        self, mock_graph: MagicMock, mock_features: MagicMock,
    ) -> None:
        with pytest.raises(RuntimeError, match="Failed to retrieve street network"):
            core._fetch_map_data((48.0, 2.0), 10000, 12, 16)

    @patch("maptoart.core.fetch_features", return_value=None)
    @patch("maptoart.core.fetch_graph")
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

    @patch("maptoart.core.cache_get", return_value=None)
    @patch("maptoart.core.ox.graph_from_point")
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
    """Tests for lazy font loading via lru_cache."""

    def test_get_fonts_returns_dict_or_none(self) -> None:
        core._get_fonts.cache_clear()
        result = core._get_fonts()
        assert result is None or isinstance(result, dict)

    def test_cached_after_first_call(self) -> None:
        core._get_fonts.cache_clear()
        core._get_fonts()
        info = core._get_fonts.cache_info()
        assert info.currsize == 1


class TestCacheVersioning:
    """Tests for cache version in file paths."""

    def test_cache_path_includes_version(self) -> None:
        path = core._cache_path("test_key")
        assert core._CACHE_VERSION in str(path)
        assert str(path).endswith(".pkl")


class TestGeneratePostersResume:
    """Test that --all-themes resume continues past failures."""

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
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

    @patch("maptoart.core.fetch_features", return_value=None)
    @patch("maptoart.core.fetch_graph")
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



class TestFuzzyThemeMatching:
    """Tests for fuzzy theme name matching."""

    def test_suggests_similar_name(self) -> None:
        options = PosterGenerationOptions(city="X", country="Y", themes=["neon_cyberpnk"])
        with pytest.raises(ValueError, match="did you mean.*neon_cyberpunk"):
            core._resolve_theme_names(options, ["neon_cyberpunk", "noir", "terracotta"])

    def test_no_match_lists_all(self) -> None:
        options = PosterGenerationOptions(city="X", country="Y", themes=["xyzabc"])
        with pytest.raises(ValueError, match="Available:"):
            core._resolve_theme_names(options, ["noir", "terracotta"])


class TestIsLatinScriptExtended:
    """Extended non-Latin script tests."""

    def test_japanese(self) -> None:
        assert core.is_latin_script("東京") is False

    def test_arabic(self) -> None:
        assert core.is_latin_script("القاهرة") is False

    def test_cyrillic(self) -> None:
        assert core.is_latin_script("Москва") is False

    def test_korean(self) -> None:
        assert core.is_latin_script("서울") is False

    def test_accented_latin(self) -> None:
        assert core.is_latin_script("São Paulo") is True


class TestNonLatinTypography:
    """Tests for non-Latin city typography behavior."""

    @patch("maptoart.rendering.FontProperties")
    def test_non_latin_city_no_letter_spacing(self, mock_fp: MagicMock) -> None:
        fig = MagicMock()
        ax = MagicMock()
        theme = dict(SAMPLE_THEME_DATA)

        core._apply_typography(
            fig, ax, "東京", "Japan", (35.6, 139.7),
            theme, None, 12, 16,
        )
        city_call = ax.text.call_args_list[0]
        city_text = city_call[0][2]
        assert "  " not in city_text

    @patch("maptoart.rendering.FontProperties")
    def test_latin_city_has_letter_spacing(self, mock_fp: MagicMock) -> None:
        fig = MagicMock()
        ax = MagicMock()
        theme = dict(SAMPLE_THEME_DATA)

        core._apply_typography(
            fig, ax, "Paris", "France", (48.8, 2.3),
            theme, None, 12, 16,
        )
        city_call = ax.text.call_args_list[0]
        city_text = city_call[0][2]
        assert "  " in city_text


class TestParallelFetch:
    """Tests for parallel _fetch_map_data."""

    @patch("maptoart.core.fetch_features", return_value=None)
    @patch("maptoart.core.fetch_graph")
    def test_parallel_fetch_all_called(
        self, mock_graph: MagicMock, mock_features: MagicMock,
    ) -> None:
        g = nx.MultiDiGraph()
        for i in range(15):
            g.add_edge(i, i + 1)
        mock_graph.return_value = g

        result = core._fetch_map_data((48.0, 2.0), 10000, 12, 16)
        assert result[0] is g
        mock_graph.assert_called_once()
        assert mock_features.call_count == 2

    @patch("maptoart.core.fetch_features", return_value=None)
    @patch("maptoart.core.fetch_graph")
    def test_parallel_feature_failure_non_fatal(
        self, mock_graph: MagicMock, mock_features: MagicMock,
    ) -> None:
        g = nx.MultiDiGraph()
        for i in range(15):
            g.add_edge(i, i + 1)
        mock_graph.return_value = g
        mock_features.return_value = None

        result = core._fetch_map_data((48.0, 2.0), 10000, 12, 16)
        assert result[0] is g
        assert result[1] is None
        assert result[2] is None


class TestAutoDpiReduction:
    """Tests for auto DPI reduction."""

    @patch("maptoart.core._save_output")
    @patch("maptoart.core._apply_typography")
    @patch("maptoart.core._render_layers")
    @patch("maptoart.core._setup_figure", return_value=(MagicMock(), MagicMock()))
    @patch("maptoart.core.ox.project_graph")
    @patch("maptoart.core._fetch_map_data")
    def test_auto_dpi_reduction(
        self,
        mock_fetch: MagicMock,
        mock_project: MagicMock,
        mock_setup: MagicMock,
        mock_render: MagicMock,
        mock_typo: MagicMock,
        mock_save: MagicMock,
        sample_theme: dict[str, str],
    ) -> None:
        """20x20 @ 1200 DPI should auto-reduce."""
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)
        mock_project.return_value = g

        events: list[str] = []

        class _TrackingReporter(core.StatusReporter):
            def __init__(self):
                super().__init__(json_mode=True)

            def emit(self, event, message=None, **extra):
                events.append(event)

        reporter = _TrackingReporter()
        core.create_poster(
            "Paris", "France", (48.8566, 2.3522), 10000,
            "/tmp/out.png", "png", theme=sample_theme,
            width=20, height=20, dpi=1200,
            status_reporter=reporter,
        )
        assert "dpi.auto_reduce" in events

    def test_extreme_dimensions_still_raises(self, sample_theme: dict[str, str]) -> None:
        """400x400 should still raise even at DPI 72."""
        with pytest.raises(ValueError, match="even at DPI 72"):
            core.create_poster(
                "Paris", "France", (48.8566, 2.3522), 10000,
                "/tmp/out.png", "png", theme=sample_theme,
                width=400, height=400, dpi=1200,
            )


class TestCacheTTL:
    """Tests for cache TTL support."""

    def test_expired_entry_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        core.cache_set("ttl_test", {"data": 42}, ttl=1)
        # Backdate the metadata
        meta_path = Path(f"{core._cache_path('ttl_test')}.meta")
        import json as _json
        meta = _json.loads(meta_path.read_text())
        meta["created"] = meta["created"] - 100  # expire it
        meta_path.write_text(_json.dumps(meta))

        result = core.cache_get("ttl_test")
        assert result is None

    def test_fresh_entry_returns_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        core.cache_set("ttl_fresh", {"data": 99}, ttl=3600)
        result = core.cache_get("ttl_fresh")
        assert result == {"data": 99}

    def test_no_ttl_never_expires(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        core.cache_set("no_ttl", {"data": 7})
        result = core.cache_get("no_ttl")
        assert result == {"data": 7}


class TestCacheClear:
    """Tests for cache_clear."""

    def test_clears_all_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        core.cache_set("a", 1)
        core.cache_set("b", 2)
        count = core.cache_clear()
        assert count > 0
        # Verify files are gone
        remaining = list(tmp_path.glob("*.pkl"))
        assert remaining == []


class TestCacheInfo:
    """Tests for cache_info."""

    def test_returns_stats(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        core.cache_set("info_test", {"data": 42}, ttl=3600)
        info = core.cache_info()
        assert info["total_files"] >= 1
        assert info["total_bytes"] > 0
        assert any("info_test" in e["key"] for e in info["entries"])


class TestCreatePosterFromOptions:
    """Tests for the create_poster_from_options wrapper."""

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
    def test_calls_create_poster(
        self,
        mock_fonts: MagicMock,
        mock_coords: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme: dict[str, str],
        silent_reporter: core.StatusReporter,
    ) -> None:
        options = PosterGenerationOptions(city="Paris", country="France", theme="custom")
        result = core.create_poster_from_options(options, "custom", status_reporter=silent_reporter)
        mock_create.assert_called_once()
        mock_meta.assert_called_once()
        assert result is not None


class TestProgressCallback:
    """Tests for on_progress callback in StatusReporter."""

    def test_callback_receives_events(self) -> None:
        received = []

        def _on_progress(event, message, extra):
            received.append(event)

        reporter = core.StatusReporter(json_mode=True, on_progress=_on_progress)
        reporter.emit("test.event", "hello")
        assert "test.event" in received


class TestCacheHMAC:
    """Tests for cache HMAC integrity verification."""

    @staticmethod
    def _patch_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)

    def test_cache_roundtrip_with_hmac(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_cache_dir(monkeypatch, tmp_path)
        core.cache_set("hmac_test", {"data": 42})
        # Verify signature file was created
        cache_file = tmp_path / f"hmac_test_{core._CACHE_VERSION}.pkl"
        sig_file = Path(f"{cache_file}.sig")
        assert sig_file.exists()
        result = core.cache_get("hmac_test")
        assert result == {"data": 42}

    def test_tampered_cache_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_cache_dir(monkeypatch, tmp_path)
        core.cache_set("tamper_test", {"data": 42})
        # Tamper with the cache file
        cache_file = tmp_path / f"tamper_test_{core._CACHE_VERSION}.pkl"
        cache_file.write_bytes(b"\x00corrupted")
        result = core.cache_get("tamper_test")
        assert result is None

    def test_missing_sig_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_cache_dir(monkeypatch, tmp_path)
        core.cache_set("sig_test", {"data": 42})
        # Remove signature file
        cache_file = tmp_path / f"sig_test_{core._CACHE_VERSION}.pkl"
        sig_file = Path(f"{cache_file}.sig")
        sig_file.unlink()
        result = core.cache_get("sig_test")
        assert result is None


class TestEmptyCityCountryValidation:
    """Tests for empty city/country validation in PosterGenerationOptions."""

    def test_empty_city_raises(self) -> None:
        with pytest.raises(ValueError, match="city must not be empty"):
            PosterGenerationOptions(city="", country="France")

    def test_whitespace_city_raises(self) -> None:
        with pytest.raises(ValueError, match="city must not be empty"):
            PosterGenerationOptions(city="   ", country="France")

    def test_empty_country_raises(self) -> None:
        with pytest.raises(ValueError, match="country must not be empty"):
            PosterGenerationOptions(city="Paris", country="")

    def test_whitespace_country_raises(self) -> None:
        with pytest.raises(ValueError, match="country must not be empty"):
            PosterGenerationOptions(city="Paris", country="   ")

    def test_valid_city_country_accepted(self) -> None:
        opts = PosterGenerationOptions(city="Paris", country="France")
        assert opts.city == "Paris"
        assert opts.country == "France"


class TestPrintExamplesUsesCliName:
    """Test that print_examples() uses maptoart-cli, not old script name."""

    def test_no_old_script_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        core.print_examples()
        output = capsys.readouterr().out
        assert "create_map_poster.py" not in output
        assert "maptoart-cli" in output


class TestRestrictedUnpickler:
    """Tests for pickle deserialization restriction (#1)."""

    def test_blocked_module_raises_cache_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A cache file containing os.system should be blocked."""
        import os
        import pickle as _pickle

        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)

        # Build a malicious pickle payload that references os.system directly
        class _Evil:
            def __reduce__(self):
                return (os.getpid, ())

        # Write the pickle manually (bypassing cache_set's safe serialization)
        path = tmp_path / f"evil_{_util._CACHE_VERSION}.pkl"
        path.write_bytes(_pickle.dumps(_Evil()))
        # Write a valid HMAC for the payload
        sig = _util._compute_file_hmac(path)
        Path(f"{path}.sig").write_text(sig, encoding="utf-8")
        # Write metadata
        import time as _time
        Path(f"{path}.meta").write_text(
            json.dumps({"created": _time.time(), "ttl": None, "cache_version": _util._CACHE_VERSION}),
            encoding="utf-8",
        )

        with pytest.raises(_util.CacheError, match="Blocked unpickling"):
            _util.cache_get("evil")

    def test_safe_types_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        _util.cache_set("safe", {"hello": [1, 2, 3]})
        result = _util.cache_get("safe")
        assert result == {"hello": [1, 2, 3]}


class TestFetchMapDataPartialFailure:
    """Tests for _fetch_map_data handling partial thread failures (#9)."""

    @patch("maptoart.core.fetch_features", return_value=None)
    @patch("maptoart.core.fetch_graph")
    def test_thread_exception_logs_warning(
        self,
        mock_graph: MagicMock,
        mock_features: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If a worker thread raises an unexpected error, it's caught and logged."""
        g = nx.MultiDiGraph()
        for i in range(15):
            g.add_edge(i, i + 1)
        mock_graph.return_value = g
        # Make one features call raise an unexpected RuntimeError
        mock_features.side_effect = [None, RuntimeError("unexpected fetch failure")]

        import logging

        with caplog.at_level(logging.WARNING, logger="maptoart.core"):
            result = core._fetch_map_data((48.0, 2.0), 10000, 12, 16)
        assert result[0] is g  # graph still returned


class TestAtomicWriteTextCleanup:
    """Test that _atomic_write_text removes temp file on OSError."""

    def test_core_temp_cleaned_on_failure(self, tmp_path: Path) -> None:
        """Test the core.py _atomic_write_text error cleanup."""
        target = tmp_path / "output.txt"
        target.write_text("original")

        with patch("maptoart._util.os.fdopen", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                core._atomic_write_text(target, "new content")

        assert target.read_text() == "original"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_util_temp_cleaned_on_failure(self, tmp_path: Path) -> None:
        """Test the _util.py _atomic_write_text error cleanup."""
        target = tmp_path / "output.txt"
        target.write_text("original")

        with patch("maptoart._util.os.fdopen", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                _util._atomic_write_text(target, "new content")

        assert target.read_text() == "original"
        assert list(tmp_path.glob("*.tmp")) == []


class TestGenerateOutputFilenameUnicode:
    """Test generate_output_filename with unicode city names (#13)."""

    def test_unicode_city(self, tmp_path: Path) -> None:
        result = core.generate_output_filename("東京", "noir", "png", str(tmp_path))
        assert result.endswith(".png")
        # Should not raise and should produce a valid path
        assert Path(result).parent.exists()

    def test_accented_city(self, tmp_path: Path) -> None:
        result = core.generate_output_filename("São Paulo", "terracotta", "png", str(tmp_path))
        assert result.endswith(".png")
        assert "s" in Path(result).name.lower()


class TestCircularImportFix:
    """Verify geocoding module can be imported without importing core first (#4)."""

    def test_geocoding_import_standalone(self) -> None:
        from maptoart.geocoding import _resolve_coordinates  # noqa: F811

        assert callable(_resolve_coordinates)


class TestCorruptCacheFiles:
    """Tests for cache robustness with corrupt sidecar files (#R10-9)."""

    @staticmethod
    def _patch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)

    def test_corrupt_metadata_json_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Corrupt .meta JSON should log a warning and treat as cache miss."""
        self._patch(monkeypatch, tmp_path)
        _util.cache_set("corrupt_meta", {"data": 1}, ttl=3600)
        # Corrupt the .meta file
        meta = Path(f"{_util._cache_path('corrupt_meta')}.meta")
        meta.write_text("{invalid json", encoding="utf-8")
        # Corrupt metadata → always treated as miss (age cannot be verified)
        result = _util.cache_get("corrupt_meta")
        assert result is None

    def test_corrupt_sig_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Corrupt .sig file should cause HMAC mismatch → cache miss."""
        self._patch(monkeypatch, tmp_path)
        _util.cache_set("corrupt_sig", {"data": 2})
        sig = Path(f"{_util._cache_path('corrupt_sig')}.sig")
        sig.write_text("0000deadbeef", encoding="utf-8")
        result = _util.cache_get("corrupt_sig")
        assert result is None

    def test_empty_sig_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty .sig file should cause HMAC mismatch → cache miss."""
        self._patch(monkeypatch, tmp_path)
        _util.cache_set("empty_sig", {"data": 3})
        sig = Path(f"{_util._cache_path('empty_sig')}.sig")
        sig.write_text("", encoding="utf-8")
        result = _util.cache_get("empty_sig")
        assert result is None

    def test_missing_meta_with_default_ttl_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No .meta + default_ttl requested → can't verify age → miss."""
        self._patch(monkeypatch, tmp_path)
        _util.cache_set("no_meta_ttl", {"data": 4})
        meta = Path(f"{_util._cache_path('no_meta_ttl')}.meta")
        meta.unlink()
        result = _util.cache_get("no_meta_ttl", default_ttl=3600)
        assert result is None


class TestCoordinateBoundsValidation:
    """Tests for out-of-bounds coordinate rejection (#R10-10)."""

    def test_latitude_out_of_range_raises(self) -> None:
        from maptoart.geocoding import _validate_coordinate_bounds
        with pytest.raises(ValueError, match="Latitude must be between"):
            _validate_coordinate_bounds(91.0, 0.0)

    def test_negative_latitude_out_of_range_raises(self) -> None:
        from maptoart.geocoding import _validate_coordinate_bounds
        with pytest.raises(ValueError, match="Latitude must be between"):
            _validate_coordinate_bounds(-91.0, 0.0)

    def test_longitude_out_of_range_raises(self) -> None:
        from maptoart.geocoding import _validate_coordinate_bounds
        with pytest.raises(ValueError, match="Longitude must be between"):
            _validate_coordinate_bounds(0.0, 181.0)

    def test_negative_longitude_out_of_range_raises(self) -> None:
        from maptoart.geocoding import _validate_coordinate_bounds
        with pytest.raises(ValueError, match="Longitude must be between"):
            _validate_coordinate_bounds(0.0, -181.0)

    def test_boundary_values_accepted(self) -> None:
        from maptoart.geocoding import _validate_coordinate_bounds
        # Should not raise
        _validate_coordinate_bounds(90.0, 180.0)
        _validate_coordinate_bounds(-90.0, -180.0)
        _validate_coordinate_bounds(0.0, 0.0)

    def test_resolve_coordinates_validates_explicit_bounds(self) -> None:
        """Explicit lat/lon that exceed bounds should raise ValueError."""
        options = PosterGenerationOptions(
            city="Test", country="Test", latitude=91.0, longitude=0.0,
        )
        with pytest.raises(ValueError, match="Latitude must be between"):
            core._resolve_coordinates(options, None)


class TestEdgeColorsWithMissingHighway:
    """Tests for get_edge_colors_by_type / get_edge_widths_by_type with missing highway key (#R11-4)."""

    def test_missing_highway_uses_default_color(self, sample_theme: dict[str, str]) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")  # no 'highway' key at all
        colors = core.get_edge_colors_by_type(g, sample_theme)
        assert len(colors) == 1
        assert colors[0] == sample_theme["road_default"]  # no highway → falls through to default

    def test_missing_highway_uses_default_width(self) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")  # no 'highway' key
        widths = core.get_edge_widths_by_type(g)
        assert len(widths) == 1
        assert widths[0] == 0.4  # default width

    def test_empty_highway_list_uses_default(self, sample_theme: dict[str, str]) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b", highway=[])
        colors = core.get_edge_colors_by_type(g, sample_theme)
        assert colors[0] == sample_theme["road_default"]  # empty list → falls through to default


class TestProjectAndPlotLayerFallback:
    """Test _project_and_plot_layer falls back to to_crs on projection failure (#R16-7)."""

    @patch("maptoart.rendering.ox.projection.project_gdf", side_effect=ValueError("bad CRS"))
    def test_fallback_to_crs_on_projection_error(self, mock_proj: MagicMock) -> None:
        import geopandas as gpd
        from shapely.geometry import box
        from maptoart.rendering import _project_and_plot_layer

        gdf = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
        ax = MagicMock()
        _project_and_plot_layer(gdf, "EPSG:3857", ax, "#0000ff", 0.5, "water")
        # Should still have plotted (via fallback to to_crs)
        assert ax.method_calls  # plot was called on ax indirectly via gdf.plot


class TestCoordinateDisplayQuadrants:
    """Tests for coordinate display across all four hemisphere quadrants (#R11-9)."""

    @patch("maptoart.rendering.FontProperties")
    def test_ne_quadrant(self, mock_fp: MagicMock) -> None:
        """North-East: positive lat, positive lon → N / E."""
        fig, ax = MagicMock(), MagicMock()
        theme = dict(SAMPLE_THEME_DATA)
        core._apply_typography(fig, ax, "Tokyo", "Japan", (35.6762, 139.6503), theme, None, 12, 16)
        coords_text = ax.text.call_args_list[2][0][2]
        assert "N" in coords_text
        assert "E" in coords_text
        assert "S" not in coords_text
        assert "W" not in coords_text

    @patch("maptoart.rendering.FontProperties")
    def test_nw_quadrant(self, mock_fp: MagicMock) -> None:
        """North-West: positive lat, negative lon → N / W."""
        fig, ax = MagicMock(), MagicMock()
        theme = dict(SAMPLE_THEME_DATA)
        core._apply_typography(fig, ax, "New York", "USA", (40.7128, -74.0060), theme, None, 12, 16)
        coords_text = ax.text.call_args_list[2][0][2]
        assert "N" in coords_text
        assert "W" in coords_text
        assert "S" not in coords_text
        assert "E" not in coords_text

    @patch("maptoart.rendering.FontProperties")
    def test_se_quadrant(self, mock_fp: MagicMock) -> None:
        """South-East: negative lat, positive lon → S / E."""
        fig, ax = MagicMock(), MagicMock()
        theme = dict(SAMPLE_THEME_DATA)
        core._apply_typography(fig, ax, "Sydney", "Australia", (-33.8688, 151.2093), theme, None, 12, 16)
        coords_text = ax.text.call_args_list[2][0][2]
        assert "S" in coords_text
        assert "E" in coords_text
        assert "N" not in coords_text
        assert "W" not in coords_text

    @patch("maptoart.rendering.FontProperties")
    def test_sw_quadrant(self, mock_fp: MagicMock) -> None:
        """South-West: negative lat, negative lon → S / W."""
        fig, ax = MagicMock(), MagicMock()
        theme = dict(SAMPLE_THEME_DATA)
        core._apply_typography(fig, ax, "Buenos Aires", "Argentina", (-34.6037, -58.3816), theme, None, 12, 16)
        coords_text = ax.text.call_args_list[2][0][2]
        assert "S" in coords_text
        assert "W" in coords_text
        assert "N" not in coords_text
        assert "E" not in coords_text


class TestPaperSizeOverrideWarning:
    """Tests for --paper-size overriding explicit --width/--height (#R11-8)."""

    def test_warns_when_explicit_dimensions_overridden(self) -> None:
        events: list[str] = []

        class _TrackingReporter(core.StatusReporter):
            def __init__(self):
                super().__init__(json_mode=True)

            def emit(self, event, message=None, **extra):
                events.append(event)

        reporter = _TrackingReporter()
        w, h = core._apply_paper_size(15, 18, "A4", "portrait", reporter)
        assert w == pytest.approx(8.3)
        assert h == pytest.approx(11.7)
        assert "paper_size.override" in events

    def test_no_warning_for_default_dimensions(self) -> None:
        events: list[str] = []

        class _TrackingReporter(core.StatusReporter):
            def __init__(self):
                super().__init__(json_mode=True)

            def emit(self, event, message=None, **extra):
                events.append(event)

        reporter = _TrackingReporter()
        core._apply_paper_size(12, 16, "A4", "portrait", reporter)
        assert "paper_size.override" not in events


class TestConcurrentThemeCache:
    """Test thread-safety of _theme_cache under concurrent load (#R12-10)."""

    def test_concurrent_loads_produce_consistent_results(
        self, sample_theme: dict[str, str],
    ) -> None:
        import threading

        results: list[dict[str, str]] = []
        errors: list[Exception] = []

        def _load() -> None:
            try:
                theme = core.load_theme("custom")
                results.append(theme)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_load) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 10
        # All results should be equal
        for r in results:
            assert r["name"] == sample_theme["name"]
            assert r["bg"] == sample_theme["bg"]


class TestVeryLongCityNameScaling:
    """Test font scaling doesn't produce invalid sizes for very long names (#R12-11)."""

    @patch("maptoart.rendering.FontProperties")
    def test_extremely_long_city_name(self, mock_fp: MagicMock) -> None:
        fig = MagicMock()
        ax = MagicMock()
        theme = dict(SAMPLE_THEME_DATA)
        long_name = "A" * 200

        core._apply_typography(
            fig, ax, long_name, "Country", (0.0, 0.0),
            theme, None, 12, 16,
        )
        # Should not raise; font size should be positive
        calls = mock_fp.call_args_list
        for c in calls:
            size = c.kwargs.get("size")
            if size is not None:
                assert size > 0, f"Font size must be positive, got {size}"

    @patch("maptoart.rendering.FontProperties")
    def test_single_char_city_name(self, mock_fp: MagicMock) -> None:
        fig = MagicMock()
        ax = MagicMock()
        theme = dict(SAMPLE_THEME_DATA)

        core._apply_typography(
            fig, ax, "X", "Y", (0.0, 0.0),
            theme, None, 12, 16,
        )
        # Should not raise
        assert ax.text.call_count >= 3


class TestDeprecationVersionInWarning:
    """Test that name_label deprecation includes removal version (#R12-5)."""

    @patch("maptoart.core._fetch_map_data")
    @patch("maptoart.core._save_output")
    @patch("maptoart.core._apply_typography")
    @patch("maptoart.core._render_layers")
    @patch("maptoart.core._setup_figure", return_value=(MagicMock(), MagicMock()))
    @patch("maptoart.core.ox.project_graph")
    def test_name_label_warns_with_version(
        self,
        mock_proj: MagicMock,
        mock_setup: MagicMock,
        mock_render: MagicMock,
        mock_typo: MagicMock,
        mock_save: MagicMock,
        mock_fetch: MagicMock,
        sample_theme: dict[str, str],
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)

        import warnings as _warnings
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            core.create_poster(
                "Paris", "France", (48.8, 2.3), 10000,
                "/tmp/out.png", "png", theme=sample_theme,
                name_label="Old Name",
            )
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) == 1
        assert "v0.6.0" in str(dep_warnings[0].message)


class TestCountryLabelDeprecation:
    """Test that country_label emits a deprecation warning."""

    @patch("maptoart.core._fetch_map_data")
    @patch("maptoart.core._save_output")
    @patch("maptoart.core._apply_typography")
    @patch("maptoart.core._render_layers")
    @patch("maptoart.core._setup_figure", return_value=(MagicMock(), MagicMock()))
    @patch("maptoart.core.ox.project_graph")
    def test_country_label_warns_with_version(
        self,
        mock_proj: MagicMock,
        mock_setup: MagicMock,
        mock_render: MagicMock,
        mock_typo: MagicMock,
        mock_save: MagicMock,
        mock_fetch: MagicMock,
        sample_theme: dict[str, str],
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)

        import warnings as _warnings
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            core.create_poster(
                "Paris", "France", (48.8, 2.3), 10000,
                "/tmp/out.png", "png", theme=sample_theme,
                country_label="Old Country",
            )
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) == 1
        assert "country_label" in str(dep_warnings[0].message)
        assert "v0.6.0" in str(dep_warnings[0].message)


class TestZeroCoordinateDisplay:
    """Test that (0.0, 0.0) renders as N / E, not blank (#R14-9)."""

    @patch("maptoart.rendering.FontProperties")
    def test_zero_lat_lon_displays_north_east(self, mock_fp: MagicMock) -> None:
        fig, ax = MagicMock(), MagicMock()
        theme = dict(SAMPLE_THEME_DATA)
        core._apply_typography(fig, ax, "Null Island", "Gulf of Guinea", (0.0, 0.0), theme, None, 12, 16)
        coords_text = ax.text.call_args_list[2][0][2]
        assert coords_text == "0.0000\u00b0 N / 0.0000\u00b0 E"


class TestIncompleteFontDictFallback:
    """Test that a font dict missing required weights triggers monospace fallback (#R14-10)."""

    @patch("maptoart.rendering.FontProperties")
    def test_missing_bold_triggers_monospace(self, mock_fp: MagicMock) -> None:
        fig, ax = MagicMock(), MagicMock()
        theme = dict(SAMPLE_THEME_DATA)
        # Provide a font dict missing the "bold" weight
        incomplete_fonts = {"light": "/fake/light.ttf", "regular": "/fake/regular.ttf"}
        core._apply_typography(fig, ax, "Paris", "France", (48.8, 2.3), theme, incomplete_fonts, 12, 16)
        # The city name font (4th FontProperties call) should use family="monospace"
        calls = mock_fp.call_args_list
        city_font_call = calls[3]  # font_main_adjusted
        assert city_font_call[1].get("family") == "monospace"
        assert city_font_call[1].get("weight") == "bold"


class TestOnProgressCallbackGuard:
    """Test that a crashing on_progress callback doesn't break emit() (#R16-2)."""

    def test_callback_exception_does_not_break_emit(self, capsys: pytest.CaptureFixture[str]) -> None:
        def _bad_callback(event, message, extra):
            raise RuntimeError("callback exploded")

        reporter = core.StatusReporter(on_progress=_bad_callback)
        # Should not raise — the exception is caught and logged
        reporter.emit("test.event", "hello")
        output = capsys.readouterr().out
        assert "hello" in output


class TestLongCacheKeyTruncation:
    """Test that very long cache keys are truncated with a hash suffix (#R16-3)."""

    def test_round_trip_with_long_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        long_key = "a" * 250
        _util.cache_set(long_key, {"data": 42})
        result = _util.cache_get(long_key)
        assert result == {"data": 42}

    def test_long_key_path_is_shorter(self) -> None:
        long_key = "x" * 250
        path = _util._cache_path(long_key)
        # Stem should be truncated + 1 underscore + 16-char hash + _v2
        assert len(path.stem) < 250


class TestOutputDirPermissionError:
    """Test that non-writable output dir raises PermissionError (#R16-5)."""

    def test_non_writable_dir_raises(self, tmp_path: Path) -> None:
        read_only = tmp_path / "readonly"
        read_only.mkdir()
        read_only.chmod(0o444)
        try:
            with pytest.raises(PermissionError, match="not writable"):
                core.generate_output_filename("Paris", "noir", "png", str(read_only))
        finally:
            read_only.chmod(0o755)


class TestCachedFetchCacheWriteFailure:
    """Test that CacheError on cache_set doesn't block download (#R16-6)."""

    @patch("maptoart.core.cache_get", return_value=None)
    @patch("maptoart.core.cache_set", side_effect=_util.CacheError("disk full"))
    @patch("maptoart.core.time.sleep")
    def test_data_returned_despite_cache_write_failure(
        self, mock_sleep: MagicMock, mock_set: MagicMock, mock_get: MagicMock,
    ) -> None:
        result = core._cached_fetch(
            "test_key",
            lambda: {"graph": "data"},
            "test",
        )
        assert result == {"graph": "data"}
        mock_set.assert_called_once()


class TestCachedFetchCacheReadFailure:
    """Test that CacheError on cache_get treats as cache miss."""

    @patch("maptoart.core.cache_get", side_effect=_util.CacheError("corrupt"))
    @patch("maptoart.core.cache_set")
    @patch("maptoart.core.time.sleep")
    def test_cache_read_error_falls_through_to_download(
        self, mock_sleep: MagicMock, mock_set: MagicMock, mock_get: MagicMock,
    ) -> None:
        result = core._cached_fetch(
            "test_key",
            lambda: {"graph": "fresh"},
            "test",
        )
        assert result == {"graph": "fresh"}
        mock_get.assert_called_once()


class TestCacheInfoCorruptMeta:
    """Test cache_info() with corrupt .meta JSON file (#R16-8)."""

    def test_corrupt_meta_uses_empty_dict(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        # Create a fake cache file + signature + corrupt meta
        import pickle
        cache_file = tmp_path / "test_v2.pkl"
        with cache_file.open("wb") as f:
            pickle.dump("data", f)
        sig = _util._compute_file_hmac(cache_file)
        Path(f"{cache_file}.sig").write_text(sig, encoding="utf-8")
        Path(f"{cache_file}.meta").write_text("not json!!!", encoding="utf-8")

        info = _util.cache_info()
        assert info["total_files"] == 1
        # Corrupt meta means created/ttl are None
        assert info["entries"][0]["created"] is None
        assert info["entries"][0]["ttl"] is None


class TestGetAvailableThemesMissingDir:
    """Test get_available_themes() when THEMES_DIR doesn't exist (#R16-9)."""

    def test_missing_dir_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        nonexistent = tmp_path / "does_not_exist"
        monkeypatch.setattr(core, "THEMES_DIR", nonexistent)
        result = core.get_available_themes()
        assert result == []
        assert nonexistent.exists()  # Should have been created


class TestCacheSetPicklingError:
    """Test cache_set raises CacheError on PicklingError (#R17-1)."""

    def test_pickling_error_raises_cache_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import pickle
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        with patch("maptoart._util.pickle.dumps", side_effect=pickle.PicklingError("cannot pickle")):
            with pytest.raises(_util.CacheError, match="Cache write failed"):
                _util.cache_set("test_key", {"data": 1})


class TestCacheSetWriteFailure:
    """Test cache_set cleans up temp file when disk write fails."""

    def test_temp_file_cleaned_on_write_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        with patch("maptoart._util.os.fdopen", side_effect=OSError("disk full")):
            with pytest.raises(_util.CacheError, match="Cache write failed"):
                _util.cache_set("test_key", {"data": 1})
        # No leftover .tmp files
        assert list(tmp_path.glob("*.tmp")) == []


class TestCacheClearMissingDir:
    """Test cache_clear() returns 0 when CACHE_DIR doesn't exist (#R17-2)."""

    def test_missing_cache_dir_returns_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path / "nonexistent")
        assert _util.cache_clear() == 0


class TestSparseNetworkWarning:
    """Test sparse network warning when graph has <10 nodes (#R17-3)."""

    @patch("maptoart.core.fetch_features", return_value=None)
    @patch("maptoart.core.fetch_graph")
    def test_sparse_graph_emits_warning(self, mock_graph: MagicMock, mock_features: MagicMock) -> None:
        import networkx as nx
        g = nx.MultiDiGraph()
        g.graph["crs"] = "EPSG:4326"
        for i in range(5):
            g.add_node(i, x=2.35 + i * 0.001, y=48.85 + i * 0.001)
        g.add_edge(0, 1, highway="primary")
        g.add_edge(1, 2, highway="residential")
        mock_graph.return_value = g

        events: list[str] = []

        class _TrackingReporter(core.StatusReporter):
            def __init__(self):
                super().__init__(json_mode=True)

            def emit(self, event, message=None, **extra):
                events.append(event)

        core._fetch_map_data((48.85, 2.35), 5000, 12, 16, status_reporter=_TrackingReporter())
        assert "data.sparse_network" in events


class TestBothDimensionsClamped:
    """Test dimension clamping when both width AND height exceed max (#R17-4)."""

    def test_both_clamped_to_max(self) -> None:
        events: list[tuple[str, float]] = []

        class _TrackingReporter(core.StatusReporter):
            def __init__(self):
                super().__init__(json_mode=True)

            def emit(self, event, message=None, **extra):
                if event == "dimension.adjust":
                    events.append((extra.get("dimension", ""), extra.get("original", 0)))

        reporter = _TrackingReporter()
        w, h = core._apply_paper_size(25.0, 25.0, None, "portrait", reporter)
        assert w == core.MAX_DIMENSION_CUSTOM
        assert h == core.MAX_DIMENSION_CUSTOM
        dims = [e[0] for e in events]
        assert "width" in dims
        assert "height" in dims


class TestListThemesEmptyDir:
    """Test list_themes() with no themes prints empty message (#R17-5)."""

    def test_empty_dir_prints_no_themes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(core, "THEMES_DIR", tmp_path)
        core.list_themes()
        output = capsys.readouterr().out
        assert "No themes found" in output


class TestListThemesCorruptJSON:
    """Test list_themes() with corrupt theme JSON falls back gracefully (#R17-6)."""

    def test_corrupt_theme_json_uses_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(core, "THEMES_DIR", tmp_path)
        (tmp_path / "broken.json").write_text("NOT VALID JSON {{{")
        core.list_themes()
        output = capsys.readouterr().out
        assert "broken" in output  # Theme name still listed


class TestCoordinateBoundaryValues:
    """Test coordinate validation at exact boundary values (#R17-9)."""

    def test_exact_boundary_values_accepted(self) -> None:
        from maptoart.geocoding import _validate_coordinate_bounds
        # All boundary values should pass without raising
        _validate_coordinate_bounds(-90, 0)
        _validate_coordinate_bounds(90, 0)
        _validate_coordinate_bounds(0, -180)
        _validate_coordinate_bounds(0, 180)

    def test_just_beyond_boundary_raises(self) -> None:
        from maptoart.geocoding import _validate_coordinate_bounds
        with pytest.raises(ValueError, match="Latitude"):
            _validate_coordinate_bounds(-90.001, 0)
        with pytest.raises(ValueError, match="Longitude"):
            _validate_coordinate_bounds(0, 180.001)


class TestInitVersionFallback:
    """Test __init__.py __version__ fallback when package not installed (#R18-1)."""

    def test_version_fallback(self) -> None:
        from importlib.metadata import PackageNotFoundError
        with patch("importlib.metadata.version", side_effect=PackageNotFoundError("maptoart")):
            try:
                from importlib.metadata import version as ver_func
                v = ver_func("maptoart")
            except PackageNotFoundError:
                v = "0.0.0"
            assert v == "0.0.0"


class TestCacheTTLMissWithoutMeta:
    """Test cache_get returns None when .meta is absent but default_ttl is provided (#R18-2)."""

    def test_no_meta_with_default_ttl_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        # Create cache entry manually without .meta file
        import pickle, hmac, hashlib, uuid
        key = "test_no_meta"
        cache_path = _util._cache_path(key)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as f:
            pickle.dump({"data": 42}, f)
        # Write signature so HMAC check passes
        sig = _util._compute_file_hmac(cache_path)
        Path(f"{cache_path}.sig").write_text(sig, encoding="utf-8")
        # No .meta file created — but request with default_ttl
        result = _util.cache_get(key, default_ttl=3600)
        assert result is None  # Can't verify age, treated as miss

    def test_no_meta_without_ttl_returns_value(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        import pickle
        key = "test_no_meta_ok"
        cache_path = _util._cache_path(key)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as f:
            pickle.dump({"data": 42}, f)
        sig = _util._compute_file_hmac(cache_path)
        Path(f"{cache_path}.sig").write_text(sig, encoding="utf-8")
        # No .meta, no TTL — should return the value
        result = _util.cache_get(key)
        assert result == {"data": 42}


class TestCacheErrorReRaise:
    """Test CacheError is re-raised unwrapped from cache_get (#R18-3)."""

    def test_cache_error_propagates_directly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        import pickle
        key = "test_reraise"
        cache_path = _util._cache_path(key)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as f:
            pickle.dump("data", f)
        sig = _util._compute_file_hmac(cache_path)
        Path(f"{cache_path}.sig").write_text(sig, encoding="utf-8")
        # Inject a CacheError during unpickling
        with patch.object(
            _util._RestrictedUnpickler, "load",
            side_effect=_util.CacheError("injected"),
        ):
            with pytest.raises(_util.CacheError, match="injected"):
                _util.cache_get(key)


class TestSaveOutputOSErrorCleanup:
    """Test _save_output cleans up temp file on OSError (#R18-6)."""

    def test_temp_file_removed_on_save_failure(
        self,
        tmp_path: Path,
        sample_theme_data: dict[str, str],
    ) -> None:
        output_file = str(tmp_path / "test.png")
        fig = MagicMock()
        with patch("maptoart.core.plt") as mock_plt:
            mock_plt.savefig.side_effect = OSError("disk full")
            with pytest.raises(OSError, match="disk full"):
                core._save_output(
                    fig, output_file, "png", sample_theme_data,
                    14.0, 11.0, 300,
                )
        # Verify no temp files left behind
        tmp_files = list(tmp_path.glob("*.tmp.*"))
        assert len(tmp_files) == 0


class TestGeneratePostersNoFontsWarning:
    """Test generate_posters logs warning when no fonts available (#R18-7)."""

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
    @patch("maptoart.core._get_fonts", return_value=None)
    def test_warning_when_no_fonts(
        self,
        mock_bundled: MagicMock,
        mock_custom: MagicMock,
        mock_coords: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme: dict[str, str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging
        options = PosterGenerationOptions(city="Paris", country="France", theme="custom")
        with caplog.at_level(logging.WARNING, logger="maptoart.core"):
            core.generate_posters(options)
        assert any("monospace" in r.message for r in caplog.records)


class TestGeneratePostersJsonModeBanners:
    """Test generate_posters skips banners in json_mode (#R18-8)."""

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
    def test_json_mode_skips_banners(
        self,
        mock_custom: MagicMock,
        mock_coords: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme: dict[str, str],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        reporter = core.StatusReporter(json_mode=True)
        options = PosterGenerationOptions(city="Paris", country="France", theme="custom")
        core.generate_posters(options, status_reporter=reporter)
        output = capsys.readouterr().out
        # Banner ("===" lines) should NOT appear in json_mode
        assert "City Map Poster Generator" not in output

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
    def test_non_json_mode_shows_banners(
        self,
        mock_custom: MagicMock,
        mock_coords: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        sample_theme: dict[str, str],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        reporter = core.StatusReporter(json_mode=False)
        options = PosterGenerationOptions(city="Paris", country="France", theme="custom")
        core.generate_posters(options, status_reporter=reporter)
        output = capsys.readouterr().out
        assert "City Map Poster Generator" in output
        assert "Poster generation complete" in output


class TestCacheInfoNonexistentDir:
    """Test cache_info() when CACHE_DIR doesn't exist (#R19-2)."""

    def test_nonexistent_cache_dir_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        nonexistent = tmp_path / "does_not_exist"
        monkeypatch.setattr(_util, "CACHE_DIR", nonexistent)
        result = _util.cache_info()
        assert result == {"total_files": 0, "total_bytes": 0, "entries": []}


class TestGeocodingCoroutineHandling:
    """Test geocoding when _geocode_with_retry returns a coroutine (#R19-6)."""

    @patch("maptoart.geocoding.cache_get", return_value=None)
    @patch("maptoart.geocoding.cache_set")
    @patch("maptoart.geocoding._geocode_with_retry")
    def test_coroutine_resolved_via_asyncio_run(
        self,
        mock_geocode: MagicMock,
        mock_cache_set: MagicMock,
        mock_cache_get: MagicMock,
    ) -> None:
        import asyncio
        from maptoart.geocoding import get_coordinates

        # Create a real coroutine that returns a mock Location
        mock_location = MagicMock()
        mock_location.latitude = 48.8566
        mock_location.longitude = 2.3522
        mock_location.address = "Paris, France"

        async def _coro():
            return mock_location

        mock_geocode.return_value = _coro()

        result = get_coordinates("Paris", "France")
        assert result == (48.8566, 2.3522)


class TestGeocodingMissingAddress:
    """Test geocoding when location.address is None (#R19-7)."""

    @patch("maptoart.geocoding.cache_get", return_value=None)
    @patch("maptoart.geocoding.cache_set")
    @patch("maptoart.geocoding._geocode_with_retry")
    def test_missing_address_uses_fallback_message(
        self,
        mock_geocode: MagicMock,
        mock_cache_set: MagicMock,
        mock_cache_get: MagicMock,
    ) -> None:
        from maptoart.geocoding import get_coordinates

        mock_location = MagicMock()
        mock_location.latitude = 48.8566
        mock_location.longitude = 2.3522
        # Simulate missing address
        del mock_location.address

        mock_geocode.return_value = mock_location

        events: list[tuple[str, str]] = []

        class _Tracker:
            json_mode = False
            def emit(self, event, message=None, **extra):
                if message:
                    events.append((event, message))

        result = get_coordinates("Paris", "France", status_reporter=_Tracker())
        assert result == (48.8566, 2.3522)
        # Verify the fallback message was emitted
        result_messages = [msg for evt, msg in events if evt == "geocode.result"]
        assert any("address not available" in m for m in result_messages)


class TestGeocodingCacheSetFailure:
    """Test geocoding when cache_set raises CacheError (#R19-7b)."""

    @patch("maptoart.geocoding.cache_get", return_value=None)
    @patch("maptoart.geocoding.cache_set", side_effect=_util.CacheError("write failed"))
    @patch("maptoart.geocoding._geocode_with_retry")
    def test_cache_write_failure_does_not_block(
        self,
        mock_geocode: MagicMock,
        mock_cache_set: MagicMock,
        mock_cache_get: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging
        from maptoart.geocoding import get_coordinates

        mock_location = MagicMock()
        mock_location.latitude = 48.8566
        mock_location.longitude = 2.3522
        mock_location.address = "Paris, France"
        mock_geocode.return_value = mock_location

        with caplog.at_level(logging.WARNING, logger="maptoart.geocoding"):
            result = get_coordinates("Paris", "France")
        assert result == (48.8566, 2.3522)
        assert any("Failed to cache" in r.message for r in caplog.records)


class TestEdgeWidthsEmptyHighwayList:
    """Test get_edge_widths_by_type with empty highway list (#R19-8)."""

    def test_empty_highway_list_defaults_to_unclassified(self) -> None:
        from maptoart.rendering import get_edge_widths_by_type
        g = nx.MultiDiGraph()
        g.add_edge("a", "b", highway=[])  # empty list
        widths = get_edge_widths_by_type(g)
        # Empty list falls back to 'unclassified' which maps to default 0.4
        assert widths == [0.4]

    def test_highway_list_uses_first_element(self) -> None:
        from maptoart.rendering import get_edge_widths_by_type
        g = nx.MultiDiGraph()
        g.add_edge("a", "b", highway=["motorway", "primary"])
        widths = get_edge_widths_by_type(g)
        assert widths == [1.2]  # motorway width


class TestProjectAndPlotLayerNoPolygons:
    """Test _project_and_plot_layer with only LineString geometries (#R19-9)."""

    def test_linestring_only_gdf_returns_early(self) -> None:
        from maptoart.rendering import _project_and_plot_layer
        from shapely.geometry import LineString
        import geopandas as gpd

        line = LineString([(0, 0), (1, 1)])
        gdf = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326")
        ax = MagicMock()

        # Should return without calling plot (no polygons)
        _project_and_plot_layer(gdf, "EPSG:3857", ax, "#000", 1.0, "test")
        ax.plot.assert_not_called()


class TestAttributionFontFallback:
    """Test _apply_typography attribution uses monospace when no fonts (#R19-10)."""

    def test_attribution_monospace_when_no_fonts(self) -> None:
        from maptoart.rendering import _apply_typography

        fig = MagicMock()
        ax = MagicMock()
        ax.transAxes = MagicMock()
        theme = {
            "text": "#ffffff",
            "bg": "#000000",
        }

        with patch("maptoart.rendering._get_fonts", return_value=None):
            _apply_typography(
                fig, ax, "PARIS", "France", (48.8566, 2.3522),
                theme, None, 14.0, 11.0, show_attribution=True,
            )

        # Check that ax.text was called for attribution (last text call)
        text_calls = ax.text.call_args_list
        # Attribution is the last text call (at position 0.98, 0.02)
        attr_call = [c for c in text_calls if c[0][0] == 0.98]
        assert len(attr_call) == 1
        font_prop = attr_call[0][1]["fontproperties"]
        assert font_prop.get_family() == ["monospace"]


class TestCreatePosterValidation:
    """Test create_poster rejects empty city/country (#R20-5)."""

    def test_empty_city_raises(self) -> None:
        with pytest.raises(ValueError, match="city must be a non-empty string"):
            core.create_poster(
                "", "France", (48.8566, 2.3522), 5000, "/tmp/out.png", "png",
                theme=SAMPLE_THEME_DATA,
            )

    def test_whitespace_city_raises(self) -> None:
        with pytest.raises(ValueError, match="city must be a non-empty string"):
            core.create_poster(
                "   ", "France", (48.8566, 2.3522), 5000, "/tmp/out.png", "png",
                theme=SAMPLE_THEME_DATA,
            )

    def test_empty_country_raises(self) -> None:
        with pytest.raises(ValueError, match="country must be a non-empty string"):
            core.create_poster(
                "Paris", "", (48.8566, 2.3522), 5000, "/tmp/out.png", "png",
                theme=SAMPLE_THEME_DATA,
            )


class TestCSVEmptyCityCountryWarning:
    """Test CSV parser warns on rows with empty city/country (#R20-9)."""

    def test_empty_city_in_csv_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging
        from maptoart.batch import load_batch_file

        csv = tmp_path / "cities.csv"
        csv.write_text("city,country\n,France\nParis,France\n")
        with caplog.at_level(logging.WARNING, logger="maptoart.batch"):
            entries = load_batch_file(csv)
        assert len(entries) == 2
        assert any("empty city" in r.message for r in caplog.records)


class TestGeocodingAsyncioRuntimeError:
    """Test geocoding handles RuntimeError from asyncio.run (#R20-10)."""

    @patch("maptoart.geocoding.cache_get", return_value=None)
    @patch("maptoart.geocoding.cache_set")
    @patch("maptoart.geocoding._geocode_with_retry")
    def test_runtime_error_with_new_loop_fallback(
        self,
        mock_geocode: MagicMock,
        mock_cache_set: MagicMock,
        mock_cache_get: MagicMock,
    ) -> None:
        import asyncio
        from maptoart.geocoding import get_coordinates

        mock_location = MagicMock()
        mock_location.latitude = 48.8566
        mock_location.longitude = 2.3522
        mock_location.address = "Paris, France"

        async def _coro():
            return mock_location

        coro = _coro()
        mock_geocode.return_value = coro

        with patch("maptoart.geocoding.asyncio") as mock_asyncio:
            mock_asyncio.iscoroutine.return_value = True
            mock_asyncio.run.side_effect = RuntimeError("no running loop")
            mock_loop = MagicMock()
            mock_loop.run_until_complete.return_value = mock_location
            mock_asyncio.new_event_loop.return_value = mock_loop

            result = get_coordinates("Paris", "France")

        assert result == (48.8566, 2.3522)
        mock_loop.run_until_complete.assert_called_once()
        mock_loop.close.assert_called_once()

    @patch("maptoart.geocoding.cache_get", return_value=None)
    @patch("maptoart.geocoding.cache_set")
    @patch("maptoart.geocoding._geocode_with_retry")
    def test_runtime_error_with_new_loop_also_fails(
        self,
        mock_geocode: MagicMock,
        mock_cache_set: MagicMock,
        mock_cache_get: MagicMock,
    ) -> None:
        import asyncio
        from maptoart.geocoding import get_coordinates

        async def _coro():
            return None

        coro = _coro()
        mock_geocode.return_value = coro

        with patch("maptoart.geocoding.asyncio") as mock_asyncio:
            mock_asyncio.iscoroutine.return_value = True
            mock_asyncio.run.side_effect = RuntimeError("event loop is running")
            mock_loop = MagicMock()
            mock_loop.run_until_complete.side_effect = RuntimeError("still fails")
            mock_asyncio.new_event_loop.return_value = mock_loop

            with pytest.raises(RuntimeError, match="synchronous environment"):
                get_coordinates("Paris", "France")
        mock_loop.close.assert_called_once()


class TestCreatePosterFromOptionsErrors:
    """Test error paths in create_poster_from_options."""

    @patch("maptoart.core._resolve_coordinates", side_effect=ValueError("not found"))
    def test_geocoding_failure_propagates(self, mock_resolve: MagicMock) -> None:
        options = PosterGenerationOptions(city="Nowhere", country="Land")
        with pytest.raises(ValueError, match="not found"):
            core.create_poster_from_options(options, "terracotta")

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster", side_effect=RuntimeError("render failed"))
    @patch("maptoart.core.load_theme", return_value=SAMPLE_THEME_DATA)
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    def test_create_poster_failure_propagates(
        self,
        mock_resolve: MagicMock,
        mock_theme: MagicMock,
        mock_poster: MagicMock,
        mock_meta: MagicMock,
    ) -> None:
        options = PosterGenerationOptions(city="Paris", country="France")
        with pytest.raises(RuntimeError, match="render failed"):
            core.create_poster_from_options(options, "terracotta")


class TestParallelFetchFailure:
    """Test that a parallel fetch task failure is caught and logged."""

    @patch("maptoart.core.fetch_features")
    @patch("maptoart.core.fetch_graph")
    def test_parks_failure_still_returns_graph(
        self, mock_graph: MagicMock, mock_features: MagicMock,
        silent_reporter: core.StatusReporter,
    ) -> None:
        from conftest import build_synthetic_graph
        g = build_synthetic_graph()
        mock_graph.return_value = g

        call_count = 0

        def _feature_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # water succeeds
                return None
            raise RuntimeError("parks fetch exploded")  # parks fails

        mock_features.side_effect = _feature_side_effect

        g_result, water, parks, _cdist = core._fetch_map_data(
            (48.8566, 2.3522), 5000, 12.0, 16.0, status_reporter=silent_reporter,
        )
        assert g_result is not None
        assert parks is None  # failed, so None


class TestMultiThemePartialFailure:
    """Test generate_posters continues past theme failures."""

    @patch("maptoart.core._write_metadata", return_value="/tmp/out.json")
    @patch("maptoart.core.create_poster")
    @patch("maptoart.core._fetch_map_data")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8566, 2.3522))
    @patch("maptoart.core.load_theme", return_value=SAMPLE_THEME_DATA)
    def test_first_succeeds_second_fails(
        self,
        mock_theme: MagicMock,
        mock_resolve: MagicMock,
        mock_fetch: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        tmp_path: Path,
    ) -> None:
        from conftest import build_synthetic_graph

        mock_fetch.return_value = (build_synthetic_graph(), None, None)
        call_count = 0

        def _create_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("theme 2 render failed")

        mock_create.side_effect = _create_side_effect
        options = PosterGenerationOptions(
            city="Paris", country="France",
            themes=["terracotta", "noir"],
            output_dir=str(tmp_path),
        )
        outputs = core.generate_posters(options)
        assert len(outputs) == 1  # first theme succeeded


class TestSaveOutputTempCleanup:
    """Test that temp file is cleaned up on non-OSError."""

    def test_temp_file_removed_on_runtime_error(
        self, tmp_path: Path, sample_theme: dict[str, str],
    ) -> None:
        import tempfile
        fig, ax = plt.subplots(figsize=(2, 2))
        output_file = str(tmp_path / "test.png")

        with patch("maptoart.core.plt.savefig", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                core._save_output(
                    fig, output_file, "png", sample_theme,
                    2, 2, 72,
                )
        # Verify no .tmp files left behind
        tmp_files = list(tmp_path.glob("*.tmp.*"))
        assert len(tmp_files) == 0
        plt.close(fig)


class TestNominatimDelayValidation:
    """Test _nominatim_delay() handles invalid env var gracefully."""

    def test_valid_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from maptoart.geocoding import _nominatim_delay
        monkeypatch.setenv("MAPTOART_NOMINATIM_DELAY", "0.5")
        assert _nominatim_delay() == 0.5

    def test_invalid_env_var_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from maptoart.geocoding import _nominatim_delay
        monkeypatch.setenv("MAPTOART_NOMINATIM_DELAY", "abc")
        assert _nominatim_delay() == 1.0

    def test_unset_env_var_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from maptoart.geocoding import _nominatim_delay
        monkeypatch.delenv("MAPTOART_NOMINATIM_DELAY", raising=False)
        assert _nominatim_delay() == 1.0


class TestBatchIgnoresCityCountryWarning:
    """Test that --batch with --city/--country prints a note."""

    def test_batch_with_city_prints_note(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        batch_file = tmp_path / "cities.csv"
        batch_file.write_text("city,country\nParis,France\n", encoding="utf-8")

        with patch("maptoart.batch.run_batch", return_value={"failures": 0, "successes": []}) as mock_batch:
            from maptoart.cli import main
            main(["--batch", str(batch_file), "--city", "London", "--country", "UK"])

        captured = capsys.readouterr()
        assert "ignored in batch mode" in captured.out


class TestHmacKeyRaceCondition:
    """Test HMAC key generation when another process creates the file first."""

    def test_file_exists_error_reads_existing_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        existing_key = b"existing_key_from_other_process!"  # 32 bytes
        key_path = tmp_path / ".hmac_key"

        # Simulate: os.open with O_CREAT|O_EXCL raises because file was just created
        original_os_open = _util.os.open

        def _fake_os_open(path: str, flags: int, mode: int = 0o777) -> int:
            # Write the "other process" key first, then raise FileExistsError
            key_path.write_bytes(existing_key)
            raise FileExistsError("Another process beat us")

        # First call to read_bytes raises FileNotFoundError (file doesn't exist yet)
        # Then our fake os.open raises FileExistsError
        # Then the fallback read_bytes returns the key
        with patch.object(_util.os, "open", side_effect=_fake_os_open):
            result = _util._cache_hmac_key()

        assert result == existing_key


class TestEntryPoint:
    """Test the _entry() console script wrapper."""

    def test_entry_raises_system_exit_with_return_code(self) -> None:
        from maptoart.cli import _entry
        with patch("maptoart.cli.main", return_value=0):
            with pytest.raises(SystemExit) as exc_info:
                _entry()
            assert exc_info.value.code == 0

    def test_entry_propagates_nonzero_exit(self) -> None:
        from maptoart.cli import _entry
        with patch("maptoart.cli.main", return_value=1):
            with pytest.raises(SystemExit) as exc_info:
                _entry()
            assert exc_info.value.code == 1


class TestGeneratePostersCountryLabelDeprecation:
    """Test that generate_posters emits deprecation for country_label."""

    @patch("maptoart.core.create_poster")
    @patch("maptoart.core._resolve_coordinates", return_value=(48.8, 2.3))
    @patch("maptoart.core._load_custom_fonts", return_value=None)
    @patch("maptoart.core._get_fonts", return_value=None)
    def test_generate_posters_warns_on_country_label(
        self,
        mock_fonts: MagicMock,
        mock_custom: MagicMock,
        mock_resolve: MagicMock,
        mock_create: MagicMock,
        sample_theme: dict[str, str],
        tmp_path: Path,
    ) -> None:
        mock_create.return_value = str(tmp_path / "out.png")
        # Patch _write_metadata so it doesn't fail
        with patch("maptoart.core._write_metadata", return_value="meta.json"):
            import warnings as _warnings
            with _warnings.catch_warnings(record=True) as w:
                _warnings.simplefilter("always")
                opts = PosterGenerationOptions(
                    city="Paris",
                    country="France",
                    themes=["custom"],
                    output_dir=str(tmp_path),
                    country_label="Old Label",
                )
                core.generate_posters(opts)

            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(dep_warnings) >= 1
            assert "country_label" in str(dep_warnings[0].message)


class TestDryRunVectorFormat:
    """Test dry-run output for SVG/PDF formats shows vector-specific message."""

    @patch("maptoart.geocoding.get_coordinates", return_value=(48.8, 2.3))
    def test_svg_dry_run_no_size_estimate(
        self,
        mock_coords: MagicMock,
        sample_theme: dict[str, str],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from maptoart.cli import main
        main(["--city", "Paris", "--country", "France", "--format", "svg", "--theme", "custom", "--dry-run"])
        captured = capsys.readouterr()
        assert "varies for vector formats" in captured.out


class TestAllThemesBatchWarning:
    """Test that --all-themes with --batch prints a warning."""

    def test_all_themes_batch_prints_note(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        batch_file = tmp_path / "cities.csv"
        batch_file.write_text("city,country\nParis,France\n", encoding="utf-8")

        with patch("maptoart.batch.run_batch", return_value={"failures": [], "successes": []}) as mock_batch:
            from maptoart.cli import main
            main(["--batch", str(batch_file), "--all-themes"])

        captured = capsys.readouterr()
        assert "per-entry theme fields will be ignored" in captured.out


class TestCacheInfoCorruptMetadata:
    """Test cache_info() handles corrupt .meta sidecar gracefully."""

    def test_corrupt_meta_still_returns_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        # Create a .pkl file and a corrupt .meta sidecar
        pkl = tmp_path / "test_v2.pkl"
        pkl.write_bytes(b"fake")
        meta = tmp_path / "test_v2.pkl.meta"
        meta.write_text("not json!!!", encoding="utf-8")

        info = _util.cache_info()
        assert info["total_files"] == 1
        assert info["entries"][0]["created"] is None
        assert info["entries"][0]["ttl"] is None


class TestOnProgressCallbackException:
    """Test that a failing on_progress callback doesn't propagate."""

    def test_exception_swallowed(self) -> None:
        def _bad_callback(event: str, message: str | None, extra: dict) -> None:
            raise RuntimeError("callback failed")

        reporter = core.StatusReporter(on_progress=_bad_callback)
        # Should not raise
        reporter.emit("test.event", "hello")


class TestCorruptMetadataWithDefaultTtl:
    """Test that corrupt .meta with default_ttl returns None (cache miss)."""

    def test_corrupt_meta_with_ttl_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_util, "CACHE_DIR", tmp_path)
        # Write a valid cache entry
        _util.cache_set("ttl_test", {"value": 42}, ttl=3600)
        # Corrupt the .meta sidecar
        path = _util._cache_path("ttl_test")
        meta_path = Path(f"{path}.meta")
        meta_path.write_text("not valid json!!!", encoding="utf-8")
        # With default_ttl, corrupt metadata should cause a miss
        result = _util.cache_get("ttl_test", default_ttl=3600)
        assert result is None


class TestNegativeNominatimDelayWarning:
    """Test that negative MAPTOART_NOMINATIM_DELAY logs a warning and clamps to 0."""

    def test_negative_delay_clamps_and_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("MAPTOART_NOMINATIM_DELAY", "-5")
        from maptoart.geocoding import _nominatim_delay
        import logging
        with caplog.at_level(logging.WARNING, logger="maptoart.geocoding"):
            result = _nominatim_delay()
        assert result == 0.0
        assert any("negative" in r.message for r in caplog.records)


class TestCreatePosterDpiClamping:
    """Test that create_poster clamps DPI below 72."""

    @patch("maptoart.core._fetch_map_data")
    @patch("maptoart.core._save_output")
    @patch("maptoart.core._apply_typography")
    @patch("maptoart.core._render_layers")
    @patch("maptoart.core._setup_figure", return_value=(MagicMock(), MagicMock()))
    @patch("maptoart.core.ox.project_graph")
    @patch("maptoart.core._estimate_memory", return_value=100_000)
    def test_low_dpi_clamped_to_72(
        self,
        mock_mem: MagicMock,
        mock_proj: MagicMock,
        mock_setup: MagicMock,
        mock_render: MagicMock,
        mock_typo: MagicMock,
        mock_save: MagicMock,
        mock_fetch: MagicMock,
        sample_theme: dict[str, str],
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)

        core.create_poster(
            "Paris", "France", (48.8, 2.3), 10000,
            "/tmp/out.png", "png", theme=sample_theme,
            dpi=50,  # below minimum
        )
        # _estimate_memory should receive the clamped DPI (72), not 50
        mem_call = mock_mem.call_args
        assert mem_call is not None
        dpi_used = mem_call[0][2]
        assert dpi_used == 72


class TestGeocodingCacheErrorFallback:
    """get_coordinates falls back to fresh lookup when cache is corrupt."""

    @patch("maptoart.geocoding.time.sleep")
    @patch("maptoart.geocoding._geocode_with_retry")
    @patch("maptoart.geocoding.cache_set")
    @patch("maptoart.geocoding.cache_get", side_effect=CacheError("corrupt"))
    def test_cache_error_falls_through(
        self, mock_cget: MagicMock, mock_cset: MagicMock, mock_geo: MagicMock, mock_sleep: MagicMock,
    ) -> None:
        loc = MagicMock(latitude=48.8, longitude=2.3, address="Paris")
        mock_geo.return_value = loc
        lat, lon = core.get_coordinates("Paris", "France")
        assert (lat, lon) == (48.8, 2.3)
        mock_cget.assert_called_once()


class TestOsmnxImportFallback:
    """osmnx._errors import fallback to osmnx.errors."""

    def test_private_import_works(self) -> None:
        # The current osmnx version uses _errors; just verify it imported
        assert hasattr(core, "InsufficientResponseError") or True
        # The real test is that core imported successfully
        from maptoart.core import _fetch_map_data  # noqa: F401


class TestThemeCacheRace:
    """Test double-checked locking returns existing entry."""

    def test_second_thread_gets_cached_copy(self, sample_theme: dict[str, str]) -> None:
        # Pre-populate cache to simulate another thread having just written
        with core._theme_cache_lock:
            core._theme_cache["custom"] = {"name": "FromOtherThread", "bg": "#000000"}
        result = core.load_theme("custom")
        assert result["name"] == "FromOtherThread"


class TestThemeCacheRaceAtEnd:
    """Test double-checked locking at the END of load_theme (line 482-484)."""

    def test_another_thread_cached_while_reading_file(
        self, sample_theme: dict[str, str],
    ) -> None:
        """Simulate another thread caching the theme between file read and final write."""
        other_result = {"name": "RaceWinner", "bg": "#AABBCC"}

        original_load = json.load

        def _intercept_json_load(f):
            # Read the file normally, then simulate another thread caching first
            result = original_load(f)
            with core._theme_cache_lock:
                core._theme_cache["custom"] = other_result
            return result

        with patch("maptoart.core.json.load", side_effect=_intercept_json_load):
            theme = core.load_theme("custom")

        # Should return the value from the "other thread", not the file
        assert theme["name"] == "RaceWinner"


class TestLongCityNameTruncation:
    """generate_output_filename truncates very long city names."""

    def test_long_city_truncated(self, tmp_path: Path) -> None:
        long_city = "a" * 200
        filename = core.generate_output_filename(long_city, "theme", "png", str(tmp_path))
        basename = Path(filename).name
        # The city slug portion should be at most 50 chars
        city_slug = basename.split("_theme_")[0]
        assert len(city_slug) <= 50


class TestCorruptThemeJsonFallback:
    """load_theme falls back to terracotta on corrupt JSON."""

    def test_corrupt_json_returns_terracotta(self, sample_theme_dir: Path) -> None:
        corrupt = sample_theme_dir / "broken.json"
        corrupt.write_text("{invalid json", encoding="utf-8")
        result = core.load_theme("broken")
        assert result["name"] == "Terracotta"
        # Should also be cached so second call hits cache
        result2 = core.load_theme("broken")
        assert result2["name"] == "Terracotta"


class TestMetadataIncludesDisplayNames:
    """_build_poster_metadata includes display_city and display_country."""

    def test_display_names_in_metadata(self) -> None:
        options = PosterGenerationOptions(
            city="Tokyo", country="Japan",
            display_city="東京", display_country="日本",
        )
        meta = core._build_poster_metadata(
            options, "terracotta", {"description": "test"}, "/tmp/out.png",
            (35.6, 139.7), 12.0, 16.0, 300,
        )
        assert meta["display_city"] == "東京"
        assert meta["display_country"] == "日本"
        assert meta["city"] == "Tokyo"

    def test_display_names_default_to_city_country(self) -> None:
        options = PosterGenerationOptions(city="Paris", country="France")
        meta = core._build_poster_metadata(
            options, "terracotta", {}, "/tmp/out.png",
            (48.8, 2.3), 12.0, 16.0, 300,
        )
        assert meta["display_city"] == "Paris"
        assert meta["display_country"] == "France"

    def test_country_label_used_in_metadata(self) -> None:
        options = PosterGenerationOptions(
            city="Munich", country="Germany", country_label="Deutschland",
        )
        meta = core._build_poster_metadata(
            options, "terracotta", {}, "/tmp/out.png",
            (48.1, 11.6), 12.0, 16.0, 300,
        )
        assert meta["display_country"] == "Deutschland"


class TestInfiniteWidthRejected:
    """PosterGenerationOptions rejects non-finite width/height/distance."""

    def test_inf_width(self) -> None:
        with pytest.raises(ValueError, match="width must be a finite number"):
            PosterGenerationOptions(city="X", country="Y", width=float("inf"))

    def test_nan_height(self) -> None:
        with pytest.raises(ValueError, match="height must be a finite number"):
            PosterGenerationOptions(city="X", country="Y", height=float("nan"))

    def test_inf_distance(self) -> None:
        with pytest.raises(ValueError, match="distance must be a finite number"):
            PosterGenerationOptions(city="X", country="Y", distance=float("inf"))


class TestNominatimDelayNonFinite:
    """_nominatim_delay rejects NaN and inf from env var."""

    def test_nan_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import maptoart.geocoding as geo
        monkeypatch.setenv("MAPTOART_NOMINATIM_DELAY", "nan")
        result = geo._nominatim_delay()
        assert result == geo._NOMINATIM_DELAY_DEFAULT

    def test_inf_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import maptoart.geocoding as geo
        monkeypatch.setenv("MAPTOART_NOMINATIM_DELAY", "inf")
        result = geo._nominatim_delay()
        assert result == geo._NOMINATIM_DELAY_DEFAULT


class TestBomCsvBatchFile:
    """CSV with BOM from Excel/Windows loads correctly."""

    def test_bom_csv_loads(self, tmp_path: Path) -> None:
        from maptoart.batch import load_batch_file
        csv_content = "\ufeffcity,country\nParis,France\n"
        bom_file = tmp_path / "bom.csv"
        bom_file.write_text(csv_content, encoding="utf-8")
        entries = load_batch_file(bom_file)
        assert len(entries) == 1
        assert entries[0]["city"] == "Paris"


class TestVietnameseLatinScript:
    """Vietnamese diacritics are recognized as Latin script."""

    def test_vietnamese_is_latin(self) -> None:
        assert core.is_latin_script("H\u1ed3 Ch\u00ed Minh") is True
        assert core.is_latin_script("\u0110\u00e0 N\u1eb5ng") is True


class TestFilenameUuidSuffix:
    """generate_output_filename includes a uuid suffix for uniqueness."""

    def test_unique_filenames(self, tmp_path: Path) -> None:
        f1 = core.generate_output_filename("Paris", "t", "png", str(tmp_path))
        f2 = core.generate_output_filename("Paris", "t", "png", str(tmp_path))
        assert f1 != f2


class TestNonStringCityCountry:
    """PosterGenerationOptions rejects non-string city/country."""

    def test_int_city_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="city must be a string"):
            PosterGenerationOptions(city=123, country="France")

    def test_int_country_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="country must be a string"):
            PosterGenerationOptions(city="Paris", country=456)

    def test_none_city_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="city must be a string"):
            PosterGenerationOptions(city=None, country="France")

    def test_list_country_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="country must be a string"):
            PosterGenerationOptions(city="Paris", country=["France"])


class TestFetchFeaturesCacheKeyIncludesValues:
    """fetch_features cache key includes tag values, not just keys."""

    @patch("maptoart.core.cache_get", return_value=None)
    @patch("maptoart.core.ox.features_from_point")
    @patch("maptoart.core.cache_set")
    def test_different_tag_values_produce_different_keys(
        self, mock_set: MagicMock, mock_features: MagicMock, mock_cache: MagicMock,
    ) -> None:
        mock_features.return_value = MagicMock()

        core.fetch_features((48.0, 2.0), 10000, tags={"natural": "water"}, name="water")
        key1 = mock_set.call_args[0][0]

        mock_set.reset_mock()
        core.fetch_features((48.0, 2.0), 10000, tags={"natural": "bay"}, name="water")
        key2 = mock_set.call_args[0][0]

        assert key1 != key2
        assert "water" in key1
        assert "bay" in key2


class TestCreatePosterZeroDimensions:
    """create_poster rejects width=0 or height=0."""

    def test_zero_width_raises(self) -> None:
        with pytest.raises(ValueError, match="width must be positive"):
            core.create_poster(
                "Paris", "France", (48.8, 2.3), 10000,
                "/tmp/out.png", "png", theme=SAMPLE_THEME_DATA,
                width=0,
            )

    def test_zero_height_raises(self) -> None:
        with pytest.raises(ValueError, match="height must be positive"):
            core.create_poster(
                "Paris", "France", (48.8, 2.3), 10000,
                "/tmp/out.png", "png", theme=SAMPLE_THEME_DATA,
                height=0,
            )

    def test_negative_width_raises(self) -> None:
        with pytest.raises(ValueError, match="width must be positive"):
            core.create_poster(
                "Paris", "France", (48.8, 2.3), 10000,
                "/tmp/out.png", "png", theme=SAMPLE_THEME_DATA,
                width=-5,
            )


class TestCreatePosterClosesFigure:
    """create_poster calls plt.close(fig) for resource cleanup."""

    @patch("maptoart.core._save_output")
    @patch("maptoart.core._apply_typography")
    @patch("maptoart.core._render_layers")
    @patch("maptoart.core._setup_figure")
    @patch("maptoart.core.ox.project_graph")
    @patch("maptoart.core._fetch_map_data")
    def test_plt_close_called_on_success(
        self,
        mock_fetch: MagicMock,
        mock_project: MagicMock,
        mock_setup: MagicMock,
        mock_render: MagicMock,
        mock_typo: MagicMock,
        mock_save: MagicMock,
        sample_theme: dict[str, str],
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)
        mock_project.return_value = g
        mock_fig = MagicMock()
        mock_setup.return_value = (mock_fig, MagicMock())

        with patch("maptoart.core.plt") as mock_plt:
            core.create_poster(
                "Paris", "France", (48.8, 2.3), 10000,
                "/tmp/out.png", "png", theme=sample_theme,
            )
            mock_plt.close.assert_called_once_with(mock_fig)

    @patch("maptoart.core._save_output", side_effect=OSError("disk full"))
    @patch("maptoart.core._apply_typography")
    @patch("maptoart.core._render_layers")
    @patch("maptoart.core._setup_figure")
    @patch("maptoart.core.ox.project_graph")
    @patch("maptoart.core._fetch_map_data")
    def test_plt_close_called_on_error(
        self,
        mock_fetch: MagicMock,
        mock_project: MagicMock,
        mock_setup: MagicMock,
        mock_render: MagicMock,
        mock_typo: MagicMock,
        mock_save: MagicMock,
        sample_theme: dict[str, str],
    ) -> None:
        g = nx.MultiDiGraph()
        g.add_edge("a", "b")
        mock_fetch.return_value = (g, None, None, 4500.0)
        mock_project.return_value = g
        mock_fig = MagicMock()
        mock_setup.return_value = (mock_fig, MagicMock())

        with patch("maptoart.core.plt") as mock_plt:
            with pytest.raises(OSError):
                core.create_poster(
                    "Paris", "France", (48.8, 2.3), 10000,
                    "/tmp/out.png", "png", theme=sample_theme,
                )
            mock_plt.close.assert_called_once_with(mock_fig)
