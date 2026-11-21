"""Ekstrakcja pól z pełnomocnictwa: mocodawcy (także konsorcja i osoby fizyczne), podpisujący, pełnomocnicy."""

from __future__ import annotations

import re

from .base import ExtractionContext, FieldResult
from .entities import LEGAL_FORM, clean_name, id_clusters, name_before
from .normalizers import NAME_WORD, PERSON_RE, TITLE_RE, collapse_ws

_DOTS = re.compile(r"^[.…_·\s]{5,}$")
_FULL_NAME_LINE = re.compile(
    rf"^(?:(?:r\.\s?pr\.|adw\.|radca\s+prawny|adwokat|mec\.)\s*)?({NAME_WORD}(?:\s+{NAME_WORD}){{1,2}})\s*,?$"
)
# Imię i nazwisko po tytule; przeniesienie linii dozwolone między imieniem a nazwiskiem („Jolanta\nZajkowski”),
# trzecie słowo (nazwisko złożone / drugie imię) tylko w tej samej linii — inaczej łączyłoby się z następną linią.
_TITLED_PERSON = re.compile(
    rf"(?:r\.\s?pr\.|adw\.|mec\.|radc\w*\s+prawn\w*|adwokat\w*|apl\.\s?\w+\.?)\s*({NAME_WORD}\s+{NAME_WORD}(?:[ \t]+{NAME_WORD})?)"
)
_ROLE_WORDS = {
    "prezes", "wiceprezes", "członek", "zarządu", "prokurent", "wspólnik", "dyrektor", "pełnomocnik", "komplementariusz",
    "właściciel", "partner", "kierownik", "główny", "księgowy", "przewodniczący", "rady", "nadzorczej", "generalny",
    "mocodawca", "pełnomocnictwo", "przyjmuję", "miejsce", "pieczęć", "podpis", "data", "zamawiający", "postępowanie",
    "krajowej", "krajową", "krajowa", "izby", "izbą", "izba", "odwoławczej", "odwoławczą", "odwoławcza", "prezesa",
    "za", "w", "na", "do", "dla", "skarbu", "państwa", "sądu", "sąd", "zamówień", "publicznych", "wykonawcy", "wykonawca", "spółki", "spółka",
}
_GRANT_START = re.compile(
    r"(ustanawia\w*\s+pełnomocnik\w*|udziela\w*[\s:.,;]+(?:dalszego\s+)?pełnomocnictwa|upoważniam\w*|"
    r"(?:^|\n)\s*(?:§\s*\d+\.?\s*)?pełnomocni(?:k|cy)\s*:?\s*(?:\n|$))",
    re.IGNORECASE,
)
_GRANT_END = re.compile(
    r"(do\s+reprezentowania|\(dalej|pełnomocnictwo\s+udzielone|pełnomocnictwo\s+dotyczy|§\s*3|\n\s*zakres|"
    r"\n\s*postępowanie|\n\s*zamawiający|w\s+postępowaniu)",
    re.IGNORECASE,
)
# Osoba wymieniona w osobnej linii listy pełnomocników: „1. Jan Kowalski — adwokat, …”, „Anna Nowak (radca …)”.
_ENUM_PREFIX = re.compile(r"^(?:\d{1,2}|[IVXl]{1,4})\s*[.)]\s*")
_LINE_PERSON = re.compile(rf"^({NAME_WORD}\s+{NAME_WORD})(?=\s*(?:$|[—–\-,(]))")
# Reprezentanci mocodawcy z funkcją: „reprezentowana przez: Jan Kowalski — Prezes Zarządu, Anna Nowak — Członek Zarządu”.
_ROLE = r"(?:Prezes|Wiceprezes|Członek|Prokurent|Wspólnik|Dyrektor|Komplementariusz|Właściciel|Pełnomocnik|Partner|Likwidator)"
_REPRESENTED = re.compile(r"reprezentowan\w*\s+przez\s*:?\s*(.{0,300}?)(?:\(dalej|niniejszym|udziela|$)", re.IGNORECASE)
_PERSON_WITH_ROLE = re.compile(rf"({NAME_WORD}\s+{NAME_WORD})\s*[-—–]+\s*{_ROLE}")
_FOR_PRINCIPAL = re.compile(rf"Za\s+Mocodawc\w*\s*:\s*(.{{0,200}})")
_E_SIGNED = re.compile(rf"podpisan\w*\s*przez\s+({NAME_WORD}\s+{NAME_WORD})")
_PUNCT_ONLY = re.compile(r"^[^\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+$")
_PESEL = re.compile(r"PESEL\s*:?\s*(\d{11})", re.IGNORECASE)
_SOLE_TRADER = re.compile(r"działalnoś\w*\s+gospodarcz\w*|CEIDG", re.IGNORECASE)
_TRADE_NAME = re.compile(r"pod\s+firmą\s+(.+?)(?:,\s*(?:ul\.|al\.|z\s+siedzibą|NIP|adres)|\s+ul\.)", re.IGNORECASE)
_SIGNED_PERSON = re.compile(rf"niżej\s+podpisan\w*(?:\(a\))?\s+({NAME_WORD}\s+{NAME_WORD})")


