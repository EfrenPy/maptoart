"""Backward compatible wrapper for the legacy script name."""

from maptoposter.cli import main


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
