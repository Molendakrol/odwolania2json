# Odwołania2JSON — przewodnik rozszerzania systemu

Praktyczny przewodnik dla programisty: jak zmieniać reguły i konfigurację oraz jak **dziś** dodać nowy typ dokumentu.
Opis architektury: [`ARCHITEKTURA.md`](ARCHITEKTURA.md). Wszystkie nazwy plików, funkcji i kluczy pochodzą z repozytorium.

> Zasada bezpieczeństwa zmian: po każdej zmianie reguł lub ekstraktorów uruchom pełne testy i ewaluację regresji
> (sekcja 9). Projekt ma rozbudowany zestaw testów regresyjnych — zmiana jednej reguły może wpłynąć na inne klasy.

---

## 1. Jak działa obecna konfiguracja klas dokumentów

Klasa dokumentu występuje w kilku miejscach — część to konfiguracja, część kod:

| Miejsce | Rola | Typ |
|---|---|---|
| `config/classifier_rules.toml`, sekcja `[classes.<klasa>]` | rozpoznawanie klasy (sygnały, progi) | konfiguracja |
| `config/templates/<klasa>.json` | kształt i typy wyniku JSON | konfiguracja |
| `config/extraction.toml` | słowniki używane przez ekstraktor (dziś tylko przelew i czynności odwołania) | konfiguracja |
| `odwolania2json/extract/<klasa>.py`, funkcja `extract_<klasa>` | logika wyodrębniania pól | **kod** |
| `odwolania2json/extract/__init__.py`, słownik `EXTRACTORS` | powiązanie nazwy klasy z ekstraktorem | **kod** |
| `odwolania2json/web/static/js/app.js`, stała `LABELS` | nazwa klasy wyświetlana w GUI | **kod** (tylko wygląd) |

Nazwa klasy musi być **identyczna** we wszystkich miejscach: sekcja `[classes.<klasa>]`, plik `templates/<klasa>.json`
(ładowany przez `load_template(<klasa>)`) i klucz w `EXTRACTORS`. Nazwa `unknown` jest zarezerwowana (`RuleConfigError`).

Przepływ: `RuleClassifier.classify` zwraca etykietę → `Pipeline.process_file` sprawdza `FieldExtractor.supports(etykieta)`
(czyli `etykieta in EXTRACTORS`) → jeśli tak, `FieldExtractor.extract` uruchamia ekstraktor, buduje wynik wg szablonu
i waliduje; jeśli nie, `fields = None` i zapisywany jest `{}`.

## 2. Jak zmienić istniejące reguły klasyfikacji

Plik: `config/classifier_rules.toml`. Kod (`classify/rules.py`) nie wymaga zmian.

**Elementy reguły klasy:**

```toml
[classes.przelew]
description = "Potwierdzenie wykonania przelewu"
min_score = 5.0          # minimalna suma wag dopasowanych sygnałów
min_signals = 2          # minimalna liczba RÓŻNYCH dopasowanych sygnałów
required_any = ["..."]   # co najmniej jeden z tych sygnałów musi pasować

[[classes.przelew.signals]]
name = "status_referencja"   # unikalna nazwa w obrębie klasy (widoczna w diagnostyce i GUI)
pattern = '(zrealizowan\w*|nr\s+ref|...)'
zone = "head"                # head = pierwsze zones.head_chars znaków; body = pierwsze zones.body_pages stron
weight = 1.0
# min_count = 2              # opcjonalnie: ile razy wzorzec musi wystąpić (domyślnie 1)
```

**Zasady pisania wzorców** (wynikające z `RuleClassifier._zone_texts` i `Signal.count`):

- tekst jest zamieniony na małe litery i ma scalone rozstrzelone słowa („P E Ł N O M O C N I C T W O” → „pełnomocnictwo”),
  więc wzorce pisz małymi literami;