def _is_person_line(line: str) -> str | None:
    m = _FULL_NAME_LINE.match(line.strip())
    if not m:
        return None
    words = {w.lower() for w in re.split(r"[\s-]+", m.group(1))}
    return None if words & _ROLE_WORDS else m.group(1)


def _mocodawcy(flat: str, res: FieldResult) -> list[dict]:
    entities: list[dict] = []
    seen: set[str] = set()
    floor = 0
    for start, end, ids in id_clusters(flat):
        key = ids.get("NIP") or ids.get("KRS") or ids.get("REGON")
        if key in seen:
            floor = end
            continue
        seen.add(key)
        context = flat[max(0, start - 500): end + 200]
        person = None
        trade = None
        if _SOLE_TRADER.search(context) and not re.search(LEGAL_FORM, context[: start - max(0, start - 500)], re.IGNORECASE):
            if m := _TRADE_NAME.search(context):
                trade = collapse_ws(m.group(1))
            if m := _SIGNED_PERSON.search(context):
                person = m.group(1)
        name = trade or name_before(flat, start, floor)
        entities.append({
            "data": {
                "imie_nazwisko": person,
                "krs": ids.get("KRS"),
                "nazwa": name,
                "nip": ids.get("NIP"),
                "typ": "osoba_fizyczna" if (trade or person) else "osoba_prawna",
            },
            "podpisujacy": [],
        })
        res.evidence[f"mocodawca[{len(entities) - 1}]"] = collapse_ws(flat[max(start - 120, 0): end])
        floor = end
    return entities


def _signatures(lines: list[str]) -> tuple[list[tuple[str | None, str]], list[str]]:
    """Zwraca (podpisy mocodawców jako (sekcja 'W imieniu X', osoba), podpisy przyjmujących pełnomocnictwo)."""
    principal: list[tuple[str | None, str]] = []
    accepted: list[str] = []
    section: str | None = None
    accepting = False
    for i, line in enumerate(lines):
        low = line.lower()
        if low.startswith("pełnomocnictwo przyjmuję"):
            accepting = True
            continue
        if low.startswith("mocodawca") and low.rstrip(":").strip() == "mocodawca":
            accepting = False
            continue
        if m := re.match(r"^(?:w\s+imieniu|za)\s+(.+?):?$", line, re.IGNORECASE):
            section = clean_name(m.group(1))
            accepting = False
            continue
        if not _DOTS.match(line):
            continue
        for nxt in lines[i + 1: i + 4]:
            if re.match(r"^(?:w\s+imieniu|za)\s+", nxt, re.IGNORECASE):
                section = clean_name(re.sub(r"^(?:w\s+imieniu|za)\s+", "", nxt, flags=re.IGNORECASE).rstrip(":"))
                continue
            person = _is_person_line(nxt)
            if person:
                titled = bool(re.match(r"^(r\.\s?pr\.|adw\.|radca|adwokat|mec\.)", nxt.strip(), re.IGNORECASE))
                if accepting or titled:
                    accepted.append(person)
                else:
                    principal.append((section, person))
                break
            if _DOTS.match(nxt):
                break
    return principal, accepted


