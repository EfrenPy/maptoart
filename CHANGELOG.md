# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-02-25

### Added (Round 5)
- **Max distance limit** — `PosterGenerationOptions` rejects distances > 100 km (100,000 m)
- **Theme name sanitization** — `_resolve_theme_names()` validates names against `[a-zA-Z0-9_-]+` regex
- **Sparse road network warning** — emits `data.sparse_network` event when graph has < 10 nodes
- **Memory estimation** — `_estimate_memory()` checks before rendering; rejects > 2 GB, warns > 500 MB
- **Cache HMAC integrity** — `cache_set` writes HMAC-SHA256 signature; `cache_get` verifies before loading
- **Font weight retry** — `download_google_font()` retries transient HTTP errors (429/500/502/503) up to 2 times
- **Integration test** — end-to-end test with real matplotlib rendering (`tests/test_integration.py`)
- **mypy config** — `[tool.mypy]` section in `pyproject.toml` with `ignore_missing_imports`, `warn_unused_configs`, `warn_redundant_casts`
- **Deprecation warning** — `create_map_poster.py` emits `DeprecationWarning` on import

### Changed (Round 5)
- **Module split** — `core.py` split into `_util.py` (StatusReporter, _emit_status, CacheError), `geocoding.py` (get_coordinates, coordinate validation), and `rendering.py` (figure setup, render layers, typography, gradient); backward-compatible re-exports maintained
- **Fixed double `_get_fonts()` call** in `_apply_typography` attribution section
- **CLI help text** — `--distance` shows max, `--dpi` shows typical values, `--theme` references `--list-themes`, epilog includes `--dry-run` example

### Added (Round 4)
- **`--dry-run` CLI flag** — prints configuration summary (city, coords, size, themes, estimated output size) without generating posters
- **`--all-themes` resume** — if one theme fails during multi-theme generation, remaining themes continue; failed themes reported via `run.partial` event
- **Font error categorization** — `download_google_font()` distinguishes `ConnectionError`, `Timeout`, and HTTP 404 with actionable messages
- **Config file size limit** — `_load_config_file()` rejects configs larger than 1 MB to prevent accidental resource exhaustion
- **Cache versioning** — cache filenames include `_v2` suffix; old caches are silently ignored (cache miss = re-download)
- **Z-order constants** — `_ZORDER` dict centralizes water/parks/gradient/text layer ordering
- **Gradient array caching** — pre-computed `_GRADIENT_HSTACK` avoids re-allocating NumPy arrays per call
- **Thread-safe theme cache** — `_theme_cache_lock` protects `load_theme()` reads/writes for concurrent use

### Changed (Round 4)
- **Lazy-load `FONTS`** — `load_fonts()` deferred to first access via `_get_fonts()` accessor; importing `maptoposter.core` no longer triggers font I/O
- **Lazy-init `CACHE_DIR`** — directory created on first `cache_get()`/`cache_set()` call, not at import time
- **`plt.close(fig)`** replaces `plt.close("all")` in `create_poster` and `plt.close()` in `_save_output` to avoid closing unrelated figures
- **Output path validation** — `generate_output_filename()` resolves output_dir to an absolute path, preventing `../` traversal
- **Actionable error messages** — geocoding errors now include remediation hints ("Check your internet connection", "Verify the city and country spelling", etc.)
- **tqdm respects json_mode** — progress bar disabled when `status_reporter.json_mode` is `True`
- **`print()` → `_emit_status()`** — banner messages in `generate_posters()` now use the status reporter
- **CLI error handling** — `main()` catches `ValueError` separately for config errors vs generic `Exception` for fatal errors
- **Coverage threshold** raised from 80% to 85% in `pyproject.toml` and `pr-checks.yml`

### Added (Round 3)
- **Pre-commit hooks** — `.pre-commit-config.yaml` with trailing-whitespace, end-of-file-fixer, check-yaml, and flake8 (max-line-length=120)
- **Theme caching** — `load_theme()` caches parsed themes in `_theme_cache` for repeated lookups; fallback path is never cached
- **Theme color validation** — `load_theme()` validates all 11 color keys match `#RRGGBB` hex format; raises `ValueError` on mismatch
- **Coordinate bounds validation** — `_validate_coordinate_bounds()` helper rejects lat outside [-90, 90] or lon outside [-180, 180]
- **Early font warning** — `generate_posters()` logs a warning when no custom or bundled fonts are available
- **Test coverage ≥ 80%** — 15+ new tests: coordinate validation, crop limits, typography, render layers, cache corruption, filename sanitization, theme color validation

### Changed (Round 3)
- **CI line length** standardized to 120 (was 160) to match `.flake8`
- **Coverage threshold** raised from 70% to 80% in `pyproject.toml` and `pr-checks.yml`
- **`print(e)` → `_logger.warning()`** for cache errors in `get_coordinates`, `fetch_graph`, `fetch_features`
- **`print()` → `_emit_status()`** for resolution/DPI info in `_save_output`
- **`except Exception` → `except (ValueError, RuntimeError)`** in `_render_layers` projection fallback
- **`get_crop_limits`** — added full type hints
- **`create_poster`** — steps 2-5 wrapped in `try/finally` with `plt.close("all")` safety net
- **`pytest` config** — added `testpaths = ["tests"]` to `pyproject.toml`
- **Re-added `logging`** module to `core.py` (removed in Round 2 as unused; now used for cache/font warnings)

