"""Zapis wynikowych plików JSON w formacie organizatora.

`output/<ścieżka względna PDF>` z rozszerzeniem `.json` — nazwa jak plik wejściowy (README organizatora).
Dla `unknown` zapisywany jest „pusty” wynik w trybie z config/app.toml (domyślnie `{}`).

Przed zapisem każdy wynik jest sprawdzany: zgodność ze szablonem (FieldResult.validation_errors)
oraz poprawność składni JSON (serializacja + ponowne wczytanie). Wynik niepoprawny NIE jest zapisywany —
trafia na listę błędów, a CLI kończy się kodem błędu.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .pipeline import DocumentResult

APP_CONFIG = Path(__file__).resolve().parents[1] / "config" / "app.toml"
UNKNOWN_MODES = ("empty_object", "empty_file")


class OutputExistsError(FileExistsError):
    """Pliki wynikowe już istnieją, a nadpisywanie nie zostało jawnie dozwolone."""

    def __init__(self, paths: list[Path]):
        self.paths = paths
        super().__init__(f"{len(paths)} plików wynikowych już istnieje (użyj --overwrite), np. {paths[0]}")


@dataclass
class WriteReport:
    written: list[Path] = field(default_factory=list)
    rejected: list[tuple[str, list[str]]] = field(default_factory=list)   # (plik wejściowy, powody)


def load_unknown_mode(path: Path = APP_CONFIG) -> str:
    with open(path, "rb") as fh:
        mode = tomllib.load(fh)["output"]["unknown"]
    if mode not in UNKNOWN_MODES:
        raise ValueError(f"output.unknown musi być jednym z {UNKNOWN_MODES}, jest: {mode!r}")
    return mode


def output_path(out_dir: Path, result: DocumentResult) -> Path:
    return Path(out_dir) / Path(result.relative_path).with_suffix(".json")


def _serialize(result: DocumentResult, unknown_mode: str) -> tuple[str | None, list[str]]:
    """Zwraca (treść pliku, błędy). Treść None = wynik odrzucony."""
    if result.fields is None:
        return ("{}\n" if unknown_mode == "empty_object" else ""), []
    if result.fields.validation_errors:
        return None, list(result.fields.validation_errors)
    content = json.dumps(result.fields.data, ensure_ascii=False, indent=2) + "\n"
    try:
        if json.loads(content) != result.fields.data:
            return None, ["serializacja JSON zmieniła dane"]
    except json.JSONDecodeError as exc:   # pragma: no cover - zabezpieczenie
        return None, [f"niepoprawny JSON: {exc}"]
    return content, []


def write_outputs(results: list[DocumentResult], out_dir: str | Path, unknown_mode: str = "empty_object",
                  overwrite: bool = False) -> WriteReport:
    out_dir = Path(out_dir)
    targets = [output_path(out_dir, r) for r in results]
    if not overwrite:
        existing = [t for t in targets if t.exists()]
        if existing:
            raise OutputExistsError(existing)

    report = WriteReport()
    for r, target in zip(results, targets):
        content, errors = _serialize(r, unknown_mode)
        if content is None:
            report.rejected.append((r.relative_path, errors))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        report.written.append(target)
    return report
