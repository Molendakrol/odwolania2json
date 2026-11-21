# Komponenty zewnętrzne i licencje

Stan na 21.11.2026 (wersje z `.venv` użytego do testów). Licencje odczytano z metadanych pakietów
(`importlib.metadata`); tam, gdzie metadane nie zawierają licencji, zaznaczono to wprost.

## Zależności uruchomieniowe (`requirements.txt`)

| Komponent | Wersja | Licencja | Uwagi |
|---|---|---|---|
| PyMuPDF (`pymupdf`) | 2.1 | **GNU AGPL-3.0** lub licencja komercyjna Artifex | ekstrakcja tekstu i OCR; jedyna bezpośrednia zależność |
| Tesseract (wbudowany w MuPDF/PyMuPDF) | — | Apache-2.0 | silnik OCR |
| `ocr/tessdata/pol.traineddata` (tessdata_best) | — | Apache-2.0 | model języka polskiego, dołączony do repozytorium |
| `pymupdf4llm` | 2.1 | **brak informacji w metadanych** | instalowany automatycznie jako zależność PyMuPDF 2.x; projekt go nie używa |
| `pymupdf_layout` | 2.1 | **brak informacji w metadanych** | jw. — licencję należy zweryfikować u wydawcy (Artifex) |
| onnxruntime | 1.32.0 | MIT | zależność przechodnia PyMuPDF 2.x; nieużywana przez projekt |
| numpy | 2.5.5 | BSD-3-Clause (i licencje składowe) | zależność przechodnia |
| networkx | 3.7 | BSD-3-Clause | zależność przechodnia |
| psutil | 7.2.2 | BSD-3-Clause | zależność przechodnia |
| PyYAML | 6.0.4 | MIT | zależność przechodnia |
| tabulate | 0.10.0 | MIT | zależność przechodnia |
| protobuf | 7.36.2 | BSD-3-Clause | zależność przechodnia |
| flatbuffers | 25.12.19 | Apache-2.0 | zależność przechodnia |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | zależność przechodnia |

## Zależności deweloperskie (`requirements-dev.txt`)

| Komponent | Licencja |
|---|---|
| pytest (+ iniconfig, pluggy, pygments) | MIT / MIT / MIT / BSD-2-Clause |

## Uwagi licencyjne (do decyzji zespołu)

1. **AGPL-3.0 (PyMuPDF).** To licencja open source (zgodna z wymogiem „wyłącznie komponenty open source”),
   ale copyleft: dystrybucja lub udostępnianie aplikacji przez sieć wymaga udostępnienia kodu źródłowego
   na zasadach AGPL. Ma to znaczenie przy ewentualnym przeniesieniu praw do UZP i wdrożeniu.
2. **`pymupdf4llm` / `pymupdf_layout`** — licencja nieustalona na podstawie metadanych; nie są używane przez kod,
   ale instalują się z PyMuPDF 2.x. Alternatywa: PyMuPDF 1.x (bez tych zależności) — patrz raport finalizacji.
3. **Licencja własnego kodu projektu nie została wybrana** (brak pliku `LICENSE`). Decyzja należy do zespołu;
   regulamin zadania przewiduje przeniesienie autorskich praw majątkowych na UZP w przypadku wygranej.
4. Dane w `tests/fixtures/` pochodzą z publicznego repozytorium organizatora (Urzad-Zamowien-Publicznych/hacknation-2026).
