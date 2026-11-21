#!/usr/bin/env sh
# Przetwarza wszystkie PDF z ./input: JSON-y do ./output, diagnostyka do ./diagnostics.
cd "$(dirname "$0")" || exit 1
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m odwolania2json run --input input --output output --diagnostics diagnostics "$@"