def _pelnomocnicy(text: str, accepted: list[str]) -> list[str]:
    names: list[str] = []
    for m in _GRANT_START.finditer(text):
        region = text[m.start(): m.start() + 700]
        if end := _GRANT_END.search(region, len(m.group(0))):
            region = region[: end.start()]
        flat_region = re.sub(r"\s+", " ", region)
        titled = [collapse_ws(p.group(1)) for p in _TITLED_PERSON.finditer(region)]
        listed = _segment_persons(region[len(m.group(0)):])
        # Uzupełnianie zamiast zastępowania: osoby z tytułem + osoby z listy, bez duplikatów, w kolejności w dokumencie.
        found = sorted(dict.fromkeys(titled + listed), key=lambda n: (flat_region.find(n) % (len(flat_region) + 1)))
        if not found:
            cleaned = TITLE_RE.sub(" ", flat_region[len(m.group(0)):])
            found = [p.group(1) for p in PERSON_RE.finditer(cleaned)
                     if not ({w.lower() for w in p.group(1).split()} & _ROLE_WORDS)][:4]
        names += found
    if not names:
        names = list(accepted)
    names = list(dict.fromkeys(names))
    # Forma z podpisu „Pełnomocnictwo przyjmuję” jest w mianowniku — preferowana przy zgodnym nazwisku.
    by_surname = {a.split()[-1][:4].lower(): a for a in accepted}
    return list(dict.fromkeys(by_surname.get(n.split()[-1][:4].lower(), n) for n in names))


_PAREN = re.compile(r"\([^()]{0,200}\)", re.DOTALL)
_SEGMENT_SPLIT = re.compile(r"\s+oraz\s+|\s+i\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ])|[,;]|\s[•▪·-]\s|\s\d{1,2}[.)]\s")
_NEW_PERSON_LINE = re.compile(rf"^(?:(?:\d{{1,2}}|[IVXl]{{1,4}})\s*[.)]\s*)?(?:{TITLE_RE.pattern})?{NAME_WORD}\s+{NAME_WORD}(?![\wąćęłńóśźż])",
                              re.IGNORECASE)
_SEGMENT_SKIP = re.compile(r"^(?:nr\b|wpisu\b|pesel\b|adwokat\w*|radc\w*|r\.\s?pr\.?|adw\.?|zwan\w*\s+dalej)", re.IGNORECASE)
_SEGMENT_PERSON = re.compile(rf"^({NAME_WORD}\s+{NAME_WORD})(?=\s*(?:$|[—–\-:]))")


def _segment_persons(region: str) -> list[str]:
    """Osoby z bloku po frazie udzielenia: kilka osób w linii („X (…) oraz Y (…)”) i po jednej w linii.
    Nawiasy (także wieloliniowe) są usuwane; linia, która nie zaczyna się od imienia i nazwiska, jest
    kontynuacją poprzedniej. Pierwszy segment będący zwykłym zdaniem kończy listę (ochrona przed nazwiskami
    z innych sekcji)."""
    lines = [ln.strip() for ln in region.split("\n")]
    lines = [ln for ln in lines if len(ln) > 3 and not _PUNCT_ONLY.match(ln)]   # linie-śmieci OCR („o”, „:”)
    joined: list[str] = []
    for ln in lines:
        if joined and ln and not _NEW_PERSON_LINE.match(ln):
            joined[-1] += " " + ln
        else:
            joined.append(ln)
    text = _PAREN.sub(" ", "\n".join(joined))
    out: list[str] = []
    for chunk in text.split("\n"):
        for seg in _SEGMENT_SPLIT.split(" " + chunk + " "):
            seg = _ENUM_PREFIX.sub("", seg.strip())
            if len(seg) <= 3 or _PUNCT_ONLY.match(seg) or _SEGMENT_SKIP.match(seg):
                continue
            seg = TITLE_RE.sub("", seg, count=1).strip() if TITLE_RE.match(seg) else seg
            m = _SEGMENT_PERSON.match(seg)
            if not m or ({w.lower() for w in m.group(1).split()} & _ROLE_WORDS):
                return out
            out.append(m.group(1))
    return out


