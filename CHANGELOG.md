# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-02-26

20 rounds of quality improvements across security, architecture, testing, and packaging.

### Added

**New modules:**
- **`batch.py`** — CSV/JSON batch processing; `--batch <file>` generates multiple posters in one invocation with `load_batch_file()` and `run_batch()`; transient-error retry with configurable backoff
- **`gallery.py`** — `--gallery` flag generates a self-contained `gallery.html` with CSS grid layout, metadata cards, and XSS-safe HTML escaping
- **`rendering.py`** — extracted from `core.py`; figure setup, render layers, typography, gradient fade; named constants replace magic numbers
- **`geocoding.py`** — extracted from `core.py`; Nominatim geocoding with tenacity retries, coordinate validation, async coroutine handling
- **`_util.py`** — extracted from `core.py`; `StatusReporter`, `_emit_status`, `CacheError`, cache HMAC integrity, `_RestrictedUnpickler`

**New CLI flags:**
- **`--dry-run`** — prints configuration summary (city, coords, size, themes, estimated output size) without generating posters; shows KB/MB appropriately
- **`--batch <file>`** — CSV or JSON batch poster generation with retry logic for transient errors
- **`--gallery`** — generates HTML gallery page after rendering
- **`--cache-clear`** — deletes all cached OSM data and exits
- **`--cache-info`** — prints cache statistics (file count, total size) and exits
- **`--all-themes`** — generates posters for every available theme; continues on failure and reports partial results
- **`--debug`** — enables DEBUG-level logging output

**New features:**
- **tenacity retries** — `font_management.py` and `geocoding.py` use `@retry` decorators (tenacity >= 8.2.0) with configurable stop/wait/retry-if strategies
- **Fuzzy theme matching** — `_resolve_theme_names()` suggests corrections for misspelled themes (e.g., "teracota" -> "terracotta")
- **Parallel OSM data fetching** — `ThreadPoolExecutor(max_workers=3)` fetches graph, water, and parks concurrently with partial failure handling
- **Auto DPI reduction** — when requested DPI would exceed 2 GB memory limit, DPI is automatically reduced to the highest safe value (minimum 72)
- **Cache TTL** — `.meta` JSON sidecar files track creation time and TTL; 30 days for coordinates, 7 days for map data
- **Cache HMAC integrity** — `cache_set` writes HMAC-SHA256 signature; `cache_get` verifies before loading with single-read optimization
- **Restricted pickle deserialization** — `_RestrictedUnpickler` allowlists safe modules only; blocks arbitrary code execution from cache files
- **`create_poster_from_options()`** — convenience API accepting keyword arguments directly
- **Progress streaming** — `StatusReporter` accepts an `on_progress` callback; thread-safe for use across `ThreadPoolExecutor` workers
- **Memory estimation** — `_estimate_memory()` rejects > 2 GB, warns > 500 MB before rendering
- **Max distance limit** — `PosterGenerationOptions` rejects distances > 100 km
- **Theme name sanitization** — validates names against `[a-zA-Z0-9_-]+` regex
- **Theme color validation** — `load_theme()` validates all 11 color keys match `#RRGGBB` hex format
- **Coordinate bounds validation** — rejects lat outside [-90, 90] or lon outside [-180, 180]
- **City/country validation** — `create_poster()` rejects empty city or country strings
- **Config file size limit** — rejects configs larger than 1 MB
- **CSV row validation** — early warning at parse time for rows with empty city/country
- **Sparse road network warning** — emits `data.sparse_network` event when graph has < 10 nodes
- **Font weight fallback** — when "regular" weight is missing, first available weight is used as substitute
- **Metadata sidecar files** — every poster generates a companion `.json` with coordinates, DPI, theme, timestamps

