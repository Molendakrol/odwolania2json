# Odwołania2JSON

**Automatyczna transformacja dokumentów spraw odwoławczych KIO do ustrukturyzowanego JSON** — rozwiązanie zadania
Urzędu Zamówień Publicznych w ramach HackNation 2026.

## Problem

Sprawa odwoławcza przed Krajową Izbą Odwoławczą to zbiór plików PDF o różnej strukturze — tekstowych i skanowanych:
treść odwołania, pełnomocnictwo, potwierdzenie przelewu, a często także dokumenty niezwiązane z tymi kategoriami.
Aplikacja rozpoznaje rodzaj każdego dokumentu i wyodrębnia z niego dane do JSON zgodnego z szablonami organizatora.

## Jak działa

```
PDF ─► ekstrakcja tekstu (warstwa tekstowa PDF; OCR tylko dla stron bez tekstu)
    ─► normalizacja tekstu
    ─► klasyfikacja: odwolanie | pelnomocnictwo | przelew | unknown
    ─► ekstraktor właściwej klasy ─► normalizacja wartości ─► szablon JSON (brak danych = null) ─► walidacja
    ─► output/<sprawa>/<nazwa_pliku>.json   (+ diagnostyka w diagnostics/)
```

- **Działa lokalnie na CPU**, bez GPU, bez LLM i bez zewnętrznych API; dokumenty nie opuszczają komputera.
- **Deterministyczny**: te same dane wejściowe dają zawsze ten sam wynik.
- **Transparentny**: dla każdego dokumentu w `diagnostics/` zapisywane są sygnały klasyfikacji, źródło tekstu
  (native/ocr), status OCR oraz fragmenty tekstu, z których pochodzą wartości pól.

### Obsługiwane typy dokumentów

| Klasa | Dokument | Szablon wyniku |
|---|---|---|
| `odwolanie` | odwołanie do Prezesa KIO | `config/templates/odwolanie.json` |
| `pelnomocnictwo` | pełnomocnictwo (procesowe) | `config/templates/pelnomocnictwo.json` |
| `przelew` | potwierdzenie wykonania przelewu | `config/templates/przelew.json` |
| `unknown` | każdy inny dokument (orzeczenia KIO, ogłoszenia BZP/TED, pisma, nieczytelne PDF) | `{}` |

Klucze i kolejność kluczy w szablonach odpowiadają plikom organizatora (`oczekiwane-json/`).

### Klasyfikacja

Reguły w `config/classifier_rules.toml`: każda klasa ma ważone sygnały (np. adresat „Prezes KIO”, tytuł „ODWOŁANIE”,
formuła udzielenia pełnomocnictwa, numery rachunków). Klasa wygrywa tylko przy łącznym spełnieniu progu punktów,
minimalnej liczby różnych sygnałów i sygnału obowiązkowego — pojedyncze słowo nie wystarcza. Weta wymuszają `unknown`
dla orzeczeń KIO, ogłoszeń BZP/TED i zgłoszeń przystąpienia; sprzeczne sygnały dają `unknown`.
Nową klasę dodaje się sekcją w pliku TOML (bez zmian w kodzie klasyfikatora).

### Ekstrakcja

Deterministyczne reguły per klasa (`odwolania2json/extract/`): etykiety pól, sekcje dokumentu (bloki „Odwołujący”,
„Zamawiający”, „dotyczy”), wyliczenia („wnoszę odwołanie wobec: 1. …”), kontekst wokół wartości. Słowniki (etykiety banków,
kategorie czynności 1–12) w `config/extraction.toml`. Normalizacja: daty ISO, kwoty jako liczby, rachunki jako 26 cyfr,
numery TED `NNNNNN-YYYY`, BZP bez spacji. Gdy wartości nie da się wiarygodnie ustalić — `null`.
Każdy wynik jest walidowany względem szablonu przed zapisem; wynik niepoprawny nie jest zapisywany.

### OCR

- Silnik: Tesseract wbudowany w PyMuPDF — **bez instalacji programu `tesseract`**.
- Model języka polskiego: `ocr/tessdata/pol.traineddata` (dołączony; źródło i licencja w `ocr/tessdata/README.md`).
- OCR uruchamia się tylko dla stron bez warstwy tekstowej (skany) lub gdy tekstu jest za mało; dokumenty tekstowe
  nie są OCR-owane. Tekst z OCR przechodzi przez ten sam klasyfikator i te same ekstraktory.
- Kontekstowe korekty typowych błędów OCR (np. „Sp. z 0.0.” → „Sp. z o.o.”, „@” odczytane jako „G” w adresie e-mail).
- Konfiguracja: `config/app.toml` → `[ocr]`; wyłączenie: `--no-ocr`.

## Wymagania

- Python **3.13+**
- System: Windows, Linux lub macOS; CPU (bez GPU); ok. 300 MB miejsca na środowisko
- Internet potrzebny tylko do instalacji pakietów (`pip`); samo przetwarzanie działa offline

## Instalacja

### Windows (PowerShell / cmd)

