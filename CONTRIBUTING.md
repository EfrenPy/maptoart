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