### Added
- **uv package manager support** ([PR #20](https://github.com/originalankur/maptoposter/pull/20))
  - Added `pyproject.toml` with project metadata and dependencies
  - Added `uv.lock` for reproducible builds
  - Added shebang to `create_map_poster.py` for direct execution
  - Updated README with uv installation instructions
- **Python version specification** - `requires-python = ">=3.11"` in pyproject.toml (fixes [#79](https://github.com/originalankur/maptoposter/issues/79))
- **Coordinate override** - `--latitude` and `--longitude` arguments to override the geocoded center point (existing from upstream PR #106, clarifies [#100](https://github.com/originalankur/maptoposter/issues/100))
  - Still requires `--city` and `--country` for display name
  - Useful for precise location control
- **Input validation** - `PosterGenerationOptions.__post_init__` validates distance, width, height, dpi, and output_format
- **Geocoding retry** - Nominatim calls retry up to 2 times with exponential backoff on transient errors (`GeocoderTimedOut`, `GeocoderUnavailable`)
- **PyPI classifiers** and `[project.urls]` in `pyproject.toml`
- **CI test automation** - `pytest --cov` step added to `pr-checks.yml` workflow
- **Type hints** added to `create_gradient_fade`, `get_edge_colors_by_type`, `get_edge_widths_by_type`, `fetch_graph`, `fetch_features`, and `font_management` functions

### Fixed
- **Z-order bug** - Roads now render above parks and water features (fixes [#39](https://github.com/originalankur/maptoposter/issues/39), relates to [PR #42](https://github.com/originalankur/maptoposter/pull/42))
  - Water layer: `zorder=1` → `zorder=0.5`
  - Parks layer: `zorder=2` → `zorder=0.8`
  - Roads remain at `zorder=2` (matplotlib default), ensuring proper layering
- **Text scaling for landscape orientations** - Font size now scales based on `min(height, width)` instead of just width (fixes [#112](https://github.com/originalankur/maptoposter/issues/112))
- **Filename sanitization** - City names with special characters are now safely sanitized in output filenames
- **Atomic writes** now catch `Exception` instead of `BaseException` to avoid swallowing `KeyboardInterrupt`/`SystemExit`
- **Duplicate `compensated_dist` calculation** removed from `create_poster` (now returned from `_fetch_map_data`)

### Changed
- Updated `.gitignore` with poster outputs, Python build artifacts, IDE files, and OS-specific files
- **Deprecated `datetime.utcnow()`** replaced with `datetime.now(UTC)` (Python 3.12+ recommended pattern)
- **Specific exception handling** in `get_coordinates` (catches `GeocoderServiceError`), `fetch_graph` and `fetch_features` (catch `InsufficientResponseError`, `ResponseStatusCodeError`, `ValueError`, `ConnectionError`)
- **`font_management.py`** — `print()` calls replaced with `logging` module; `Optional`/`list` type hints modernized to PEP 604/585

### Removed
- Unused `_logger` and `import logging` from `core.py` (logging was imported but never used)

---

## [0.3.0] - 2026-01-27 (Maintainer: @originalankur)

### Added
- **Custom coordinates support** - `--latitude` and `--longitude` arguments ([#106](https://github.com/originalankur/maptoposter/pull/106))
- **Emerald theme** - Lush dark green aesthetic with mint accents ([#114](https://github.com/originalankur/maptoposter/pull/114))
- **GitHub Actions** - PR checks workflow ([#98](https://github.com/originalankur/maptoposter/pull/98))
- **Conflict labeling** - Auto-label PRs with merge conflicts

### Changed
- **Default theme** changed from `feature_based` to `terracotta` ([#131](https://github.com/originalankur/maptoposter/pull/131))
- **Default distance** changed from 12000m to 18000m ([#128](https://github.com/originalankur/maptoposter/pull/128))
- **Max dimensions** enforced at 20 inches for width/height (supports up to 4K resolution) ([#128](https://github.com/originalankur/maptoposter/pull/128), [#129](https://github.com/originalankur/maptoposter/pull/129))

### Removed
- `feature_based` theme ([#131](https://github.com/originalankur/maptoposter/pull/131))

### Fixed
- Cache directory handling ([#109](https://github.com/originalankur/maptoposter/pull/109))
- Dynamic font scaling based on poster width

---

## [0.2.1] - 2026-01-18 (Maintainer: @originalankur)

### Added
- **SVG/PDF export** - `--format` flag for vector output ([#57](https://github.com/originalankur/maptoposter/pull/57))
- **Variable poster dimensions** - `-W` and `-H` arguments ([#59](https://github.com/originalankur/maptoposter/pull/59))
- **Caching** - Downloaded OSM data is now cached locally
- **Rate limiting** - 0.3s delay between API requests

### Fixed
- Map warping issues with variable dimensions ([#59](https://github.com/originalankur/maptoposter/pull/59))
- Edge nodes retention for complete road networks ([#27](https://github.com/originalankur/maptoposter/pull/27))
- Point geometry filtering to prevent dots on maps
- Dynamic font size adjustment for long city names
- Nominatim timeout increased to 10 seconds

### Changed
- Graph projection to linear coordinates for proper aspect ratio
- Improved cache handling with hashed filenames and error handling

---

## [0.2.0] - 2026-01-17 (Tag: v0.2)

### Added
- Example poster images in README
- Initial theme collection

---

## [0.1.0] - 2026-01-17 (Initial Release)

### Added
- Initial maptoposter source code
- README with usage instructions
- 17 built-in themes:
  - autumn, blueprint, contrast_zones, copper_patina
  - forest, gradient_roads, japanese_ink, midnight_blue
  - monochrome_blue, neon_cyberpunk, noir, ocean
  - pastel_dream, sunset, terracotta, warm_beige
- Core features:
  - City/country based map generation
  - Customizable themes via JSON
  - Road hierarchy coloring
  - Water and park feature rendering
  - Typography with Roboto font
  - Coordinate display
  - OSM attribution
