"""Ekstrakcja pól z odwołania do KIO.

Zasady zgodne z komentarzami w schemacie organizatora (oczekiwane-json/odwolanie.json):
- data i miejscowość tylko z linii nagłówka typu „X, dnia …” / „X DD.MM.RRRR”;
- Odwołujący: podmiot wnoszący odwołanie; przy konsorcjum — Lider;
- KRS/NIP/REGON zamawiającego tylko „jawnie podane przy Zamawiającym” (w jego bloku);
- prowadzacy_postepowanie: null przy jednym zamawiającym, 1/0 przy kilku;
- numer TED w formacie NNNNNN-YYYY, BZP bez spacji; numery tylko z części nagłówkowej (dystraktory w uzasadnieniu);
- czynność: kategoria z pierwszego zarzutu, a gdy jest on nieokreślony — ze zdania o czynności Zamawiającego.
"""

from __future__ import annotations

import re

from .base import ExtractionContext, FieldResult
from .entities import clean_name, find_ids
from .normalizers import (
    BZP_RE, DATE_PATTERN, EMAIL_RE, clean_person, collapse_ws, drop_punct_tokens, normalize_bzp, normalize_ted,
    parse_date, repair_email, strip_ocr_noise,
)

_HEADER_LINE = re.compile(
    rf"^([A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż.\-]*(?:[ -][A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż.\-]*){{0,3}}),?\s+"
    rf"(?:dnia\s+|dn\.\s*)?((?i:{DATE_PATTERN}))\s*(?:r\.|roku)?\s*$"
)
_HEAD_END = re.compile(r"działając\s+w\s+imieniu|niniejszym\s+wnoszę|wnoszę\s+odwołanie|\bUZASADNIENIE\b", re.IGNORECASE)
_MARKERS = re.compile(
    r"(?P<odw>\bOdwo[łl]uj[ąa]c\w*\s*:)"
    r"|(?P<zam>\bZamawiaj[ąa]c\w*\s*(?:\([^)]{0,200}\))?\s*:)"
    r"|(?P<sub>\b\d\)\s*Zamawiaj[ąa]c\w*\s*[-–][^:]{0,80}:)"
    r"|(?P<dot>\bdotyczy\s*:)"
    r"|(?P<title>\bO\s?D\s?W\s?O\s?Ł\s?A\s?N\s?I\s?E\b)"
    r"|(?P<kio>\bPrezes\w*\s+Krajowej\s+Izby\s+Odwoławczej)"
    r"|(?P<misc>\bWartość\s+zamówienia|\bWpis\s+od\s+odwołania|\bNr\s+ogłoszenia\b)"
)
_REP = re.compile(r"reprezentowan\w*\s+przez\s*:?\s*", re.IGNORECASE)
_CORRESP = re.compile(r"adres\s+do\s+(?:korespondencji|doręczeń)\s*:?\s*(.+?)(?:,\s*(?:ul\.|al\.|pl\.)|\s+ul\.|,\s*\d{2}-\d{3})", re.IGNORECASE)
_KANCELARIA = re.compile(r"(Kancelari\w+[^,;\n]{0,120}?)(?:,\s*(?:ul\.|al\.|pl\.)|\s+ul\.|,\s*\d{2}-\d{3}|\s+[-–]\s)", re.IGNORECASE)
_LEADER = re.compile(r"Lider\w*(?:\s+Konsorcjum)?\s*[:\-–]\s*", re.IGNORECASE)
_PARTNER = re.compile(r"\b\d\)\s*Partner|\bPartner\w*\s+Konsorcjum", re.IGNORECASE)
_REF_NO = re.compile(
    r"(?:nr\s+referencyjn\w*(?:\s+sprawy)?|numer\s+referencyjn\w*|znak\s+sprawy|znak\s+postępowania|"
    r"nr\s+postępowania|numer\s+postępowania|postępowani\w+\s+nr|nr\s+sprawy|sygn\.\s+postępowania)\s*:?\s*"
    r"([A-ZĄĆĘŁŃÓŚŹŻ0-9][\w./\-]*\d[\w./\-]*)",
    re.IGNORECASE,
)
_TITLE = re.compile(r"(?:pn\.\s*:?|pod\s+nazwą|nazwa\s+zamówienia\s*:|przedmiotem\s+jest)\s*[„\"“]([^”\"“„]{5,400})[”\"“]", re.IGNORECASE)
_QUOTED = re.compile(r"[„\"“]([^”\"“„]{10,400})[”\"“]")
_TED_HINT = re.compile(
    r"(?:TED|Dzienniku\s+Urzędowym|Dz\.\s*Urz\.\s*UE|Dz\.U\.UE)[^0-9]{0,40}((?:\d{4}\s*/\s*S\s*\d{1,3}\s*[-–]\s*\d{5,6})|(?:\d{5,6}\s*[-–]\s*\d{4}))",
    re.IGNORECASE,
)
_TED_BARE = re.compile(r"\d{4}\s*/\s*S\s*\d{1,3}\s*[-–]\s*\d{5,6}", re.IGNORECASE)
_ZARZUT1 = re.compile(r"Zarzut\s+(?:nr\s+)?1\b\s*[-–:]?\s*(.{0,250})", re.IGNORECASE)
_ACTION = re.compile(
    rf"Zamawiając\w*((?:\s+[\wąćęłńóśźż]+){{0,8}}?)\s+(?:w\s+dniu|dnia|z\s+dnia)\s+({DATE_PATTERN})(?:\s*(?:r\.|roku))?(.{{0,220}})",
    re.IGNORECASE,
)
# Opis zaskarżonej czynności bez daty, np. „od czynności Zamawiającego polegającej na odrzuceniu oferty”,
# „wobec treści dokumentów zamówienia – SWZ”.
_ACT_PHRASE = re.compile(
    r"(?:(?:od|wobec|na)\s+(?:niezgodn\w*\s+z\s+przepisami\s+\w+\s+(?:\w+\s+)?)?czynnoś\w*|wobec\s+treści|zaniechani\w+\s+zamawiając\w*)"
    r"([^.]{0,250})",
    re.IGNORECASE,
)
# „wnoszę odwołanie wobec: 1. <czynność …> … w dniu <data>; 2. …” — pierwszy punkt = pierwsza zaskarżona czynność.
_APPEAL_LIST = re.compile(
    r"wnosz[ęe]\s+odwołanie\s+(?:wobec|od|na)\b[^:;]{0,150}?:\s*(.{0,450}?)(?:;|\s(?:2|II)\s?[.)]\s|$)",
    re.IGNORECASE,
)
# „Informacja o zaskarżonej czynności została przekazana/opublikowana … w dniu <data>”.
_INFO_DATE = re.compile(
    rf"informacj\w*\s+o\s+zaskarżonej\s+czynności[^.]{{0,120}}?w\s+dniu\s+({DATE_PATTERN})", re.IGNORECASE
)
_ENUM_ITEM = re.compile(r"(?:^|\s)([1-9])\)\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ])")
_EMAIL_LABEL = re.compile(r"e-?mail\s*:?\s*(\S+)", re.IGNORECASE)
_CHRONO = re.compile(rf"({DATE_PATTERN})\s+([^\n]{{0,160}}?zaskarżon\w*\s+czynnoś\w*)", re.IGNORECASE)


