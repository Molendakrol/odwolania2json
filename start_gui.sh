#!/usr/bin/env sh
# Uruchamia interfejs graficzny Odwołania2JSON i otwiera go w przeglądarce (http://127.0.0.1:8000).
# Zamknięcie: Ctrl+C w tym oknie. Dodatkowe opcje, np.: ./start_gui.sh --port 8080
cd "$(dirname "$0")" || exit 1
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m odwolania2json.web --open "$@"
