"""Public API for programmatic access to the map poster renderer."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("maptoposter")
except PackageNotFoundError:
    __version__ = "0.0.0"

from .core import (
    REQUIRED_THEME_KEYS,
    PosterGenerationOptions,
    StatusReporter,
    cache_get,
    cache_set,
    create_poster,
    generate_output_filename,
    generate_posters,
    get_available_themes,
    get_coordinates,
    list_themes,
    load_theme,
    print_examples,
)

__all__ = [
    "REQUIRED_THEME_KEYS",
    "__version__",
    "PosterGenerationOptions",
    "StatusReporter",
    "cache_get",
    "cache_set",
    "create_poster",
    "generate_output_filename",
    "generate_posters",
    "get_available_themes",
    "get_coordinates",
    "list_themes",
    "load_theme",
    "print_examples",
]
