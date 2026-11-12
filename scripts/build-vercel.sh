#!/usr/bin/env bash
set -euo pipefail

version=314.0.7
archive="${TMPDIR:-/tmp}/pyodide-${version}.tar.bz2"

curl --fail --location --retry 3 --output "$archive" \
  "https://github.com/pyodide/pyodide/releases/download/${version}/pyodide-${version}.tar.bz2"
echo "192b5864e6e6d30ab074861af800cb8b4acb0998ef0f9342c3367448aeb86645  $archive" | sha256sum --check

python3 scripts/prepare-pyodide.py \
  "$archive" public/pyodide/v${version}/full public/python/setup.py

npm run build