- pisz z polskimi znakami — wersja bez nich (`fold_diacritics`) jest dopasowywana automatycznie (odporność na OCR);
- podziały linii są zachowane — można używać `(^|\n)` dla tytułów;
- w TOML używaj literałów w apostrofach (`'...'`), żeby nie podwajać ukośników.

**Weta** (`[[veto]]`): wszystkie wzorce z listy `all` muszą pasować w strefie `zone` (domyślnie `head`);
dopasowane weto zawsze daje `unknown` z powodem `veto:<nazwa>`. Używaj ich dla dokumentów, które zawierają
słownictwo klas pozytywnych, ale nimi nie są (np. orzeczenia KIO).

**Parametry globalne:** `[zones] head_chars`, `body_pages`; `[decision] min_margin` (minimalna przewaga zwycięzcy nad
drugim kandydatem; mniejsza → `conflict`).

**Sprawdzenie zmiany:** `python -m odwolania2json classify plik.pdf` wypisuje klasę i powód; szczegóły (punkty i sygnały
każdej klasy) są w `diagnostics/<plik>.diag.json` w sekcji `classification`. Alternatywny plik reguł bez podmiany
domyślnego: `--rules ścieżka/do/reguł.toml` (polecenia `run`, `classify`, `eval`).

## 3. Jak zmienić konfigurację ekstrakcji

Plik: `config/extraction.toml` (wczytywany w `FieldExtractor.__init__` jako `self.config` i przekazywany jako `cfg`
do każdego ekstraktora).

**Przelew** — używane w `extract_przelew`:

- `[przelew.labels]`: dla każdego z pól `data_operacji`, `kwota`, `z_rachunku`, `na_rachunek`, `numer_transakcji`, `tytul`
  lista etykiet w kolejności priorytetu. Wartość jest szukana bezpośrednio po etykiecie (ta sama linia lub kolejna),
  w oknie znaków z `WINDOW` / `ACCOUNT_WINDOW_TABLE` (stałe w `extract/przelew.py`). Etykiety są dopasowywane bez
  względu na wielkość liter i z tolerancją na brak polskich znaków (`_tolerant`).
- `[przelew.exclude]`: etykiety-dystraktory, po których wartość **nie** jest brana (dziś dla `data_operacji` i `kwota`).

Dodanie nowej etykiety (np. innego banku) = dopisanie jej do listy; nowe **pole** wymaga zmian w kodzie (sekcja 4).

**Odwołanie — kategoria zaskarżonej czynności** — `[[czynnosc]]`, używane przez klasę `_Czynnosc` w `extract/odwolanie.py`:

```toml
[[czynnosc]]
id = 7
opis = "Zaniechanie odrzucenia oferty"
patterns = ['zaniecha\w*\s+odrzuc', 'nie\s+odrzucił']
```

Kategorie są sprawdzane **w kolejności z pliku** — pierwsza pasująca wygrywa, dlatego szczegółowe kategorie
(„zaniechanie odrzucenia”) muszą stać przed ogólnymi („odrzucenie”). `opis` trafia do pola `czynnosc_opis`.

**Pełnomocnictwo** — `extract_pelnomocnictwo` nie korzysta z `extraction.toml`; zmiany wymagają edycji
`extract/pelnomocnictwo.py`.

## 4. Jak zmienić mapowanie pól

„Mapowanie” ma dwie części:

1. **Kształt wyniku** — `config/templates/<klasa>.json`. Klucze i ich kolejność w wyniku pochodzą z szablonu
   (`build_output`). Liście to typy: `string|null`, `date|null`, `int|null`, `number|null`; tablica z jednym elementem = lista obiektów.
2. **Skąd pochodzi wartość** — w kodzie ekstraktora: funkcja zwraca słownik `FieldResult.data` z tymi samymi kluczami,
   np. w `extract_przelew`: `out["kwota"] = parse_amount(raw_amount)` i `res.data = {"przelew_bankowy": out}`.

Ważne zachowanie `build_output`: klucze, których **nie ma w szablonie, są pomijane**, a klucze szablonu, których
ekstraktor nie zwrócił, dostają `null`. Dlatego:

- **zmiana nazwy klucza tylko w szablonie** da pole `null` bez błędu walidacji — trzeba zmienić też klucz w ekstraktorze;
- **nowe pole** = nowy klucz w szablonie + kod, który ustawia wartość pod tym kluczem;
- **zmiana kolejności kluczy** = tylko szablon.

Szablony muszą pozostać zgodne z plikami organizatora (`oczekiwane-json/`); test `tests/test_template.py::test_templates_have_organizer_keys`
sprawdza klucze trzech obecnych szablonów.

## 5. Jak zmienić walidację i normalizację

**Walidacja typów** — w szablonie (`config/templates/<klasa>.json`), bez kodu. Przykład z repozytorium:
`pelnomocnicy[].pesel` ma typ `string|null`; zmiana na `int|null` zmienia typ wyniku (test
`tests/test_template.py::test_pesel_type_is_configurable_via_template`). Walidację wykonują `build_output` (rzutowanie,
wartość nierzutowalna → `null`) i `validate` (błędy → wynik niezapisany).

Dostępne typy są zdefiniowane w `extract/template.py` (`_TYPES = {"string", "date", "int", "number"}`); nowy typ
(np. `bool`) wymaga zmian w `_check_template`, `_coerce` i `validate`.

**Reguły biznesowe** (długość NIP, format TED itd.) nie są częścią walidacji szablonu — są w normalizatorach.

**Normalizacja wartości** — funkcje w `odwolania2json/extract/normalizers.py`, wywoływane bezpośrednio z ekstraktorów:
`parse_date`, `parse_amount`, `normalize_account`, `digits_only`, `normalize_ted`, `normalize_bzp`, `clean_person`,
`collapse_ws`, `strip_ocr_noise`, `drop_punct_tokens`, `repair_email`, `fix_legal_form`, `fix_lone_prepositions`.
Zmiana formatu = zmiana funkcji (wpływa na wszystkie miejsca jej użycia — sprawdź `grep -rn nazwa_funkcji odwolania2json`).

Przykład zmiany do rozważenia: ustalenie organizatora wymaga zachowania prefiksu kraju w numerze rachunku, a
`normalize_account` zwraca dziś 26 cyfr bez „PL”. Zmiana dotyczyłaby `normalize_account` (używana w `extract_przelew`),
testów normalizatorów i porównania z danymi referencyjnymi organizatora.

**Normalizacja tekstu** — `odwolania2json/normalize.py` (`clean_text`, `matching_text`, `fold_diacritics`); zmiany wpływają
na wszystkie klasy i na klasyfikator — wymagają pełnej regresji.

## 6. Jak obecnie dodać nowy typ dokumentu

Przykład: klasa `faktura` (nazwa i pola są ilustracyjne). Kroki 1–3 to konfiguracja, kroki 4–5 to kod.

### Krok 1 — reguły rozpoznawania: `config/classifier_rules.toml`

```toml
# ---------------------------------------------------------------- faktura
[classes.faktura]
description = "Faktura VAT"
min_score = 5.0
min_signals = 2
required_any = ["tytul_faktura"]

[[classes.faktura.signals]]
name = "tytul_faktura"
pattern = '(^|\n)\s*faktura(\s+vat)?\b'
zone = "head"
weight = 3.0

[[classes.faktura.signals]]
name = "strony_faktury"
pattern = '(sprzedawca|nabywca)\s*:'
zone = "head"
weight = 2.0

[[classes.faktura.signals]]
name = "do_zaplaty"
pattern = 'do\s+zapłaty'
zone = "body"
weight = 1.0
```

Sprawdź, czy sygnały nie pokrywają się z istniejącymi klasami (np. słownictwo przelewu: kwoty, rachunki) — przy
podobnej punktacji dwóch kandydatów klasyfikator zwróci `conflict` (patrz `decision.min_margin`).