**Architecture & code quality:**
- **Module split** — `core.py` split into `_util.py`, `geocoding.py`, `rendering.py`; backward-compatible re-exports maintained
- **Deduplicated fetch functions** — `_cached_fetch` helper removes ~40 lines of duplication from `fetch_graph`/`fetch_features`
- **`functools.lru_cache`** replaces `_FONTS`/`_Sentinel`/`_UNLOADED` global state pattern
- **`os.path` -> `pathlib`** standardization across `_util.py` and `font_management.py`
- **Named rendering constants** — `_BASE_FONT_CITY`, `_POS_CITY_Y`, `_GRADIENT_BOTTOM_END`, etc. replace magic numbers
- **Z-order constants** — `_ZORDER` dict centralizes water/parks/gradient/text layer ordering
- **Gradient array caching** — pre-computed `_GRADIENT_HSTACK` avoids re-allocating NumPy arrays per call
- **Thread-safe theme cache** — `_theme_cache_lock` protects concurrent `load_theme()` access
- **Lazy-load fonts and cache directory** — no I/O at import time
- **Status event naming convention** — documented dot-separated hierarchy (`module.action.detail`)
- **Environment variable documentation** — module-level docstrings list all `MAPTOPOSTER_*` env vars
- **`ClassVar` type annotation** on `_RestrictedUnpickler._ALLOWED_MODULES`
- **Consolidated exception handlers** — `ConnectionError`/`Timeout` merged in `font_management.py`
- **Narrowed `except Exception`** — replaced with specific types (`RuntimeError`, `ValueError`, `OSError`) throughout

**Packaging & CI:**
- **Dockerfile** — multi-stage `python:3.12-slim` build with `libgeos-dev` and `libproj-dev`; healthcheck and font verification
- **Docker CI** — `.github/workflows/docker.yml` builds and pushes to `ghcr.io` on GitHub releases
- **PyPI publishing** — `.github/workflows/publish.yml` via trusted publisher (`pypa/gh-action-pypi-publish`)
- **Compatible-release deps** — `~=` operators replace exact `==` pins in `pyproject.toml`
- **`tenacity~=8.2.0`** added as new dependency
- **Coverage threshold** raised to 100% in `pyproject.toml` and `pr-checks.yml`
- **`generate_gallery`** exported in `__init__.py` `__all__`
- **`py.typed`** marker included in package data

**Testing (321 tests, 100% coverage):**
- SVG/PDF integration tests with real matplotlib rendering
- Non-Latin typography tests (CJK, Arabic, Thai, Cyrillic, mixed-script)
- Batch processing tests (CSV, JSON, edge cases, unsupported formats)
- Gallery tests (PNG cards, PDF placeholders, XSS escaping, missing metadata)
- Cache tests (HMAC integrity, TTL expiry, nonexistent dir, restricted unpickling)
- Geocoding tests (coroutine handling, asyncio RuntimeError, missing address, cache failures)
- Font management tests (weight fallback, Google Font success log, network error consolidation)
- CLI tests (dry-run KB formatting, --gallery flag, ValueError handling, parse_coordinates(None))
- Rendering tests (attribution font fallback, empty highway list, LineString-only layers)
- Core tests (create_poster validation, partial fetch failure, atomic write cleanup)
- Performance tests (`tests/test_performance.py`)

### Changed
- **Author** updated to Efren Rodriguez Rodriguez
- **GitHub URLs** updated to `EfrenPy/maptoposter`
- **`plt.close(fig)`** replaces `plt.close("all")` to avoid closing unrelated figures
- **Output path validation** — `generate_output_filename()` resolves to absolute path, preventing `../` traversal
- **Actionable error messages** — geocoding errors include remediation hints
- **tqdm respects json_mode** — progress bar disabled in JSON mode
- **`print()` -> `_emit_status()`** — all status messages routed through StatusReporter
- **`logging.basicConfig`** wired up in CLI `main()` with `--debug` support
- **`datetime.utcnow()`** replaced with `datetime.now(UTC)`
- **Specific exception handling** throughout (no more bare `except Exception` without justification)

### Fixed
- **XSS vulnerability** in `gallery.py` — all metadata values now HTML-escaped
- **Circular import** — `geocoding.py` no longer imports itself through `core`
- **Z-order bug** — roads render above parks and water features
- **Text scaling** — font size scales based on `min(height, width)` for landscape orientations
- **Filename sanitization** — city names with special characters safely handled
- **Double `_get_fonts()` call** in `_apply_typography` attribution section
- **Atomic writes** catch `Exception` instead of `BaseException` to preserve `KeyboardInterrupt`/`SystemExit`

### Removed
- `feature_based` theme (replaced by `terracotta` as default in v0.3.0)
- Unused `_logger` and `import logging` from early `core.py` (re-added properly in later rounds)

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
