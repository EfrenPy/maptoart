# AGENTS GUIDE FOR MAPTOART
This file captures the operational knowledge agentic contributors need: how to set up the project, which commands CI expects, how to run targeted checks, and the coding style guardrails that keep the CLI reliable. Keep it in sync with the repo.

## Repository Snapshot
- Primary entrypoint: `src/maptoart/core.py` (data fetching, poster generation, theme resolution).
- CLI entry: `src/maptoart/cli.py` exposes the `maptoart-cli` console script (supports `--config` files, structured logging, metadata output, parallel rendering).
- Modules (split from core in v0.4.0):
  - `rendering.py` — figure setup, render layers, typography, gradient fade
  - `geocoding.py` — Nominatim geocoding with tenacity retries, coordinate validation
  - `_util.py` — StatusReporter, cache HMAC integrity, CacheError, RestrictedUnpickler
  - `batch.py` — CSV/JSON batch processing, parallel city processing, pre-geocoding
  - `gallery.py` — HTML gallery generator with CSS grid and metadata cards
  - `font_management.py` — Google Fonts download + caching logic
- Assets: `src/maptoart/themes/*.json`, `src/maptoart/fonts/` (Roboto defaults + cached web fonts), `posters/` (generated output), `cache/` (OSM + geocode cache, ignored).
- Tooling manifests: `pyproject.toml`, `requirements.txt`, `.flake8`, `.github/workflows/*.yml`, `test/all_variations.sh`.
- Python target: 3.11+ (CI runs 3.11-3.14). Most dependencies need native libs (GEOS/Proj via `pyproj`/`shapely`).
- Test suite: 414 tests, 100% coverage enforced.