### Krok 2 — szablon wyniku: `config/templates/faktura.json`

```json
{
  "Faktura": {
    "numer": "string|null",
    "data_wystawienia": "date|null",
    "kwota_brutto": "number|null",
    "nip_sprzedawcy": "string|null"
  }
}
```

Plik jest wymagany także przez GUI: `system_info()` w `web/server.py` wywołuje `load_template` dla każdej klasy
klasyfikatora — bez szablonu `GET /api/info` zwraca błąd.

### Krok 3 (opcjonalnie) — słowniki: `config/extraction.toml`

```toml
[faktura.labels]
numer = ["faktura vat nr", "faktura nr", "numer faktury"]
data_wystawienia = ["data wystawienia", "wystawiono dnia"]
kwota_brutto = ["razem do zapłaty", "do zapłaty", "wartość brutto"]
```

Klucze są dowolne — odczytuje je tylko Twój ekstraktor (przez argument `cfg`).

### Krok 4 — ekstraktor: `odwolania2json/extract/faktura.py`

Kontrakt (alias `Extractor` w `extract/__init__.py`): `Callable[[ExtractionContext, dict], FieldResult]`.
`ExtractionContext` udostępnia `pages`, `text` (strony połączone `\n`), `lines` (niepuste linie) i `flat` (tekst z zwiniętymi białymi znakami).

```python
"""Ekstrakcja pól z faktury (przykład)."""

from __future__ import annotations

import re

from .base import ExtractionContext, FieldResult
from .normalizers import DATE_RE, collapse_ws, digits_only, parse_amount, parse_date

_NIP = re.compile(r"NIP\s*:?\s*(\d[\d\s-]{8,12}\d)", re.IGNORECASE)


def _after_label(text: str, labels: list[str], window: int = 80) -> tuple[str | None, str | None]:
    """Pierwszy fragment tekstu po jednej z etykiet (etykiety wg priorytetu)."""
    low = text.lower()
    for label in labels:
        i = low.find(label)
        if i >= 0:
            segment = text[i + len(label): i + len(label) + window]
            return segment, collapse_ws(text[i: i + len(label) + window])
    return None, None


def extract_faktura(ctx: ExtractionContext, cfg: dict) -> FieldResult:
    labels = cfg["faktura"]["labels"]
    res = FieldResult(data={})
    text = ctx.text
    out: dict = {}

    seg, ev = _after_label(text, labels["numer"], 40)
    m = re.search(r"[A-Z0-9][A-Z0-9/-]{2,}", seg or "", re.IGNORECASE)
    out["numer"] = m.group(0) if m else None
    if m:
        res.evidence["Faktura.numer"] = ev

    seg, ev = _after_label(text, labels["data_wystawienia"])
    out["data_wystawienia"] = parse_date(seg)
    if out["data_wystawienia"]:
        res.evidence["Faktura.data_wystawienia"] = ev

    seg, ev = _after_label(text, labels["kwota_brutto"])
    out["kwota_brutto"] = parse_amount(seg)
    if out["kwota_brutto"] is not None:
        res.evidence["Faktura.kwota_brutto"] = ev

    m = _NIP.search(ctx.flat)
    out["nip_sprzedawcy"] = digits_only(m.group(1), 10) if m else None

    for key, value in out.items():
        if value is None:
            res.warnings.append(f"nie znaleziono pola Faktura.{key}")
    res.data = {"Faktura": out}          # klucze muszą odpowiadać szablonowi z kroku 2
    return res
```

Wskazówki:

- korzystaj z normalizatorów z `extract/normalizers.py` i pomocników z `extract/entities.py` (`find_ids`, `id_clusters`,
  `name_before`, `clean_name`) zamiast pisać nowe;
- wyszukiwanie „etykieta → wartość” z tolerancją na OCR jest zaimplementowane w `extract/przelew.py`
  (`_find_after_labels`, `_label_regex`) — to funkcje prywatne modułu; ich ponowne użycie wymaga świadomej decyzji
  (import funkcji prywatnej albo przeniesienie do modułu wspólnego);
