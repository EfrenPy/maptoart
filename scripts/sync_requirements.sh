#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to sync requirements (https://docs.astral.sh/uv/)" >&2
  exit 1
fi

UV_ARGS=("pyproject.toml" "--output-file" "requirements.txt")

echo "Updating requirements.txt from pyproject.toml using uv pip compile..."
uv pip compile "${UV_ARGS[@]}"
echo "✓ requirements.txt synced"
