"""Public API for programmatic access to the map poster renderer."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("maptoart")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

from .batch import load_batch_file, run_batch
from .font_management import get_active_fonts
from .gallery import generate_gallery
from .core import (
    REQUIRED_THEME_KEYS,
    PosterGenerationOptions,
    StatusReporter,
    cache_clear,
    cache_get,
    cache_info,
    cache_set,
    create_poster,
    create_poster_from_options,
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
    "cache_clear",
    "cache_get",
    "cache_info",
    "cache_set",
    "create_poster",
    "create_poster_from_options",
    "generate_gallery",
    "generate_output_filename",
    "generate_posters",
    "get_active_fonts",
    "get_available_themes",
    "get_coordinates",
    "list_themes",
    "load_batch_file",
    "load_theme",
    "print_examples",
    "run_batch",
]