class _Czynnosc:
    def __init__(self, cfg: list[dict]):
        self.rules = [(int(c["id"]), c["opis"], [re.compile(p, re.IGNORECASE) for p in c["patterns"]]) for c in cfg]
        self.opis = {i: o for i, o, _ in self.rules}

    def categorize(self, text: str) -> int | None:
        for cid, _, patterns in self.rules:
            if any(p.search(text) for p in patterns):
                return cid
        return None


def _header(lines: list[str]) -> tuple[str | None, str | None, str | None]:
    for line in lines[:60]:
        if line.lower().startswith(("zamawiaj", "odwołuj")):
            continue
        if m := _HEADER_LINE.match(line):
            return collapse_ws(m.group(1)), parse_date(m.group(2)), line
    return None, None, None


def _blocks(head: str) -> list[tuple[str, int, int]]:
    """(rodzaj, początek treści, koniec treści) dla kolejnych etykiet w nagłówku."""
    marks = [(m.lastgroup, m.start(), m.end()) for m in _MARKERS.finditer(head)]
    out = []
    for i, (kind, _, content_start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(head)
        out.append((kind, content_start, end))
    return out


def _entity_name(block: str) -> str | None:
    """Nazwa = początek bloku do adresu / identyfikatorów / reprezentacji."""
    cut = re.search(r",?\s*(?:ul\.|al\.|pl\.|os\.|\d{2}-\d{3}\b|KRS\b|NIP\b|REGON\b|reprezentowan|z\s+siedzibą|przedsiębiorca\s+wpisany)",
                    block, re.IGNORECASE)
    return clean_name(block[: cut.start()] if cut else block[:150])


def _party(block: str) -> dict:
    rep = _REP.search(block)
    ids = find_ids(block, 0, rep.start() if rep else None)
    return {"krs": ids.get("KRS"), "nazwa": _entity_name(block), "nip": ids.get("NIP"), "regon": ids.get("REGON")}


def _odwolujacy(block: str, res: FieldResult) -> dict:
    target = block
    if lider := _LEADER.search(block):
        target = block[lider.end():]
        if partner := _PARTNER.search(target):
            target = target[: partner.start()]
    party = _party(target)

    rep = _REP.search(block)
    pel = {"imie_nazwisko": None, "nazwa": None, "mail": None}
    if rep:
        pel["imie_nazwisko"] = clean_person(block[rep.end(): rep.end() + 80].split(",")[0])
        after = block[rep.end():]
        if m := _CORRESP.search(after) or _KANCELARIA.search(after):
            pel["nazwa"] = _clean_firm(m.group(1))
        pel["mail"] = _find_email(after)
        res.evidence["Odwolujacy[0].pelnomocnik"] = collapse_ws(block[rep.start(): rep.start() + 300])
    party["przez-pelnomocnika"] = 1 if rep else 0
    party["pelnomocnik"] = pel
    res.evidence["Odwolujacy[0]"] = collapse_ws(target[:250])
    return party


def _clean_firm(text: str) -> str | None:
    """Nazwa kancelarii bez szumu OCR na brzegach (także cyfry przed słowem 'Kancelaria')."""
    value = strip_ocr_noise(collapse_ws(text))
    return re.sub(r"^\d+\s+(?=Kancelari)", "", value) if value else None


def _find_email(text: str) -> str | None:
    """Pierwszy e-mail w tekście; gdy OCR zgubił '@', naprawa tylko tokenu po etykiecie 'e-mail:'."""
    label = _EMAIL_LABEL.search(text)
    plain = EMAIL_RE.search(text)
    if plain and (not label or plain.start() <= label.end() + 2):
        return plain.group(0).lower()
    if label:
        repaired = repair_email(label.group(1))
        if repaired:
            return repaired
    return plain.group(0).lower() if plain else None


def _enumerated_parties(block: str) -> list[dict]:
    """Zamawiający wymienieni jako „1) Nazwa … 2) Nazwa …” w jednym bloku (zamówienie wspólne)."""
    items = list(_ENUM_ITEM.finditer(block))
    if len(items) < 2 or [m.group(1) for m in items[:2]] != ["1", "2"]:
        return []
    out = []
    for i, m in enumerate(items):
        seg = block[m.end(): items[i + 1].start() if i + 1 < len(items) else len(block)]
        party = _party(seg)
        party["prowadzacy_postepowanie"] = 1 if re.search(r"prowadząc\w*\s+postępowani|\blider", seg, re.IGNORECASE) else 0
        out.append(party)
    return out


def _zamawiajacy(head: str, blocks: list[tuple[str, int, int]], res: FieldResult) -> list[dict]:
    subs = [(s, e) for kind, s, e in blocks if kind == "sub"]
    if subs:
        out = []
        for (s, e), m in zip(subs, [m for m in _MARKERS.finditer(head) if m.lastgroup == "sub"]):
            party = _party(head[s:e])
            party["prowadzacy_postepowanie"] = 1 if re.search(r"lider|prowadząc", m.group(0), re.IGNORECASE) else 0
            out.append(party)
        res.evidence["Zamawiajacy"] = collapse_ws(head[subs[0][0]: subs[-1][1]][:400])
        return out
    for kind, s, e in blocks:
        if kind == "zam":
            if enumerated := _enumerated_parties(head[s:e]):
                res.evidence["Zamawiajacy"] = collapse_ws(head[s: s + 400])
                return enumerated
            party = _party(head[s:e])
            party["prowadzacy_postepowanie"] = None
            res.evidence["Zamawiajacy[0]"] = collapse_ws(head[s: s + 250])
            return [party]
    return []


def _zamowienie(head: str, blocks, flat: str, res: FieldResult) -> dict:
    dotyczy = " ".join(head[s:e] for kind, s, e in blocks if kind in ("dot", "title", "misc")) or head
    scope = head  # numery tylko z części nagłówkowej
    out = {"numer_bzp": None, "numer_postepowania": None, "numer_ted": None, "tytul": None}
    if m := BZP_RE.search(dotyczy) or BZP_RE.search(scope):
        out["numer_bzp"] = normalize_bzp(m.group(0))
    if m := _REF_NO.search(dotyczy) or _REF_NO.search(scope):
        out["numer_postepowania"] = m.group(1).rstrip(".,;)")
    if m := _TED_HINT.search(dotyczy) or _TED_HINT.search(scope) or _TED_BARE.search(scope):
        out["numer_ted"] = normalize_ted(m.group(0))
    if m := _TITLE.search(dotyczy) or _TITLE.search(scope) or _QUOTED.search(dotyczy):
        out["tytul"] = strip_ocr_noise(drop_punct_tokens(m.group(1)))
    res.evidence["ZamowieniePubliczne"] = collapse_ws(dotyczy[:400])
    return out


def _czynnosc(flat: str, text: str, cz: _Czynnosc, res: FieldResult) -> tuple[int | None, str | None]:
    zarzut_id = None
    if m := _ZARZUT1.search(flat):
        heading = re.split(r":|,\s*tj\.|\s+tj\.", m.group(1))[0]
        zarzut_id = cz.categorize(heading)
        res.evidence["czynnosc.zarzut_1"] = collapse_ws(heading)

    list_id = list_day = None
    if zarzut_id is None and (m := _APPEAL_LIST.search(flat)):
        item = m.group(1)
        list_id, list_day = cz.categorize(item), parse_date(item)
        if list_id is not None:
            res.evidence["czynnosc.punkt_1"] = collapse_ws(item[:200])

    actions = []
    for m in _ACTION.finditer(flat):
        tail = re.split(r"(?<!\br)(?<!\bul)(?<!\bnr)(?<!\bpkt)(?<!\bust)(?<!\bart)\.\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ])", m.group(3))[0]
        snippet = f"{m.group(1)} {tail}"
        actions.append((parse_date(m.group(2)), cz.categorize(snippet), collapse_ws(m.group(0)[:200])))
    categorized = [a for a in actions if a[1] is not None]

    chosen = None
    if zarzut_id is not None:
        chosen = next((a for a in categorized if a[1] == zarzut_id), None)
    chosen = chosen or (categorized[0] if categorized else None)
    cid = zarzut_id if zarzut_id is not None else (list_id if list_id is not None else (chosen[1] if chosen else None))
    if cid is None:
        # Brak zarzutu nr 1 i zdania o czynności z kategorią: opis czynności bez daty (pierwszy rozpoznany).
        for m in _ACT_PHRASE.finditer(flat):
            cid = cz.categorize(m.group(0))
            if cid is not None:
                res.evidence["czynnosc.opis_czynnosci"] = collapse_ws(m.group(0)[:200])
                break
    if list_id is not None:
        chosen = next((a for a in categorized if a[1] == list_id), None)
    day = list_day if list_id is not None and list_day else (chosen[0] if chosen else None)
    if chosen:
        res.evidence["czynnosc.zdanie"] = chosen[2]
    if day is None and cid is not None and (m := _INFO_DATE.search(flat)):
        day = parse_date(m.group(1))
        res.evidence["czynnosc.data_informacji"] = collapse_ws(m.group(0)[:200])
    if day is None and (m := _CHRONO.search(text)):  # wiersz tabeli chronologii „… – zaskarżona czynność”
        day = parse_date(m.group(1))
    if cid is None:
        res.warnings.append("nie ustalono kategorii zaskarżonej czynności")
    if day is None:
        res.warnings.append("nie ustalono daty zaskarżonej czynności")
    return cid, day


def extract_odwolanie(ctx: ExtractionContext, cfg: dict) -> FieldResult:
    res = FieldResult(data={})
    flat, lines = ctx.flat, ctx.lines

    miejscowosc, data, header_line = _header(lines)
    if header_line:
        res.evidence["OdwolanieKIO.data"] = header_line

    head_end = _HEAD_END.search(flat)
    head = flat[: head_end.start()] if head_end else flat[:6000]
    blocks = _blocks(head)

    odw = next(((s, e) for kind, s, e in blocks if kind == "odw"), None)
    odwolujacy = [_odwolujacy(head[odw[0]: odw[1]], res)] if odw else []
    if not odw:
        res.warnings.append("nie znaleziono bloku Odwołującego")
    zamawiajacy = _zamawiajacy(head, blocks, res)

    cz = _Czynnosc(cfg["czynnosc"])
    cid, day = _czynnosc(flat, ctx.text, cz, res)

    res.data = {
        "OdwolanieKIO": {
            "data": data,
            "czynnosc_dzien": day,
            "czynnosc_powod_id": cid,
            "czynnosc_opis": cz.opis.get(cid) if cid is not None else None,
            "miejscowosc": miejscowosc,
        },
        "Odwolujacy": odwolujacy,
        "Zamawiajacy": zamawiajacy,
        "ZamowieniePubliczne": _zamowienie(head, blocks, flat, res),
    }
    return res
