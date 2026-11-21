"""Uruchomienie interfejsu:  python -m odwolania2json.web [--host 127.0.0.1] [--port 8000] [--open]"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from .documents import DEFAULT_MAX_BYTES
from .server import make_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="odwolania2json.web", description="Interfejs graficzny Odwołania2JSON")
    parser.add_argument("--host", default="127.0.0.1", help="Adres nasłuchu (domyślnie tylko ten komputer)")
    parser.add_argument("--port", type=int, default=8000, help="Port (domyślnie 8000)")
    parser.add_argument("--open", action="store_true", help="Otwórz interfejs w przeglądarce")
    parser.add_argument("--data-dir", help="Katalog danych (dokumenty); domyślnie katalog danych użytkownika poza projektem")
    parser.add_argument("--max-upload-mb", type=int, default=DEFAULT_MAX_BYTES // (1024 * 1024),
                        help="Maksymalny rozmiar pojedynczego pliku PDF w MB (domyślnie 50)")
    args = parser.parse_args(argv)
    try:
        server = make_server(args.host, args.port, args.data_dir, args.max_upload_mb * 1024 * 1024)
    except OSError as exc:
        print(f"Nie można uruchomić serwera na {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 2
    url = f"http://{args.host}:{args.port}/"
    print(f"Odwołania2JSON — interfejs dostępny pod adresem {url}  (Ctrl+C kończy pracę)")
    print(f"Dokumenty przechowywane w: {server.store.root}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
