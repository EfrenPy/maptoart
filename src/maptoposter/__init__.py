"""Public API for programmatic access to the map poster renderer."""

from .core import (
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
