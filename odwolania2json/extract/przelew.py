"""Ekstrakcja pól z potwierdzenia przelewu.

Metoda: etykiety z config/extraction.toml (wg priorytetu) + typowany wzorzec wartości szukany
bezpośrednio po etykiecie. Działa dla układów: etykieta/wartość w osobnych liniach,
„ETYKIETA ....... wartość”, „Etykieta: wartość” oraz opisowego („z rachunku nr … w kwocie …”).
"""

from __future__ import annotations

import re

from .base import ExtractionContext, FieldResult
from .normalizers import (
    ACCOUNT_RE, DATE_RE, account_digits, collapse_ws, normalize_account, parse_amount, parse_date,
    strip_lead_letter, strip_ocr_noise,
)

_AMOUNT_VALUE = re.compile(
    r"(?:(?:PLN|zł)\s*)?(?:\d{1,3}(?:[  .]\d{3})+|\d+)[.,]\d{2}(?!\d)|\d+(?:[  ]\d{3})*\s*(?:PLN|zł)",
    re.IGNORECASE,
)
_REF_VALUE = re.compile(r"\b(?=[A-Z0-9/-]*\d)[A-Z0-9][A-Z0-9/-]{5,}\b")
_LEADER = re.compile(r"^[\s.:…·\-–]*")
_LABEL_LINE = re.compile(r"^[A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż/. ]{2,40}(:|\s\.{3,})|^[-=*]{3,}")

WINDOW = {"date": 80, "amount": 90, "account": 100, "ref": 100}
# Układy tabelaryczne (etykiety kolumn, potem wartości): numer rachunku może stać dalej od etykiety sekcji.
# Okno jest szersze, ale zawsze ucinane na pierwszej etykiecie drugiej strony przelewu.
ACCOUNT_WINDOW_TABLE = 260
_DATE_SPACED = re.compile(r"(?<!\d)(\d{1,2}) (\d{2}) (20\d{2})(?!\d)")   # „20 04 2026” (OCR bez kropek), tylko po etykiecie
_CURRENCY_AMOUNT = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:[ \u00a0.]\d{3})+|\d+)[.,]\d{2}\s*(?:PLN|zł)", re.IGNORECASE)
_PUNCT_LINE = re.compile(r"^[^\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+$")
_DIACRITIC_CLASS = {"ą": "[ąa]", "ć": "[ćc]", "ę": "[ęe]", "ł": "[łl]", "ń": "[ńn]", "ó": "[óo]", "ś": "[śs]",
                    "ź": "[źz]", "ż": "[żz]"}


def _tolerant(word: str) -> str:
    """Etykieta odporna na zgubione przez OCR polskie znaki: 'płatności' pasuje też do 'platnosci'."""
    return "".join(_DIACRITIC_CLASS.get(ch, re.escape(ch)) for ch in word.lower())


def _label_regex(label: str, line_start: bool = False) -> re.Pattern[str]:
    body = r"\s+".join(_tolerant(w) for w in label.split())
    prefix = r"(?:^|\n)[ \t]*" if line_start else r"(?<![\wąćęłńóśźż])"
    return re.compile(prefix + body + r"(?![\wąćęłńóśźż])", re.IGNORECASE)


def _excluded(text: str, start: int, excludes: list[str]) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    head = text[line_start:start + 40].lower()
    return any(head.lstrip().startswith(e) for e in excludes)


def _find_after_labels(text, labels, value_re, window, excludes=(), skip=None, stop_labels=()):
    """Pierwsza wartość typu `value_re` znaleziona tuż po etykiecie (etykiety wg priorytetu).
    `skip` = zakres tekstu (np. tytuł przelewu), w którym słowa-etykiety nie są etykietami.
    `stop_labels` = etykiety, na których okno wyszukiwania jest ucinane (np. strona odbiorcy dla rachunku nadawcy)."""
    stops = [_label_regex(s) for s in stop_labels]
    for label in labels:
        for m in _label_regex(label).finditer(text):
            if _excluded(text, m.start(), list(excludes)):
                continue
            if skip and skip[0] <= m.start() < skip[1]:
                continue
            segment = text[m.end(): m.end() + window]
            for s in stops:
                if (cut := s.search(segment)) is not None:
                    segment = segment[: cut.start()]
            v = value_re.search(segment)
            if v:
                return v.group(0), f"{text[m.start():m.end()]} {v.group(0)}"
    return None, None


_OTHER_LABELS = ["status", "waluta", "opłata", "opłata/prowizja", "prowizja", "zleceniodawca", "odbiorca", "nadawca",
                 "beneficjent", "płatnik", "adresat przelewu", "typ operacji", "kanał", "tryb", "nr klienta", "godz.",
                 "dane nadawcy", "dane odbiorcy", "szczegóły operacji", "zrealizowano"]


def _is_label_line(line: str, known: list[str]) -> bool:
    """Linia jest etykietą, gdy zaczyna się od znanej etykiety, po której jest koniec linii, ':', '/', kropki lub liczba."""
    low = line.lower()
    for k in known:
        if low.startswith(k) and re.match(r"^\s*(?:$|[:/]|\.{2,}|\s+\d)", low[len(k):]):
            return True
    return bool(_LABEL_LINE.match(line))


# Stopki i pola banku, na których kończy się tytuł (także po pominiętej linii-śmieci OCR).
_TITLE_STOP = re.compile(
    r"^(?:dokument\b|signature\b|typ\s+(?:transakcji|operacji)|data\s+(?:wystawienia|wydruku|księgowania|waluty)|"
    r"niniejsze\b|potwierdzenie\s+ma\b|wydruk\b|strona\s+\d|bank\s+nie\b|kwota\b|status\b|nr\s+ref|numer\s+ref)",
    re.IGNORECASE,
)


