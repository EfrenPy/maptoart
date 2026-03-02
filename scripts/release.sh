#!/usr/bin/env bash
set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required (https://cli.github.com/)" >&2
  exit 1
fi

if ! command -v python >/dev/null 2>&1; then
  echo "python is required" >&2
  exit 1
fi

BUMP_KIND="${1:-patch}"
DRY_RUN="false"

if [[ "${2:-}" == "--dry-run" || "${1:-}" == "--dry-run" ]]; then
  DRY_RUN="true"
fi

if [[ "$BUMP_KIND" == "--dry-run" ]]; then
  BUMP_KIND="patch"
fi

if [[ "$BUMP_KIND" != "patch" && "$BUMP_KIND" != "minor" && "$BUMP_KIND" != "major" ]]; then
  echo "Usage: ./scripts/release.sh [patch|minor|major] [--dry-run]" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean. Commit or stash changes before release." >&2
  exit 1
fi

gh auth status >/dev/null

python - "$BUMP_KIND" <<'PY' > /tmp/maptoart_next_version.txt
from datetime import date
from pathlib import Path
import re
import sys

bump_kind = sys.argv[1]

pyproject = Path("pyproject.toml")
changelog = Path("CHANGELOG.md")

text = pyproject.read_text(encoding="utf-8")
match = re.search(r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"\s*$', text, re.M)
if not match:
    raise SystemExit("Could not find project version in pyproject.toml")

major, minor, patch = map(int, match.groups())
if bump_kind == "patch":
    patch += 1
elif bump_kind == "minor":
    minor += 1
    patch = 0
else:
    major += 1
    minor = 0
    patch = 0

new_version = f"{major}.{minor}.{patch}"
new_text = text[:match.start(0)] + f'version = "{new_version}"' + text[match.end(0):]
pyproject.write_text(new_text, encoding="utf-8")

cl = changelog.read_text(encoding="utf-8")
header = f"## [{new_version}] - {date.today().isoformat()}"
if header not in cl:
    insert_after = "and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n"
    idx = cl.find(insert_after)
    if idx == -1:
        raise SystemExit("Could not find changelog insertion point")
    idx += len(insert_after)
    section = (
        "\n"
        f"{header}\n\n"
        "### Changed\n"
        "- Describe release changes.\n"
    )
    cl = cl[:idx] + section + cl[idx:]
    changelog.write_text(cl, encoding="utf-8")

print(new_version)
PY

NEXT_VERSION="$(tr -d '[:space:]' < /tmp/maptoart_next_version.txt)"
TAG="v${NEXT_VERSION}"

echo "Prepared release $TAG"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Dry run: updated pyproject.toml and CHANGELOG.md only."
  exit 0
fi

git add pyproject.toml CHANGELOG.md
git commit -m "release: bump to $TAG"
git tag "$TAG"
git push origin main
git push origin "$TAG"
gh release create "$TAG" --target main --title "$TAG" --generate-notes

echo "Release created: $TAG"
