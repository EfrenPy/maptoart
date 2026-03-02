"""HTML gallery generator for poster collections."""

import html
import json
import logging
from pathlib import Path
from urllib.parse import quote

__all__ = ["generate_gallery"]

_logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".png", ".svg", ".pdf"}

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'">
<title>Map Poster Gallery</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1a1a2e;
    color: #eee;
    margin: 0;
    padding: 2rem;
  }}
  h1 {{
    text-align: center;
    margin-bottom: 2rem;
    font-weight: 300;
    letter-spacing: 0.1em;
  }}
  .gallery {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1.5rem;
    max-width: 1400px;
    margin: 0 auto;
  }}
  .card {{
    background: #16213e;
    border-radius: 8px;
    overflow: hidden;
    transition: transform 0.2s;
  }}
  .card:hover {{
    transform: translateY(-4px);
  }}
  .card img {{
    width: 100%;
    height: auto;
    display: block;
  }}
  .card .info {{
    padding: 0.8rem 1rem;
  }}
  .card .info h3 {{
    margin: 0 0 0.3rem 0;
    font-weight: 500;
  }}
  .card .info p {{
    margin: 0;
    font-size: 0.85rem;
    color: #aaa;
  }}
  .empty {{
    text-align: center;
    padding: 4rem;
    color: #888;
    font-size: 1.2rem;
  }}
</style>
</head>
<body>
<h1>Map Poster Gallery</h1>
{content}
</body>
</html>
"""


def generate_gallery(poster_dir: str, output_path: str | None = None) -> str:
    """Create an index.html with a CSS grid of poster thumbnails.

    Args:
        poster_dir: Directory containing poster images and metadata JSON files.
        output_path: Path for the HTML file. Defaults to ``poster_dir/index.html``.

    Returns:
        Path to the generated HTML file.
    """
    poster_dir_path = Path(poster_dir)
    if output_path is None:
        output_path = str(poster_dir_path / "index.html")

    # Collect image files
    images = sorted(
        f for f in poster_dir_path.iterdir()
        if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS
    )

    if not images:
        content = '<div class="empty">No posters found in this directory.</div>'
    else:
        cards = []
        for img in images:
            # Try to find metadata sidecar
            meta_path = img.with_suffix(".json")
            meta = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass

            city = html.escape(meta.get("city", img.stem.split("_")[0].title()))
            country = html.escape(meta.get("country", ""))
            theme = html.escape(meta.get("theme", ""))
            title = f"{city}, {country}" if country else city
            subtitle = f"Theme: {theme}" if theme else html.escape(img.name)

            # URL-encode filename for src attributes (handles #, ?, %);
            # html.escape is used separately for text content.
            rel = quote(img.name, safe="")

            if img.suffix.lower() == ".pdf":
                # PDFs can't be shown as <img>, use a placeholder
                card = (
                    f'<div class="card">'
                    f'<div style="padding:2rem;text-align:center;background:#0f3460;">'
                    f'<p style="font-size:3rem;">📄</p>'
                    f'<p>{rel}</p>'
                    f'</div>'
                    f'<div class="info"><h3>{title}</h3><p>{subtitle}</p></div>'
                    f'</div>'
                )
            else:
                card = (
                    f'<div class="card">'
                    f'<img src="{rel}" alt="{title}" loading="lazy">'
                    f'<div class="info"><h3>{title}</h3><p>{subtitle}</p></div>'
                    f'</div>'
                )
            cards.append(card)

        content = '<div class="gallery">\n' + "\n".join(cards) + "\n</div>"

    page = _HTML_TEMPLATE.format(content=content)
    Path(output_path).write_text(page, encoding="utf-8")
    return output_path