## Environment Setup
- Preferred workflow is [uv](https://docs.astral.sh/uv/): `uv sync --locked` to install with `uv.lock`, or `uv run maptoart-cli --city "Paris" --country "France"` to auto-create the venv on demand.
- Traditional venv: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
- When debugging CI parity, mirror its steps: upgrade pip, `pip install -r requirements.txt` (no extras), then `pip install flake8 pylint mypy pip-audit`.
- Fonts download into `fonts/cache/`. The directory is gitignored; do not commit downloaded `.woff2`/`.ttf` files.
- Nominatim and Google Fonts depend on outbound HTTPS. Ensure proxies/firewalls allow requests.
- `MAPTOART_OUTPUT_DIR` overrides where posters/metadata are written. Users can also pass `--output-dir` on the CLI.

## Build & Packaging Commands
- `uv run python -m compileall . -q` — matches the CI "Validate Python syntax" stage.
- `uv run python -m pip install --upgrade pip` whenever system pip is old; several libs require modern wheels.
- Packaging is managed via `pyproject.toml` + setuptools (no wheel build script yet). To build: `uv run python -m build` (optional, not part of CI but handy for releases).
- Regenerate dependency lock when required: `uv lock` (note: `uv.lock` is ignored; share pinned deps via PR discussion if necessary).
- Sync `requirements.txt` from the project metadata via `./scripts/sync_requirements.sh` (wraps `uv pip compile`).
- Whenever `pyproject.toml` dependencies change, run the sync script before pushing so `requirements.txt` stays in lockstep; include the refreshed file in commits.

## Linting & Static Analysis
- `uv run flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics` then `uv run flake8 . --count --statistics --max-line-length=120` — replicate CI job exactly. The `MAX_LINE_LENGTH` env var in `pr-checks.yml` is set to 120.
- `uv run pylint . --max-line-length=120` — CI marks pylint warnings as non-blocking but still reports them; keep score high to avoid regressions.
- `uv run mypy . --ignore-missing-imports --no-strict-optional` — ensures our gradual typing remains consistent despite heavy geospatial libs lacking stubs.
- `uv run pip-audit -r requirements.txt` — mirrors `dependency-check` job; run before bumping dependencies.
- Keep `tqdm`, `osmnx`, and `geopandas` imports grouped under third-party block; avoid unused imports or wildcard pulls to stay lint-clean.

- `uv run maptoart-cli --help` — sanity-checks argparse wiring.
- `uv run maptoart-cli --list-themes` — ensures JSON themes parse cleanly and required files exist.
- `bash test/all_variations.sh` — exhaustive integration exercise that generates Bengaluru posters in every theme/size. It is network and disk heavy; prefer targeted runs while iterating.
- **Single scenario regression:** `uv run maptoart-cli -c "Paris" -C "France" -t terracotta -d 12000 --dpi 150`. Swap arguments to stress specific code paths (custom fonts, non-Latin labels, etc.).
- Unit tests live under `tests/`. Install dev extras once via `uv pip install '.[dev]'`, then run `uv run pytest` (fixtures mock geocoding/OSM data, so tests avoid network calls). CI enforces 100% coverage via `--cov-fail-under=100`.
- Run a single test with `uv run pytest tests/test_core.py::TestClassName::test_method`.

## Data & Assets Expectations
- Theme JSON contract: keys `bg`, `text`, `gradient_color`, `water`, `parks`, `road_*`, optionally new layer entries. Always provide hex strings and keep descriptions short; CLI uses them in logs.
- Each generated poster also writes a metadata JSON sibling capturing coordinates, DPI, theme, and attribution flags. Keep schema additions backward-compatible and reflect changes in README/tests.
- Fonts: default Roboto weights live in `fonts/`. Custom fonts come from Google Fonts via HTTPS; `font_management.download_google_font` caches by `font_family` and weight. Ensure new code respects `font_family` CLI flag and gracefully falls back to Roboto when downloads fail.
- Cache: `MAPTOART_CACHE_DIR` env var overrides default `cache/` (legacy `CACHE_DIR` still works as fallback). Cached coordinates and graph data are pickled; do not change schema lightly. Always wrap cache operations in `try/except CacheError` if you extend caching.

## Architecture Overview
- CLI parses args, optionally lists themes, resolves coordinates (geocode or manual lat/long), loads theme + fonts, and calls `create_poster` for each requested theme.
- `create_poster` builds an OSMnx graph, constrains view window to figure aspect ratio, renders water/parks/roads/text layers with Matplotlib, and writes PNG/SVG/PDF outputs.
- `font_management` isolates HTTP calls, caches, and verifies fonts exist before handing paths to Matplotlib.
- Global constants (e.g., `PAPER_SIZES`, `CACHE_DIR`, `THEME`) live near top-level; treat them as configuration knobs for future refactors.

### Parallelization Architecture
- **Data hoisting:** `generate_posters()` calls `_fetch_map_data()` and `ox.project_graph()` once before the theme loop, passing results to `create_poster()` via `_prefetched_data` and `_projected_graph` params. This avoids redundant network calls when generating multiple themes for the same city.
- **Parallel theme rendering:** When `parallel_themes=True`, `generate_posters()` uses `ProcessPoolExecutor` to render themes concurrently. matplotlib is NOT thread-safe, so multiprocessing (not threading) is required. The worker function `_render_theme_worker()` must be top-level (not a closure or method) for pickle serialization.
- **Pre-geocoding:** `_pre_geocode_batch()` resolves coordinates for all batch entries before the main loop. Nominatim enforces 1 req/sec, so geocoding remains sequential, but the results make city processing independent.
- **Parallel batch:** When `parallel=True`, `run_batch()` uses `ProcessPoolExecutor` to process cities concurrently via `_process_city_worker()`. Each worker calls `generate_posters()` independently.
- **Key constraint:** All worker functions must be pickle-serializable (top-level, no lambdas, no closures over unpicklable objects). This affects testing—mocking `ProcessPoolExecutor` itself is required since `MagicMock` objects can't be pickled.

## Import & Module Guidelines
- Order imports: stdlib → third-party → local. Use blank lines to separate groups, mirroring `maptoart/core.py`.
- Avoid `from module import *`. Prefer explicit names and, where relevant, alias heavy modules (`matplotlib.pyplot as plt`).
- Type-aware imports belong near the rest of the group; do not hide them inside functions unless they are optional and expensive.
- Keep CLI-only dependencies (e.g., `argparse`, `sys`) at module scope; dynamic imports complicate linting/type checking.

## Formatting Expectations
- 4 spaces, no tabs. Stick to 120 characters (CI enforces this via `MAX_LINE_LENGTH=120`).
- Use descriptive triple-double-quoted docstrings for modules, classes, and non-trivial functions. Include purpose, important args, and side effects when relevant.
- Prefer f-strings for string formatting. Avoid `%`-style formatting except when required by logging APIs.
- Multi-line literals (lists/dicts) should align keys/values similarly to existing theme dicts. Trailing commas acceptable for readability.
- Keep CLI help strings concise; longer explanations belong in README.md or here.

## Typing & Data Handling
- Use standard typing primitives (`dict[str, str]`, `list[float]`, `Optional[dict]`). Keep type hints close to where values originate to help mypy.
- When interacting with third-party libs lacking stubs, use `typing.cast` to appease mypy instead of `# type: ignore` whenever possible.
- Return tuples for coordinate pairs `(lat, lon)` consistently. Document units (meters/inches) explicitly in function docstrings.
- Treat JSON-derived data as `dict[str, Any]` until validated. Convert to typed structures before heavy use to prevent runtime surprises.

## Naming Conventions
- snake_case for functions/variables, CamelCase for classes (`CacheError`), UPPER_SNAKE for module-level constants (`PAPER_SIZES`).
- CLI arguments mirror long-form option names (e.g., `--country-label` → `country_label`). Keep new flags consistent with this mapping.
- Filenames: `my_theme.json`, `city_theme_timestamp.ext`. Avoid spaces and uppercase to keep poster filenames predictable.

## Error Handling & Logging
- Use precise exceptions (`ValueError` for invalid user data, `CacheError` for cache failures, `RuntimeError` for event-loop collisions). Propagate rather than silently swallowing errors.
- Wrap IO/network calls in `try/except` and surface actionable messages (prefix with `⚠`, `✗`, `✓` like current logging to help CLI UX).
- Respect Nominatim usage policy: keep `time.sleep(1)` before each geocode, do not parallelize geocode requests.
- When catching broad `Exception`, immediately log stack traces (see final block in `__main__`) to aid debugging.

## CLI & UX Practices
- `if __name__ == "__main__"` guard must stay at bottom to support module imports/tests.
- Provide graceful fallbacks: if `--list-themes` is used, exit after printing. When inputs are missing, print examples and exit with status 1.
- Keep `print_examples()` synchronized with README usage tables so help output matches docs.
- Respect `--no-attribution`, `--paper-size`, and `--orientation` semantics exactly; these feed print shops, so regressions are high impact.
- Config files (`--config <json|yaml>`) mirror CLI flags (snake_case). CLI arguments must continue to override config values deterministically; document new keys in README + tests.
- Structured logs (`--log-format json`) emit newline-delimited events (currently `run.start`, `poster.start`, `poster.metadata`, etc.). Maintain event names when extending telemetry so downstream automation stays stable.

## Performance & Memory
- Large `dist` (>20km) values balloon memory/time. Document any code that increases graph size and provide CLI warnings like existing ones.
- Use `tqdm` or lightweight logging for long loops (e.g., generating posters for all themes). Avoid nested progress bars; they render poorly in CI logs.
- `create_gradient_fade` currently builds `np.linspace` arrays per call. Reuse or cache results if you add more gradients to avoid redundant allocations.
- Limit DPI above 2400. Existing guard rails warn users; keep them if you tweak defaults.
- `--parallel-themes` and `--parallel` use `ProcessPoolExecutor`; each worker spawns a full Python process with its own memory. Monitor total RAM when combining large `dist`, high DPI, and many workers.
- Data fetching is hoisted in `generate_posters()`—adding new per-theme data should go through the prefetch mechanism to avoid re-downloading.

## Git & Workflow Notes
- Do not commit generated posters, fonts, caches, or virtualenvs. `.gitignore` already covers them; expand if new tooling adds artifacts.
- CI runs on Ubuntu + Windows; avoid hardcoding POSIX-only paths. Use `Path`/`os.path` so scripts stay cross-platform.
- Pull Request checks rely on `pr-checks.yml`; keep any new scripts cross-platform and reference them from CI before merging.
- Conflict labeling workflow adds/clears a `Conflicts` label automatically; resolve merges promptly to avoid noisy automation.

## Cursor / Copilot Rules
- There are currently no `.cursor/rules*` or `.github/copilot-instructions.md` files. If such tooling guidance is added later, reference it here immediately so agents inherit the same guardrails.

- Ensure `uv run flake8`, `uv run pylint`, and `uv run mypy` pass locally.
- Run `uv run maptoart-cli --help` plus at least one targeted poster generation command relevant to the change (structured logs help when inspecting automation output).
- Install dev extras and run `uv run pytest` to keep the regression suite green (especially after touching typography/geocoding/theme logic).
- If dependency versions were edited, rerun `./scripts/sync_requirements.sh` and include the regenerated `requirements.txt` in the commit; keep `uv.lock` up to date as well.
- For dependency bumps, capture `uv run pip-audit -r requirements.txt` output in the PR description if issues are resolved.
- Update README.md, themes, or this AGENTS guide when changing CLI options, theme schema, or tooling expectations.

## Theme Development Tips
- Keep palette values high-contrast enough for roads vs. water vs. background; test in both PNG and PDF outputs.
- Document each theme in its JSON `description` so `--list-themes` produces meaningful summaries.
- When adding new color keys (e.g., `railway`, `buildings`), update defaults in `load_theme` and adjust render order explicitly.
- Store new theme files in `themes/` with lowercase snake_case names to align with CLI expectations.
- Validate JSON via `python -m json.tool themes/<file>.json` before committing to avoid runtime decode errors.

## Font & i18n Notes
- `font_management.load_fonts` expects `Roboto-*.ttf` in `fonts/`; keep those files present even when testing custom families.
- Google Fonts downloads reuse `fonts/cache/`; avoid deleting this directory during CI because it saves repeated HTTP hits locally.
- Always test non-Latin scripts (Japanese, Arabic, etc.) when touching layout calculations—letter spacing logic only applies to Latin alphabets.
- When new CLI flags adjust typography, ensure they gracefully fall back to defaults and include docstrings.

## Cache & External Services
- Respect `MAPTOART_CACHE_DIR` overrides to support ephemeral filesystems; use `Path`/`os.makedirs(..., exist_ok=True)` like `_cache_path` does.
- Cached pickles must remain backward-compatible; update keys or version them explicitly before changing structure.
- Network calls (Nominatim, Google Fonts) should set timeouts and catch `requests`/`geopy` errors; bubble up actionable messages.
- Geocoding is pre-resolved in batch via `_pre_geocode_batch()` (still sequential, respects rate limits). Never parallelize geocode requests without revisiting Nominatim rate-limit guards.

## Manual QA Checklist
- Verify generated poster filenames follow `{city}_{theme}_{YYYYMMDD_HHMMSS}.ext` and land in `posters/`.
- Check `--paper-size` presets match README tables (A0–A4) with correct orientation swaps.
- Exercise `--no-attribution`, `--format svg`, and `--format pdf` to ensure watermark logic works across formats.
- Run at least one scenario with custom lat/long overrides to confirm `lat_lon_parser` parsing stays accurate.
- Smoke test `--all-themes` to confirm loops handle fonts and caching correctly.

## Common Pitfalls
- Forgetting to guard CLI-only behavior behind `if __name__ == "__main__"` makes importing the module in tests impossible.
- Hardcoding relative paths without `Path` will break on Windows CI; prefer `Path(__file__).parent` or `Path.cwd()`.
- Adding new dependencies requires updating both `pyproject.toml` and `requirements.txt`; keep versions pinned to avoid CI drift.
- Leaving matplotlib figures open leaks memory during multi-theme renders—always close figures after saving.

## Observability & Logging Patterns
- Continue using the ✓/⚠/✗ prefixes for user-facing prints; they make CLI logs scannable in CI and local terminals.
- When adding verbose logs, gate them behind a CLI flag or environment variable to avoid overwhelming the default output.
- Prefer `tqdm` for loops over manual counters so long-running operations give users progress visibility.
- Capture stack traces via `traceback.print_exc()` in `except Exception` blocks so diagnostics survive headless CI runs.

## Future Automation Notes
- Consider adding `ruff` or `black` only after aligning max-line-length with `.flake8`; update this guide simultaneously.
- Any new CLI subcommands should expose `--dry-run` or similar toggles so CI can exercise them without hitting APIs.
- Keep telemetry optional; default to off unless governed by explicit flag or env var.

## Helpful References
- README.md doubles as product documentation; update screenshots/themes when visuals change.
- CHANGELOG.md summarizes release highlights—append entries when bumping versions in `pyproject.toml`.
- `.github/workflows/pr-checks.yml` lists authoritative CI commands; re-read it before editing automation steps.
- `test/all_variations.sh` is the canonical example generator; mirror its flags when documenting new features.
