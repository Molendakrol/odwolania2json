"""Szablony wyjściowe: wczytanie, budowa wyniku (z null jako wartością domyślną) i walidacja typów.

Szablon to JSON o strukturze wyniku, w którym liście są deklaracjami typów
(`string|null`, `date|null`, `int|null`, `number|null`) — patrz config/templates/README.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "config" / "templates"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TYPES = {"string", "date", "int", "number"}


class TemplateError(ValueError):
    pass


def load_template(name: str, directory: Path = TEMPLATES_DIR) -> dict:
    path = directory / f"{name}.json"
    try:
        template = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TemplateError(f"Brak szablonu dla klasy '{name}': {path}") from exc
    _check_template(template, name)
    return template


def _check_template(node: Any, where: str) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _check_template(v, f"{where}.{k}")
    elif isinstance(node, list):
        if len(node) != 1:
            raise TemplateError(f"{where}: tablica w szablonie musi mieć dokładnie jeden element-wzorzec")
        _check_template(node[0], f"{where}[]")
    elif isinstance(node, str):
        base = node.split("|")[0]
        if base not in _TYPES:
            raise TemplateError(f"{where}: nieznany typ '{node}'")
    else:
        raise TemplateError(f"{where}: liść szablonu musi być deklaracją typu (string)")


def _coerce(value: Any, spec: str) -> Any:
    """Rzutuje wartość na typ ze szablonu; wartości nie dające się rzutować -> None."""
    if value is None:
        return None
    base = spec.split("|")[0]
    try:
        if base == "int":
            return int(value)
        if base == "number":
            return float(value)
        if base in ("string", "date"):
            text = str(value).strip()
            if not text:
                return None
            if base == "date" and not _DATE.match(text):
                return None
            return text
    except (TypeError, ValueError):
        return None
    return None


def build_output(template: Any, data: Any) -> Any:
    """Tworzy wynik o dokładnie takiej strukturze jak szablon; brakujące wartości = null,
    nadmiarowe klucze z `data` są pomijane, typy są rzutowane wg szablonu."""
    if isinstance(template, dict):
        data = data if isinstance(data, dict) else {}
        return {k: build_output(spec, data.get(k)) for k, spec in template.items()}
    if isinstance(template, list):
        items = data if isinstance(data, list) and data else [None]
        return [build_output(template[0], item) for item in items]
    return _coerce(data, template)


def validate(output: Any, template: Any, path: str = "$") -> list[str]:
    """Zwraca listę niezgodności wyniku z szablonem (pusta lista = zgodny)."""
    errors: list[str] = []
    if isinstance(template, dict):
        if not isinstance(output, dict):
            return [f"{path}: oczekiwano obiektu"]
        missing = [k for k in template if k not in output]
        extra = [k for k in output if k not in template]
        errors += [f"{path}.{k}: brak klucza" for k in missing]
        errors += [f"{path}.{k}: nadmiarowy klucz" for k in extra]
        for k in template:
            if k in output:
                errors += validate(output[k], template[k], f"{path}.{k}")
    elif isinstance(template, list):
        if not isinstance(output, list):
            return [f"{path}: oczekiwano tablicy"]
        for i, item in enumerate(output):
            errors += validate(item, template[0], f"{path}[{i}]")
    else:
        nullable = template.endswith("|null")
        base = template.split("|")[0]
        if output is None:
            if not nullable:
                errors.append(f"{path}: null niedozwolony")
        elif base == "int" and not (isinstance(output, int) and not isinstance(output, bool)):
            errors.append(f"{path}: oczekiwano int, jest {type(output).__name__}")
        elif base == "number" and not (isinstance(output, (int, float)) and not isinstance(output, bool)):
            errors.append(f"{path}: oczekiwano liczby, jest {type(output).__name__}")
        elif base == "string" and not isinstance(output, str):
            errors.append(f"{path}: oczekiwano string, jest {type(output).__name__}")
        elif base == "date" and not (isinstance(output, str) and _DATE.match(output)):
            errors.append(f"{path}: oczekiwano daty YYYY-MM-DD, jest {output!r}")
    return errors