- wypełniaj `res.evidence` (ścieżka pola → fragment źródłowy) — trafia do diagnostyki i GUI;
- wyjątek w ekstraktorze nie zatrzyma przetwarzania (`FieldExtractor.extract` go przechwytuje), ale wszystkie pola będą `null`.

### Krok 5 — rejestracja: `odwolania2json/extract/__init__.py`

```python
from .faktura import extract_faktura

EXTRACTORS: dict[str, Extractor] = {
    "odwolanie": extract_odwolanie,
    "pelnomocnictwo": extract_pelnomocnictwo,
    "przelew": extract_przelew,
    "faktura": extract_faktura,
}
```

`FieldExtractor.__init__` automatycznie wczyta szablon `config/templates/faktura.json` dla nowego wpisu.

### Krok 6 (opcjonalnie) — etykieta w GUI: `odwolania2json/web/static/js/app.js`

```js
const LABELS = { odwolanie: "Odwołanie", pelnomocnictwo: "Pełnomocnictwo", przelew: "Potwierdzenie przelewu", faktura: "Faktura" };
```

Bez tego GUI pokaże surową nazwę klasy (`faktura`); działanie się nie zmienia.

### Co się dzieje, gdy pominiesz kroki

| Pominięty krok | Skutek |
|---|---|
| 1 | klasa nigdy nie zostanie rozpoznana (dokument → `unknown` albo inna klasa) |
| 2 | `FieldExtractor()` zgłosi `TemplateError` przy starcie (gdy klasa jest w `EXTRACTORS`); GUI: błąd `GET /api/info` |
| 4–5 | klasa jest rozpoznawana, ale wynik to `{}` — w pliku JSON nie do odróżnienia od `unknown` |
| 6 | tylko surowa nazwa w GUI |

## 7. Jakie pliki trzeba aktualnie zmodyfikować

| Plik | Zmiana | Wymagane? |
|---|---|---|
| `config/classifier_rules.toml` | sekcja `[classes.<klasa>]` z sygnałami | tak |
| `config/templates/<klasa>.json` | nowy szablon | tak |
| `config/extraction.toml` | słowniki dla ekstraktora | opcjonalnie |
| `odwolania2json/extract/<klasa>.py` | nowa funkcja `extract_<klasa>` | tak (inaczej wynik `{}`) |
| `odwolania2json/extract/__init__.py` | import + wpis w `EXTRACTORS` | tak |
| `odwolania2json/web/static/js/app.js` | wpis w `LABELS` | opcjonalnie |
| `tests/test_classifier.py`, `tests/test_template.py`, `tests/test_web.py` | aktualizacja list klas (sekcja 8) | tak |
| `README.md`, `docs/ARCHITEKTURA.md` | tabela obsługiwanych typów | zalecane |

## 8. Jakie testy należy dodać

Istniejące testy, które **trzeba zaktualizować** (zawierają listę klas):

- `tests/test_classifier.py::test_labels_come_from_config` — `assert classifier.labels == ["odwolanie", "pelnomocnictwo", "przelew"]`,
- `tests/test_template.py::test_templates_have_organizer_keys` — parametryzacja `["odwolanie", "pelnomocnictwo", "przelew"]`
  (nowej klasy nie ma w plikach organizatora — dla niej potrzebny jest osobny test szablonu),
- `tests/test_web.py` — porównanie listy klas zwracanej przez `/api/info`.

Nowe testy (wzorce w istniejących plikach):

1. **Klasyfikacja** (`tests/test_classifier.py`): dokument nowej klasy jest rozpoznawany; dokumenty pozostałych klas
   i negatywy nadal dają dotychczasowe wyniki; brak konfliktu przy dokumentach podobnych.
