"""Minimal example showing how to call the library from Python."""

from pathlib import Path

from maptoart import PosterGenerationOptions, generate_posters, StatusReporter


def main() -> None:
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True, parents=True)

    options = PosterGenerationOptions(
        city="Paris",
        country="France",
        themes=["terracotta", "neon_cyberpunk"],
        distance=9000,
        output_dir=str(output_dir),
    )

    reporter = StatusReporter(json_mode=True)
    files = generate_posters(options, status_reporter=reporter)
    print("Generated files: ", files)


if __name__ == "__main__":
    main()
