# Contributing to maptoart

Thanks for your interest in contributing!

## Dev Setup

```bash
uv sync
```

## Running Tests

```bash
uv run pytest
```

## Linting

```bash
uv run flake8 src/ tests/
uv run mypy src/maptoart/
```

## Theme Contributions

Theme files live in `src/maptoart/themes/` as JSON. Each theme must include all required keys (see `REQUIRED_THEME_KEYS` in `core.py`):

- `name`, `description`, `bg`, `text`, `gradient_color`, `water`, `parks`
- `road_motorway`, `road_primary`, `road_secondary`, `road_tertiary`, `road_residential`, `road_default`

Validate your JSON before committing:

```bash
python -m json.tool src/maptoart/themes/your_theme.json
```

## More Details

See [AGENTS.md](AGENTS.md) for full operational guidance.

## Releases

Automate version bump + changelog section + tag + GitHub release (which triggers PyPI publish):

```bash
./scripts/release.sh patch
```

Options:

- `./scripts/release.sh minor`
- `./scripts/release.sh major`
- `./scripts/release.sh patch --dry-run` (only updates files, no commit/tag/release)

You can also run releases from GitHub UI without terminal:

- Go to **Actions -> Manual Release -> Run workflow**
- Provide `version` (for example `0.5.2`)
- Optionally keep `run_validation` enabled to run lint/type/tests before tagging
- Choose `tests_scope`:
  - `full`: runs complete `pytest`
  - `smoke`: runs a fast representative subset
- The workflow updates `pyproject.toml` and `CHANGELOG.md`, creates `v<version>` tag, and publishes a GitHub release
- The release then triggers `.github/workflows/publish.yml` to publish to PyPI
