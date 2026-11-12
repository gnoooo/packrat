#!/usr/bin/env bash
# Bump pyproject.toml and refresh uv.lock in one step.
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "usage: $0 X.Y.Z" >&2
    exit 2
fi
VERSION="$1"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.+]+)?$ ]]; then
    echo "bump-version: invalid version: $VERSION" >&2
    exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python - "$VERSION" <<'PY'
import pathlib
import re
import sys
version = sys.argv[1]
path = pathlib.Path("pyproject.toml")
text = path.read_text(encoding="utf-8")
text, count = re.subn(
    r'^version = ".*"$', f'version = "{version}"', text, count=1, flags=re.M
)
if count != 1:
    raise SystemExit("could not find project version")
path.write_text(text, encoding="utf-8")
PY
uv lock
python3 dev/check_version.py
git diff -- pyproject.toml uv.lock