def _extract_title(text: str, labels: list[str], all_labels: list[str]):
    """Zwraca (tytuł, uzasadnienie, zakres (start, koniec) wartości tytułu w tekście)."""
    known = sorted({l.lower() for l in all_labels} | set(_OTHER_LABELS), key=len, reverse=True)
    for label in labels:
        m = _label_regex(label, line_start=True).search(text)
        if not m:
            continue
        rest = text[m.end():]
        first, _, tail = rest.partition("\n")
        parts = [_LEADER.sub("", first)]
        end = m.end() + len(first)
        for line in tail.split("\n")[:8]:
            s = line.strip()
            if s and _PUNCT_LINE.match(s):      # linia-śmieć OCR (':', '!', '.') wewnątrz wartości
                continue
            if not s or _is_label_line(s, known) or _TITLE_STOP.match(s):
                break
            parts.append(s)
            end += len(line) + 1
        value = collapse_ws(" ".join(p for p in parts if p))
        if value:
            value = strip_ocr_noise(value.strip().strip("„”\"").rstrip(".").strip("„”\"").strip()) or ""
            value = strip_lead_letter(value) or ""
            return value or None, f"{m.group(0).strip()} {value}", (m.end(), end)
    return None, None, None


def extract_przelew(ctx: ExtractionContext, cfg: dict) -> FieldResult:
    labels: dict[str, list[str]] = cfg["przelew"]["labels"]
    excludes: dict[str, list[str]] = cfg["przelew"].get("exclude", {})
    keep_prefix = bool(cfg["przelew"].get("keep_country_prefix", False))
    text = ctx.text
    out: dict = {}
    res = FieldResult(data={})

    # Tytuł najpierw: jego treść (np. „zwrot na rachunek …”) nie może być źródłem innych pól.
    all_labels = [l for group in labels.values() for l in group]
    out_title, ev_title, title_span = _extract_title(text, labels["tytul"], all_labels)

    raw, ev = _find_after_labels(text, labels["data_operacji"], DATE_RE, WINDOW["date"],
                                 excludes.get("data_operacji", ()), title_span)
    out["data_operacji"] = parse_date(raw)
    if out["data_operacji"] is None:   # „20 04 2026” — data bez separatorów, tylko bezpośrednio po etykiecie daty
        raw_sp, ev_sp = _find_after_labels(text, labels["data_operacji"], _DATE_SPACED, 40,
                                           excludes.get("data_operacji", ()), title_span)
        if raw_sp:
            out["data_operacji"], ev = parse_date(raw_sp.replace(" ", ".")), ev_sp
    raw_amount, ev_amount = _find_after_labels(text, labels["kwota"], _AMOUNT_VALUE, WINDOW["amount"],
                                               excludes.get("kwota", ()), title_span)
    out["kwota"] = parse_amount(raw_amount)
    if out["kwota"] is None:
        # Brak etykiety kwoty: jedyna (różna od zera) kwota z walutą w dokumencie poza tytułem.
        amounts = {parse_amount(a.group(0)) for a in _CURRENCY_AMOUNT.finditer(text)
                   if not (title_span and title_span[0] <= a.start() < title_span[1])} - {0.0, None}
        if len(amounts) == 1:
            out["kwota"] = amounts.pop()
            ev_amount = "jedyna kwota z walutą w dokumencie"
    raw_z, ev_z = _find_after_labels(text, labels["z_rachunku"], ACCOUNT_RE, ACCOUNT_WINDOW_TABLE, skip=title_span,
                                     stop_labels=labels["na_rachunek"])
    raw_na, ev_na = _find_after_labels(text, labels["na_rachunek"], ACCOUNT_RE, ACCOUNT_WINDOW_TABLE, skip=title_span,
                                       stop_labels=labels["z_rachunku"])
    out["z_rachunku"] = normalize_account(raw_z, keep_prefix)
    out["na_rachunek"] = normalize_account(raw_na, keep_prefix)
    raw_ref, ev_ref = _find_after_labels(text, labels["numer_transakcji"], _REF_VALUE, WINDOW["ref"], skip=title_span)
    out["numer_transakcji"] = raw_ref
    out["tytul"] = out_title

    # Rachunki bez etykiet: jeśli w dokumencie (poza tytułem) są dokładnie dwa różne numery i brakuje jednego z nich.
    found: dict[str, str] = {}   # cyfry -> pierwsza postać z dokumentu ('PL…' i '…' to ten sam rachunek)
    for a in ACCOUNT_RE.finditer(text):
        if title_span and title_span[0] <= a.start() < title_span[1]:
            continue
        if acc := normalize_account(a.group(0), keep_prefix):
            found.setdefault(account_digits(acc), acc)
    accounts = list(found.values())
    if len(accounts) == 2 and bool(out["z_rachunku"]) != bool(out["na_rachunek"]):
        known = account_digits(out["z_rachunku"] or out["na_rachunek"])
        other = accounts[1] if account_digits(accounts[0]) == known else accounts[0]
        out["na_rachunek" if out["z_rachunku"] else "z_rachunku"] = other
        res.warnings.append("rachunek bez etykiety uzupełniony jako drugi numer w dokumencie")

    for key, e in (("data_operacji", ev), ("kwota", ev_amount), ("z_rachunku", ev_z), ("na_rachunek", ev_na),
                   ("numer_transakcji", ev_ref), ("tytul", ev_title)):
        if e:
            res.evidence[f"przelew_bankowy.{key}"] = collapse_ws(e)
        elif out.get(key) is None:
            res.warnings.append(f"nie znaleziono pola przelew_bankowy.{key}")
    res.data = {"przelew_bankowy": out}
    return res