```bat
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### Linux / macOS

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Do uruchamiania testów zainstaluj `requirements-dev.txt` zamiast `requirements.txt`.

## Uruchomienie

Umieść sprawy w katalogu `input` (`input/<sprawa>/<dokument>.pdf`) i uruchom:

| System | Polecenie |
|---|---|
| Windows | `start.bat` |
| Linux / macOS | `./start.sh` |
| dowolny | `python -m odwolania2json run` (z aktywnym `.venv`) |

Wyniki: `output/<sprawa>/<nazwa_pliku>.json` — nazwa jak plik wejściowy. Diagnostyka: `diagnostics/`.
Ponowne uruchomienie na tym samym katalogu `output` wymaga `--overwrite` (ochrona przed przypadkowym nadpisaniem):
`start.bat --overwrite` / `./start.sh --overwrite`.

### Interfejs graficzny

Najprościej: `start_gui.bat` (Windows) lub `./start_gui.sh` (Linux/macOS) — uruchamia serwer i otwiera aplikację
w przeglądarce. Ręcznie:

```bash
python -m odwolania2json.web            # http://127.0.0.1:8000  (opcje: --port 8080, --open)
python -m odwolania2json.web --data-dir /sciezka/magazynu --max-upload-mb 100
```

Lokalny serwer (biblioteka standardowa Pythona, bez dodatkowych zależności i zasobów z internetu), dostępny tylko
z tego komputera. Funkcje: dodawanie dokumentów PDF (przeciągnij i upuść lub „Wybierz pliki”), lista dokumentów,
usuwanie oraz przetwarzanie wybranych dokumentów (zaznaczenie + „Przetwórz zaznaczone” albo „Przetwórz” przy
dokumencie). Lista pokazuje rzeczywisty status (oczekuje / w kolejce / przetwarzanie z czasem trwania /
przetworzono / błąd), rozpoznany typ dokumentu i informację o użyciu OCR. Zasady wyglądu: `docs/ui/DESIGN_SYSTEM.md`.

Przetwarzanie korzysta z tego samego pipeline'u co CLI (`Pipeline.process_file`, `write_outputs`) — wynik JSON jest
identyczny jak z `odwolania2json run`. Dokumenty są przetwarzane kolejno w osobnym procesie roboczym z limitem czasu
(60 s + 45 s na stronę); awaria lub przekroczenie czasu dotyczy tylko jednego dokumentu, a serwer działa dalej.
Wyniki zapisywane są obok dokumentu: `<id>/result/output.json` (format organizatora) i `diagnostics.json`
(klasyfikacja, OCR, pola z uzasadnieniami — bez pełnego tekstu). Plik źródłowy jest tylko do odczytu i nie jest
zmieniany. Po ponownym uruchomieniu serwera przerwane przetwarzanie wraca do statusu „oczekuje”.

Lista dokumentów rozróżnia statusy: Oczekuje / W kolejce / Przetwarzanie / Gotowe / Do weryfikacji (brakujące pola) /
Nierozpoznany / Błąd; „Przetwórz oczekujące” uruchamia wszystkie nowe dokumenty jednym kliknięciem, a pasek postępu
pokazuje liczbę ukończonych dokumentów partii. Przycisk „Wynik” otwiera szczegóły dokumentu (adres `#/dokumenty/<id>`):
na górze „Pobierz JSON” (plik `<nazwa PDF>.json`) i „Kopiuj JSON”, zakładka „Dane” (czytelna tabela pól ze źródłem
każdej wartości i liczbą uzupełnionych pól), zakładka „JSON” (dokładna treść zapisanego pliku z numerami wierszy)
oraz zwijana diagnostyka (powód klasyfikacji, punkty i sygnały, OCR, ostrzeżenia, walidacja). Widok „Wyniki i eksport”
zawiera listę wszystkich zapisanych plików JSON do pobrania.

Dodane dokumenty są przechowywane lokalnie **poza katalogiem projektu** (nie trafiają do Git):
macOS `~/Library/Application Support/Odwolania2JSON/documents`, Linux `~/.local/share/Odwolania2JSON/documents`
(lub `$XDG_DATA_HOME`), Windows `%LOCALAPPDATA%\Odwolania2JSON\documents`. Zmiana: `--data-dir` lub zmienna
`ODWOLANIA_DATA_DIR`. Limit pojedynczego pliku: 50 MB (`--max-upload-mb`). Serwer odrzuca pliki bez rozszerzenia
`.pdf`, puste, bez nagłówka PDF, uszkodzone i zabezpieczone hasłem.

API (JSON): `GET /api/info`, `GET /api/documents`, `POST /api/documents` (surowe ciało, nazwa w nagłówku
`X-Filename`), `DELETE /api/documents/{id}` (409 w trakcie przetwarzania), `POST /api/documents/process`
(`{"ids": [...]}` → 202), `GET /api/documents/{id}/result` (diagnostyka + wygenerowany JSON),
`GET /api/documents/{id}/output.json` (pobranie zapisanego pliku wyniku). Żądania zmieniające stan wymagają nagłówka
`X-Requested-With: odwolania2json` i hosta `127.0.0.1`/`localhost`.

### Docker

```bash
docker compose up --build
```

