# Odwołania2JSON — design system interfejsu

Cel: narzędzie urzędowe do pracy z dokumentami — czytelne, spokojne, wiarygodne. Inspiracja: serwisy administracji
publicznej (gov.pl, UZP) — białe tło, grafitowy tekst, czerwony akcent, cienkie linie, brak dekoracji.
Nie kopiujemy żadnego serwisu; nie używamy godła, logotypów ani znaków gov.pl.

**Nie stosujemy:** ciemnych motywów, gradientów, fioletu/neonów, glassmorphism, poświaty, dużych zaokrągleń,
kart dekoracyjnych, wielkich liczb-statystyk, wykresów bez funkcji.

Źródło prawdy: `odwolania2json/web/static/css/tokens.css` (tokeny) i `app.css` (komponenty).

## Kolory

| Token | Wartość | Zastosowanie |
|---|---|---|
| `--color-bg` | `#ffffff` | tło strony i paneli |
| `--color-bg-subtle` | `#f5f5f5` | nawigacja boczna, nagłówki tabel, bloki kodu |
| `--color-text` | `#1b1b1b` | tekst główny (grafit) |
| `--color-text-muted` | `#5c5c5c` | tekst pomocniczy (kontrast 6,7:1) |
| `--color-border` / `--color-border-strong` | `#d6d6d6` / `#a8a8a8` | obramowania |
| `--color-primary` | `#c8203a` | akcent: aktywna nawigacja, przycisk główny, podkreślenie nazwy (kontrast 5,5:1) |
| `--color-primary-hover` | `#9f1239` | stan hover akcentu |
| `--color-link` / `--color-info` | `#0052a5` | linki, komunikaty informacyjne |
| `--color-success` / `warning` / `error` | `#1e6b3a` / `#8a5a00` / `#b3261e` | statusy (zawsze z tekstem, nie samym kolorem) |

## Typografia

Krój: „Open Sans”, jeśli zainstalowany w systemie, w przeciwnym razie krój systemowy (Segoe UI, system-ui, Helvetica).
Bez pobierania czcionek — interfejs działa offline.

| Rola | Rozmiar | Grubość |
|---|---|---|
| Tytuł strony (h1) | 30 px (24 px na telefonie) | 700 |
| Nagłówek sekcji (h2) | 24 px | 700 |
| Tytuł panelu | 18 px | 600 |
| Tekst | 16 px, interlinia 1,55 | 400 |
| Tekst pomocniczy, tabele | 14 px | 400 |
| Znaczniki statusu | 13 px | 600 |

## Odstępy, kształt, układ

- Skala odstępów 4 px: 4, 8, 12, 16, 24, 32, 48.
- Zaokrąglenia: 2 px (przyciski, znaczniki), 4 px (panele). Obramowania 1 px; belka akcentu 4 px.
- Nagłówek aplikacji ~72 px; nawigacja boczna 256 px; szerokość treści maks. 1088 px.
- Punkty przełamania: 960 px (siatka dwukolumnowa → jedna kolumna), 760 px (nawigacja boczna → menu rozwijane).

## Komponenty

| Komponent | Klasa | Uwagi |
|---|---|---|
| Nagłówek aplikacji | `.app-header`, `.brand` | nazwa z czerwonym podkreśleniem, podpis, informacja „Praca lokalna” |
| Nawigacja boczna | `.sidenav`, `.sidenav__link` | aktywna pozycja: `aria-current="page"`, czerwona belka z lewej, tło białe |
| Nagłówek strony | `.page-header` | etykieta sekcji, tytuł h1, krótki opis |
| Panel | `.panel`, `.panel__header`, `.panel__body` | biały, obramowanie 1 px; jedyny „kontener” treści |
| Tabela klucz–wartość | `.kv` | dane systemu, metadane dokumentu |
| Tabela | `.table` | listy (dokumenty, wyniki) |
| Znacznik statusu | `.badge--neutral/success/warning/error/info` | kropka + tekst; stonowane tło |
| Przycisk | `.btn--primary` (czerwony), `.btn--secondary` (obrys) | min. wysokość 44 px |
| Komunikat | `.notice`, `.notice--warning` | belka z lewej, jasne tło |
| Stan pusty | `.empty` | przerywane obramowanie, opis tego, co się pojawi |
| Lista kroków | `.steps` | numeracja w okręgach z czerwonym obrysem |
| Blok kodu | `.code` | podgląd JSON |
| Stopka | `.app-footer` | informacja o przetwarzaniu lokalnym |

## Dostępność

- Język strony `pl`, odnośnik „Przejdź do treści”, punkty orientacyjne (`header`, `nav`, `main`, `footer`).
- Widoczny fokus klawiatury (3 px, niebieski); po zmianie widoku fokus trafia na tytuł strony.
- Kontrast tekstu ≥ 4,5:1; statusy nie są przekazywane samym kolorem.
- Ikony liniowe (SVG, `aria-hidden`), zawsze obok tekstu.
