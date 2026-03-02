# Using maptoart in Other Projects

This guide outlines the recommended ways to install and consume the `maptoart` package from neighboring services (e.g., marketing sites, backend workers, or design tools).

## 1. Install via pip from Git (recommended)

Reference a tagged commit directly from Git when you need a reproducible build:

```bash
pip install "git+https://github.com/EfrenPy/maptoart.git@v0.5.0"
```

If your environment uses SSH:

```bash
pip install "git+ssh://git@github.com/EfrenPy/maptoart.git@main"
```

Pinning to tags (or SHAs) guarantees rebuilds are deterministic across consumers.

## 2. Install locally in editable mode

When working on `maptoart` alongside another project (e.g., `maptoartpage`):

```bash
cd /path/to/maptoartpage
pip install -e ../maptoart
```

Editable installs keep both repos in sync—updating the library immediately affects the consumer app.

## 3. Build a wheel for distribution

If you prefer uploading artifacts to an internal index or attaching them to releases:

```bash
cd /path/to/maptoart
uv build  # or: python -m build
pip install dist/maptoart-<version>-py3-none-any.whl
```

Publish the wheel to your preferred package index (private PyPI, GitHub Packages, etc.) so downstream services can `pip install maptoart==<version>` without referencing Git.

## 4. Programmatic usage

```python
from maptoart import PosterGenerationOptions, generate_posters

options = PosterGenerationOptions(
    city="Paris",
    country="France",
    themes=["terracotta", "neon_cyberpunk"],
    output_dir="/tmp/posters",
)
generate_posters(options)
```

The snippet above shows the minimal API surface. Pass additional fields on `PosterGenerationOptions` (e.g. `dpi`, `distance`, `output_format`, `parallel_themes`, `max_theme_workers`) to customise output. See the `PosterGenerationOptions` dataclass docstring for all available fields.

**Parallel rendering** — speed up multi-theme and batch workflows:

```python
from maptoart import PosterGenerationOptions, generate_posters, run_batch

# Parallel theme rendering (multiprocessing)
options = PosterGenerationOptions(
    city="Paris", country="France",
    all_themes=True, parallel_themes=True, max_theme_workers=8,
)
generate_posters(options)

# Parallel batch processing
result = run_batch("cities.csv", parallel=True, max_workers=8)
```

## 5. Packaging data

Themes and default Roboto fonts ship with the wheel. If your app needs custom palettes or fonts:

1. Drop new theme JSON files into a directory and set `MAPTOART_THEMES_DIR=/path/to/themes`
2. Provide custom fonts via `MAPTOART_FONTS_DIR=/path/to/fonts` or `--font-family "Some Google Font"`

## 6. Keeping dependencies aligned

Whenever you bump dependencies in `pyproject.toml` (whether for this repo or a consumer), run:

```bash
./scripts/sync_requirements.sh
```

`requirements.txt` stays in lockstep and CI will fail if the files diverge.

## 7. CI integration tips

- Call `maptoart-cli --log-format json` to stream structured events into your build logs.
- For deterministic renders, set `MAPTOART_OUTPUT_DIR` to an isolated workspace path and collect both the poster PNG/PDF and its sibling metadata JSON.
- Cache `~/.cache/maptoart/fonts` between runs if your workloads frequently fetch Google Fonts.

Refer back to the main README for detailed CLI flags, examples, and troubleshooting steps.
