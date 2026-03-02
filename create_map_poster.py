"""Backward compatible wrapper for the legacy script name."""

import warnings

from maptoart.cli import main

warnings.warn(
    "create_map_poster.py is deprecated. Use 'maptoart-cli' instead.",
    DeprecationWarning,
    stacklevel=1,
)

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