def _line_persons(region: str) -> list[str]:
    """Lista pełnomocników zapisana linia po linii (bez tytułów przed nazwiskiem) — każda linia to jedna osoba.
    Tylko zwarty blok bezpośrednio po frazie udzielenia: krótkie linie-śmieci OCR są pomijane,
    pierwsza inna linia kończy listę (np. „wskazanym pełnomocnikom do:” -> brak osób)."""
    out = []
    for raw in region.split("\n"):
        line = raw.strip()
        if len(line) <= 3 or _PUNCT_ONLY.match(line):
            continue
        m = _LINE_PERSON.match(_ENUM_PREFIX.sub("", line))
        if not m or ({w.lower() for w in m.group(1).split()} & _ROLE_WORDS):
            break
        out.append(m.group(1))
    return out[:6]


def _representatives(flat: str) -> list[str]:
    """Podpisujący, gdy brak linii podpisów: reprezentanci z funkcją, „Za Mocodawcę:”, podpis elektroniczny."""
    if m := _REPRESENTED.search(flat):
        people = [p.group(1) for p in _PERSON_WITH_ROLE.finditer(m.group(1))]
        if people:
            return list(dict.fromkeys(people))
    if m := _FOR_PRINCIPAL.search(flat):
        people = [p.group(1) for p in _PERSON_WITH_ROLE.finditer(m.group(1))]
        if people:
            return list(dict.fromkeys(people))
    if m := _E_SIGNED.search(flat):
        return [m.group(1)]
    return []


def _assign_signers(mocodawcy: list[dict], principal: list[tuple[str | None, str]]) -> None:
    if not mocodawcy:
        return
    for section, person in principal:
        target = mocodawcy[0]
        if section and len(mocodawcy) > 1:
            first = section.split()[0].lower()
            for m in mocodawcy:
                if (m["data"]["nazwa"] or "").lower().startswith(first):
                    target = m
                    break
        names = [p["imie_nazwisko"] for p in target["podpisujacy"]]
        if person not in names:
            target["podpisujacy"].append({"imie_nazwisko": person})


def extract_pelnomocnictwo(ctx: ExtractionContext, cfg: dict) -> FieldResult:
    res = FieldResult(data={})
    flat, text, lines = ctx.flat, ctx.text, ctx.lines
    mocodawcy = _mocodawcy(flat, res)
    principal, accepted = _signatures(lines)
    if not principal and len(mocodawcy) == 1:
        principal = [(None, p) for p in _representatives(flat)]
        if principal:
            res.evidence["podpisujacy"] = "reprezentanci / podpis: " + ", ".join(p for _, p in principal)
    _assign_signers(mocodawcy, principal)
    for m in mocodawcy:
        # Osoba fizyczna podpisująca bez sekcji: podpisujący = mocodawca.
        if m["data"]["typ"] == "osoba_fizyczna" and not m["podpisujacy"] and m["data"]["imie_nazwisko"]:
            m["podpisujacy"].append({"imie_nazwisko": m["data"]["imie_nazwisko"]})

    pelnomocnicy = []
    for name in _pelnomocnicy(text, accepted):
        pesel = None
        idx = flat.find(name.split()[-1])
        if idx >= 0 and (pm := _PESEL.search(flat[idx: idx + 120])):
            pesel = pm.group(1)
        pelnomocnicy.append({"imie_nazwisko": name, "pesel": pesel})

    if not mocodawcy:
        res.warnings.append("nie znaleziono mocodawcy (brak KRS/NIP/REGON)")
    if not pelnomocnicy:
        res.warnings.append("nie znaleziono pełnomocników")
    res.data = {"mocodawca": mocodawcy, "pelnomocnicy": pelnomocnicy}
    return res