2. **Ekstrakcja** (styl `tests/test_extract.py`): `FieldExtractor().extract("<klasa>", [tekst])` na tekstach pisanych od zera —
   wartości pól, `evidence`, `validation_errors == []`, zachowanie przy braku pól (same `null`, ostrzeżenia).
3. **Normalizacja** (styl `tests/test_extract_normalizers.py`): nowe lub zmienione normalizatory.
4. **End-to-end** (styl `tests/test_pipeline.py`): PDF utworzony przez `make_pdf` z `tests/conftest.py` →
   `Pipeline(ocr_engine=None).process_file` → `write_outputs` → plik JSON zgodny z szablonem.
5. **OCR** (opcjonalnie, styl `tests/test_ocr.py`): skan utworzony przez `make_scan_pdf`.

## 9. Jak zweryfikować nową klasę

```bash
# 1. Testy jednostkowe i end-to-end
.venv/bin/python -m pytest

# 2. Pojedynczy dokument: klasa, powód, sygnały
.venv/bin/python -m odwolania2json classify sciezka/dokument.pdf
.venv/bin/python -m odwolania2json run sciezka/dokument.pdf --output /tmp/wyniki --diagnostics /tmp/diag

# 3. Regresja na danych organizatora (pełny zbiór; ścieżka do lokalnej kopii repozytorium organizatora)
ODWOLANIA_DANE=/sciezka/hacknation-2026/dane .venv/bin/python -m pytest

# 4. Evaluator przed i po zmianie + porównanie (brak regresji na istniejących klasach i negatywach)
.venv/bin/python -m odwolania2json eval --manifest datasets/manifests/fixtures.jsonl --report reports/przed.json
.venv/bin/python -m odwolania2json eval --manifest datasets/manifests/fixtures.jsonl --report reports/po.json
.venv/bin/python -m odwolania2json eval-compare reports/przed.json reports/po.json --fail-on-regression
```

Manifest dla pełnego zbioru organizatora generuje `python -m odwolania2json manifest-organizer --dane …/dane --out …`;
opis zbiorów: `datasets/BENCHMARK.md`. Dla nowej klasy przygotuj własny manifest JSONL z dokumentami i plikami
referencyjnymi (`{"pdf": …, "label": "<klasa>", "gt": …}`) — wtedy `eval` policzy także dokładność pól.

Weryfikacja w GUI: `python -m odwolania2json.web --open` → dodaj PDF → „Przetwórz” → szczegóły dokumentu (typ, JSON, diagnostyka).

## 10. Co można w przyszłości przenieść do konfiguracji

Elementy, które dziś wymagają kodu, a mogłyby być konfiguracją (szczegóły wariantów: `ARCHITEKTURA.md`, sekcja 19):

| Element | Dziś (kod) | Możliwa konfiguracja |
|---|---|---|
| powiązanie klasy z ekstraktorem | słownik `EXTRACTORS` | wpis w TOML: nazwa ekstraktora albo „generyczny” |
| pola „etykieta → wartość” | funkcje `extract_*` | lista etykiet, typ wartości, okno, normalizator i ścieżka JSON per pole (jak `[przelew.labels]`, ale dla dowolnej klasy) |
| wybór normalizatora pola | wywołania w ekstraktorach | nazwa normalizatora w konfiguracji pola (rejestr funkcji z `normalizers.py`) |
| reguły biznesowe walidacji | normalizatory | długość / wzorzec / suma kontrolna w deklaracji pola |
| etykiety klas w GUI | `LABELS` w `app.js` | `description` z `[classes.*]` (już dostępne w `GET /api/info`) |
| progi jakości tekstu | `MIN_CHARS_DOCUMENT`, `MIN_CHARS_PAGE` w `pdf_text.py` | klucze w `config/app.toml` |

Struktury złożone (listy podmiotów, konsorcja, kilku pełnomocników) będą nadal wymagały dedykowanego kodu albo
rozbudowanego schematu konfiguracji — generyczny ekstraktor najlepiej sprawdzi się dla dokumentów „płaskich”.
