# Benchmarki (metryki wewnętrzne)

| Zbiór | Manifest | Dokumenty | Rola |
|---|---|---|---|
| Oficjalny (60 spraw + negatywy) | generowany: `manifest-organizer --dane …/dane` (pomija `dane/skany`); kopia lokalna: `manifests/organizer_official_local.jsonl` | 196 PDF tekstowych | **regresja** — reguły tworzone na tych danych; nie mierzy generalizacji |
| Skany organizatora — DEVELOPMENT | `manifests/skany_dev.jsonl` | 15 skanów (5 / klasę) | analiza błędów i poprawki ekstraktorów |
| Skany organizatora — HOLDOUT | `manifests/skany_holdout.jsonl` | 15 skanów (5 / klasę) | **zamknięty** — uruchamiany raz po zakończeniu poprawek; bez analizy szczegółowych błędów i bez strojenia |
| Negatywy zewnętrzne | `manifests/external_negatives.jsonl` | 10 PDF (7 orzeczeń KIO, pismo zamawiającego, 2 dokumenty bankowe) | fałszywe pozytywy; źródła w `external/SOURCES.md` |
| Do weryfikacji | `manifests/external_needs_review.jsonl` | 1 (broszura UZP + pusty wzór odwołania) | poza metrykami |
| Fikstury repozytorium | `manifests/fixtures.jsonl` | 8 | szybki test |

## Podział skanów organizatora (`dane/skany/`, dodane przez organizatora 21.11.2026)

Reguła (deterministyczna, ustalona przed jakąkolwiek zmianą kodu w P10): w każdej klasie do DEVELOPMENT trafiają
dokumenty, których tekst OCR był oglądany szczegółowo podczas analizy P09, a resztę do 5 uzupełniają najniższe numery;
pozostałe 5 to HOLDOUT.

| Klasa | DEVELOPMENT | HOLDOUT |
|---|---|---|
| odwolania | 0001, 0002, 0003, 0004, 0005 | 0006, 0007, 0008, 0009, 0010 |
| pelnomocnictwa | 0001, 0002, 0003, 0004, 0006 | 0005, 0007, 0008, 0009, 0010 |
| potwierdzenia | 0001, 0002, 0003, 0004, 0008 | 0005, 0006, 0007, 0009, 0010 |

**Zastrzeżenie o czystości HOLDOUT:** w raporcie P09 (przed podziałem) widoczne były zbiorcze listy rozbieżności
dla wszystkich 30 skanów (pierwsze przykłady dla każdego pola), więc HOLDOUT nie jest w pełni „dziewiczy”.
Tekstu OCR dokumentów HOLDOUT nie oglądano szczegółowo.

Manifesty skanów zawierają ścieżki bezwzględne do lokalnej kopii repozytorium organizatora — na innej maszynie
trzeba je wygenerować ponownie (ta sama lista plików).