Katalog `./input` jest montowany tylko do odczytu, wyniki trafiają do `./output` i `./diagnostics`.
Kontener działa bez dostępu do sieci (`network_mode: none`); model OCR jest w obrazie.

### Przykłady CLI

```bash
python -m odwolania2json run                                       # ./input -> ./output
python -m odwolania2json run sciezka/do/sprawy --output wyniki --diagnostics diag
python -m odwolania2json run dokument.pdf --output wyniki          # pojedynczy plik
python -m odwolania2json run --no-ocr                              # bez OCR
python -m odwolania2json classify input/case1/plik.pdf             # tylko klasyfikacja
```

Kody wyjścia `run`: 0 — sukces, 1 — brak PDF, 2 — błąd argumentów/konfiguracji, 3 — wyniki już istnieją
(bez `--overwrite`), 4 — część wyników odrzucona przez walidację.

### Przykładowy wynik

`output/case1/potwierdzenie_przelewu.json` (dane syntetyczne z repozytorium organizatora):

```json
{
  "przelew_bankowy": {
    "data_operacji": "2026-03-21",
    "kwota": 200.0,
    "na_rachunek": "68438223049992295262834736",
    "numer_transakcji": "CEN2604016344389",
    "tytul": "opłata skarbowa od pełnomocnictwa dla Aleksandra Marciniak i Jolanta Zajkowski w postępowaniu przed KIO w przetargu ZS.791.2026 CEN2604016344389",
    "z_rachunku": "05470680287581539721455024"
  }
}
```

Dokument nierozpoznany (`unknown`) daje plik z zawartością `{}`.

## Testy

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                       # ok. 290 testów, w tym OCR na prawdziwym skanie
ODWOLANIA_DANE=/sciezka/hacknation-2026/dane .venv/bin/python -m pytest   # + regresja na pełnym zbiorze organizatora
```

Evaluator (metryki wewnętrzne — **nie** są oficjalnym scoringiem organizatora):

```bash
python -m odwolania2json eval --manifest datasets/manifests/fixtures.jsonl --report reports/run.json
python -m odwolania2json eval-compare reports/a.json reports/b.json --fail-on-regression
```

Opis zbiorów testowych: `datasets/BENCHMARK.md`.

## Wyniki ewaluacji i ich interpretacja

| Zbiór | Charakter | Klasyfikacja | Ekstrakcja (accuracy pól) |
|---|---|---|---|
| 60 spraw organizatora + 16 negatywów (196 PDF tekstowych) | **regresja** — reguły tworzone na tych danych | 196/196 | 2316/2316 |
| 15 skanów organizatora — DEVELOPMENT | dane używane do poprawek | 15/15 | 0,948 |
| 15 skanów organizatora — HOLDOUT | jedna kontrola po poprawkach | 15/15 | **0,821** |
| 10 publicznych negatywów (orzeczenia KIO, dokumenty bankowe, pismo zamawiającego) | niezależne | 0 fałszywych pozytywów | — |

Interpretacja:
- Wynik 100% na danych organizatora **nie jest dowodem generalizacji** — reguły powstawały na tych dokumentach.
- Najbardziej miarodajny jest HOLDOUT (0,82 accuracy pól na nowych szablonach i skanach; przed poprawkami 0,66).
  Zbiór jest mały (15 dokumentów) i nie był w pełni „dziewiczy” (zbiorcze listy błędów były widoczne przed podziałem).
- Metryki są wewnętrzne: sposób liczenia F1 przez organizatora nie jest znany.

Czas przetwarzania (Apple Silicon): dokument tekstowy — ułamek sekundy; skan — ok. 13 s (ok. 6 s na stronę).
Na laptopie z procesorem Intel i7 czas OCR może być dłuższy.

## Ograniczenia

- Błędy OCR w nazwach i liczbach (np. zlepione słowa, „Aśterion” zamiast „Asterion”, rok 2020 zamiast 2026) przechodzą do wyników.
- Imiona i nazwiska w przypadkach zależnych (np. „Annę Kowal”) nie są odmieniane do mianownika.
- Dokumenty mieszane (kilka dokumentów w jednym PDF) są klasyfikowane jako całość.
- Konwencje nierozstrzygnięte przez organizatora: format wyniku dla `unknown` (`{}` — decyzja zespołu, alternatywa
  w `config/app.toml`), typ PESEL (tekst), prefiks „PL” w numerach rachunków (dane organizatora są niespójne).
- Ochrona przed orzeczeniami KIO opiera się w dużej mierze na wecie rozpoznającym nagłówek orzeczenia.

## Struktura projektu

```
odwolania2json/        kod: pdf_text, ocr, normalize, classify/, extract/, pipeline, output, report, cli, evaluation, benchmark
config/                reguły klasyfikacji, słowniki ekstrakcji, szablony JSON, konfiguracja aplikacji
ocr/tessdata/          model OCR języka polskiego
tests/                 testy (pytest) i fikstury z repozytorium organizatora
datasets/              opis benchmarków, manifesty, źródła dokumentów zewnętrznych
input/  output/        katalogi wejścia i wyników
```

Licencje komponentów: `THIRD_PARTY_NOTICES.md`.
