"""Backward compatible wrapper for the legacy script name."""

import warnings

from maptoposter.cli import main

warnings.warn(
    "create_map_poster.py is deprecated. Use 'maptoposter-cli' instead.",
    DeprecationWarning,
    stacklevel=1,
)

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
