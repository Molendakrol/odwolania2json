# Szablony wyjściowe

Struktura kluczy i ich kolejność odpowiadają plikom organizatora
(`oczekiwane-json/*.json` w repozytorium hacknation-2026). Zamiast przykładowych wartości
każdy liść zawiera deklarację typu:

| Typ | Znaczenie |
|---|---|
| `string\|null` | tekst albo null |
| `date\|null` | tekst w formacie ISO `YYYY-MM-DD` albo null |
| `int\|null` | liczba całkowita albo null |
| `number\|null` | liczba (float) albo null |

Tablica z jednym elementem oznacza listę obiektów o tej strukturze. Gdy ekstraktor nie znajdzie
żadnego elementu, wynik zawiera jeden element wypełniony wartościami null (zachowanie kształtu szablonu).

Decyzje kontraktowe nierozstrzygnięte przez organizatora:
- `pelnomocnicy[].pesel` — `string|null` (tak jest w danych treningowych organizatora; w przykładzie schematu jest liczba).
  Zmiana na `int|null` przełącza typ wyjścia bez zmian w kodzie.
- Nazwa pliku szablonu = nazwa klasy dokumentu z klasyfikatora.
