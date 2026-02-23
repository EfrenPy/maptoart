# Using maptoposter in Other Projects

This guide outlines the recommended ways to install and consume the `maptoposter` package from neighboring services (e.g., marketing sites, backend workers, or design tools).

## 1. Install via pip from Git (recommended)

Reference a tagged commit directly from Git when you need a reproducible build:

```bash
pip install "git+https://github.com/EfrenPy/maptoposter.git@v0.2.0"
```

If your environment uses SSH:

```bash
pip install "git+ssh://git@github.com/EfrenPy/maptoposter.git@main"
```

Pinning to tags (or SHAs) guarantees rebuilds are deterministic across consumers.

## 2. Install locally in editable mode

When working on `maptoposter` alongside another project (e.g., `maptoposterpage`):

```bash
cd /path/to/maptoposterpage
pip install -e ../maptoposter
```

Editable installs keep both repos in sync—updating the library immediately affects the consumer app.

## 3. Build a wheel for distribution

If you prefer uploading artifacts to an internal index or attaching them to releases:

```bash
cd /path/to/maptoposter
uv build  # or: python -m build
pip install dist/maptoposter-<version>-py3-none-any.whl
```

Publish the wheel to your preferred package index (private PyPI, GitHub Packages, etc.) so downstream services can `pip install maptoposter==<version>` without referencing Git.

## 4. Programmatic usage

```python
from maptoposter import PosterGenerationOptions, generate_posters

options = PosterGenerationOptions(
    city="Paris",
    country="France",
    themes=["terracotta", "neon_cyberpunk"],
    output_dir="/tmp/posters",
)
generate_posters(options)
```

See `examples/basic_python_usage.py` for a complete snippet plus logging hooks. A sample YAML config lives in `examples/config/poster.yaml` and mirrors every CLI flag.

## 5. Packaging data

Themes and default Roboto fonts ship with the wheel. If your app needs custom palettes or fonts:

1. Drop new theme JSON files into a directory and set `MAPTOPOSTER_THEMES_DIR=/path/to/themes`
2. Provide custom fonts via `MAPTOPOSTER_FONTS_DIR=/path/to/fonts` or `--font-family "Some Google Font"`

## 6. Keeping dependencies aligned

Whenever you bump dependencies in `pyproject.toml` (whether for this repo or a consumer), run:

```bash
./scripts/sync_requirements.sh
```

`requirements.txt` stays in lockstep and CI will fail if the files diverge.

## 7. CI integration tips

- Call `maptoposter-cli --log-format json` to stream structured events into your build logs.
- For deterministic renders, set `MAPTOPOSTER_OUTPUT_DIR` to an isolated workspace path and collect both the poster PNG/PDF and its sibling metadata JSON.
- Cache `~/.cache/maptoposter/fonts` between runs if your workloads frequently fetch Google Fonts.

Refer back to the main README for detailed CLI flags, examples, and troubleshooting steps.
