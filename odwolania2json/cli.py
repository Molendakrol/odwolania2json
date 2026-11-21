"""Minimalne CLI.

    python -m odwolania2json run      [--input input] [--output output] [--diagnostics diagnostics]
    python -m odwolania2json classify [ŚCIEŻKA] [--input DIR] [--output DIR] [--rules PLIK] [--save-text] [--json]
    python -m odwolania2json evaluate --pred output --gold KATALOG_Z_REFERENCYJNYMI_JSON
    python -m odwolania2json eval --manifest MANIFEST.jsonl [--report reports/run.json] [--label NAZWA] [--no-ocr]
    python -m odwolania2json eval-compare BAZOWY.json NOWY.json [--fail-on-regression]
    python -m odwolania2json manifest-organizer --dane hacknation-2026/dane --out datasets/organizer.jsonl

`run`      — pełne przetwarzanie: JSON-y w formacie organizatora do --output + diagnostyka.
             Kody wyjścia: 0 OK, 1 brak PDF, 2 błąd argumentów/konfiguracji, 3 wyniki już istnieją
             (bez --overwrite), 4 część wyników odrzucona przez walidację (nie zapisana).
`classify` — tylko tekst + klasyfikacja; diagnostyka do --output (domyślnie ./diagnostics).
`evaluate` — wewnętrzne porównanie JSON-ów z referencją (metryka wewnętrzna, nie scoring organizatora).
`eval`     — evaluator: uruchamia pipeline na dokumentach z manifestu, liczy metryki klasyfikacji i ekstrakcji,
             zapisuje raport JSON (metryki wewnętrzne).
`eval-compare` — porównanie dwóch raportów `eval` (poprawy i regresje).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import benchmark
from .classify import DEFAULT_RULES_PATH, RuleClassifier, RuleConfigError
from .evaluation import evaluate_dirs, format_report
from .ocr import engine_from_settings, load_ocr_settings
from .output import APP_CONFIG, OutputExistsError, load_unknown_mode, write_outputs
from .pipeline import Pipeline, find_pdfs
from .report import write_diagnostics


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="odwolania2json", description="Dokumenty spraw odwoławczych KIO -> JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="Przetwórz PDF-y i zapisz JSON-y w formacie organizatora")
    r.add_argument("path", nargs="?", help="Plik PDF lub katalog (domyślnie wartość --input)")
    r.add_argument("--input", default="input", help="Katalog wejściowy (domyślnie: input)")
    r.add_argument("--output", default="output", help="Katalog na wynikowe JSON-y (domyślnie: output)")
    r.add_argument("--diagnostics", default="diagnostics", help="Katalog diagnostyki (domyślnie: diagnostics)")
    r.add_argument("--rules", default=str(DEFAULT_RULES_PATH), help="Plik reguł klasyfikatora (TOML)")
    r.add_argument("--no-ocr", action="store_true", help="Wyłącz OCR (tylko tekst z warstwy PDF)")
    r.add_argument("--app-config", default=str(APP_CONFIG), help="Plik konfiguracji aplikacji (TOML)")
    r.add_argument("--save-text", action="store_true", help="Zapisz też wyciągnięty tekst w diagnostyce")
    r.add_argument("--overwrite", action="store_true", help="Pozwól nadpisać istniejące pliki wynikowe w --output")

    c = sub.add_parser("classify", help="Wyciągnij tekst i sklasyfikuj PDF lub katalog PDF-ów")
    c.add_argument("path", nargs="?", help="Plik PDF lub katalog (domyślnie wartość --input)")
    c.add_argument("--input", default="input", help="Katalog wejściowy (domyślnie: input)")
    c.add_argument("--output", default="diagnostics", help="Katalog na wyniki diagnostyczne (domyślnie: diagnostics)")
    c.add_argument("--rules", default=str(DEFAULT_RULES_PATH), help="Plik reguł klasyfikatora (TOML)")
    c.add_argument("--no-ocr", action="store_true", help="Wyłącz OCR (tylko tekst z warstwy PDF)")
    c.add_argument("--save-text", action="store_true", help="Zapisz też wyciągnięty tekst (.txt)")
    c.add_argument("--json", action="store_true", help="Wypisz wyniki na stdout jako JSON zamiast tabeli")

    e = sub.add_parser("evaluate", help="Porównaj wynikowe JSON-y z referencyjnymi (metryka wewnętrzna)")
    e.add_argument("--pred", default="output", help="Katalog z wynikami (domyślnie: output)")
    e.add_argument("--gold", required=True, help="Katalog z referencyjnymi JSON-ami (np. dane organizatora)")
    e.add_argument("--max-mismatches", type=int, default=30, help="Ile rozbieżności wypisać")

    ev = sub.add_parser("eval", help="Evaluator: metryki klasyfikacji i ekstrakcji na manifeście (raport JSON)")
    ev.add_argument("--manifest", required=True, help="Manifest JSONL (pdf, label, gt, group, source)")
    ev.add_argument("--report", help="Plik raportu JSON (domyślnie reports/eval_<czas>.json)")
    ev.add_argument("--label", default="", help="Nazwa runu zapisywana w raporcie")
    ev.add_argument("--rules", default=str(DEFAULT_RULES_PATH), help="Plik reguł klasyfikatora (TOML)")
    ev.add_argument("--app-config", default=str(APP_CONFIG), help="Plik konfiguracji aplikacji (TOML)")
    ev.add_argument("--no-ocr", action="store_true", help="Wyłącz OCR")

    ec = sub.add_parser("eval-compare", help="Porównaj dwa raporty evaluatora")
    ec.add_argument("base", help="Raport bazowy (JSON)")
    ec.add_argument("new", help="Raport nowy (JSON)")
    ec.add_argument("--report", help="Zapisz porównanie do pliku JSON")
    ec.add_argument("--fail-on-regression", action="store_true", help="Kod wyjścia 1, gdy są regresje")

    mo = sub.add_parser("manifest-organizer", help="Zbuduj manifest dla katalogu `dane` organizatora")
    mo.add_argument("--dane", required=True, help="Katalog dane/ z repozytorium hacknation-2026")
    mo.add_argument("--out", required=True, help="Plik wynikowy JSONL")
    return parser


def _print_table(results) -> None:
    width = max([len(r.relative_path) for r in results] + [4])
    print(f"{'PLIK':<{width}}  {'KLASA':<15} {'STATUS':<8} {'TEKST':<7} UZASADNIENIE")
    for r in results:
        print(f"{r.relative_path:<{width}}  {r.doc_class:<15} {r.status.value:<8} "
              f"{r.extraction.text_source.value:<7} {r.classification.reason}")
    counts = Counter(r.doc_class for r in results)
    print(f"\nDokumentów: {len(results)} | " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))


def _process(pipeline: Pipeline, target: Path):
    if target.is_dir():
        if not find_pdfs(target):
            print(f"Brak plików PDF w katalogu: {target}", file=sys.stderr)
            return None
        return pipeline.process_dir(target)
    return [pipeline.process_file(target)]


def _make_pipeline(args, extract_fields: bool) -> Pipeline | None:
    try:
        settings = load_ocr_settings(Path(getattr(args, "app_config", APP_CONFIG)))
        engine = None if args.no_ocr else engine_from_settings(settings)
        if engine is not None:
            ok, detail = engine.is_available()
            if not ok:
                print(f"UWAGA: OCR niedostępny — skany nie zostaną odczytane ({detail})", file=sys.stderr)
        return Pipeline(RuleClassifier.from_file(args.rules), extract_fields=extract_fields, ocr_engine=engine)
    except (OSError, RuleConfigError, ValueError, KeyError) as exc:
        print(f"Błąd konfiguracji: {exc}", file=sys.stderr)
        return None


def cmd_classify(args: argparse.Namespace) -> int:
    target = Path(args.path or args.input)
    if not target.exists():
        print(f"Błąd: ścieżka nie istnieje: {target}", file=sys.stderr)
        return 2
    pipeline = _make_pipeline(args, extract_fields=False)
    if pipeline is None:
        return 2
    results = _process(pipeline, target)
    if results is None:
        return 1
    out = write_diagnostics(results, args.output, save_text=args.save_text)
    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
    else:
        _print_table(results)
        print(f"Diagnostyka zapisana w: {out}")
    return 0


def _inside(a: Path, b: Path) -> bool:
    """Czy ścieżka a jest równa b lub leży wewnątrz b."""
    a, b = a.resolve(), b.resolve()
    return a == b or b in a.parents


def cmd_run(args: argparse.Namespace) -> int:
    target = Path(args.path or args.input)
    if not target.exists():
        print(f"Błąd: ścieżka nie istnieje: {target}", file=sys.stderr)
        return 2
    out, diag = Path(args.output), Path(args.diagnostics)
    in_dir = target if target.is_dir() else target.parent
    if _inside(out, in_dir) or _inside(in_dir, out):
        print(f"Błąd: katalog wynikowy ({out}) nie może pokrywać się z wejściowym ({in_dir})", file=sys.stderr)
        return 2
    if _inside(diag, out) or _inside(out, diag):
        print(f"Błąd: diagnostyka ({diag}) nie może trafiać do katalogu wyników ({out})", file=sys.stderr)
        return 2
    try:
        unknown_mode = load_unknown_mode(Path(args.app_config))
    except (OSError, KeyError, ValueError) as exc:
        print(f"Błąd konfiguracji aplikacji ({args.app_config}): {exc}", file=sys.stderr)
        return 2
    pipeline = _make_pipeline(args, extract_fields=True)
    if pipeline is None:
        return 2
    results = _process(pipeline, target)
    if results is None:
        return 1
    try:
        report = write_outputs(results, out, unknown_mode, overwrite=args.overwrite)
    except OutputExistsError as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 3
    write_diagnostics(results, diag, save_text=args.save_text)
    _print_table(results)
    print(f"Zapisano {len(report.written)} plików JSON w: {out} | diagnostyka: {diag}")
    if report.rejected:
        print(f"BŁĄD: {len(report.rejected)} wyników nie przeszło walidacji i NIE zostało zapisanych:", file=sys.stderr)
        for path, errors in report.rejected:
            print(f"  {path}: {'; '.join(errors[:5])}", file=sys.stderr)
        return 4
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    pred, gold = Path(args.pred), Path(args.gold)
    for p in (pred, gold):
        if not p.is_dir():
            print(f"Błąd: katalog nie istnieje: {p}", file=sys.stderr)
            return 2
    print(format_report(evaluate_dirs(pred, gold), args.max_mismatches))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    try:
        entries = benchmark.load_manifest(args.manifest)
    except (OSError, benchmark.ManifestError) as exc:
        print(f"Błąd manifestu: {exc}", file=sys.stderr)
        return 2
    missing = [str(e.pdf) for e in entries if not e.pdf.exists()] + [str(e.gt) for e in entries if e.gt and not e.gt.exists()]
    if missing:
        print(f"Błąd: brak {len(missing)} plików z manifestu, np. {missing[0]}", file=sys.stderr)
        return 2
    pipeline = _make_pipeline(args, extract_fields=True)
    if pipeline is None:
        return 2
    report = benchmark.run_evaluation(entries, pipeline, args.label)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = benchmark.save_report(report, args.report or Path("reports") / f"eval_{stamp}.json")
    print(benchmark.format_summary(report))
    print(f"\nRaport zapisany: {path}")
    return 0


def cmd_eval_compare(args: argparse.Namespace) -> int:
    try:
        base, new = benchmark.load_report(args.base), benchmark.load_report(args.new)
    except (OSError, ValueError) as exc:
        print(f"Błąd raportu: {exc}", file=sys.stderr)
        return 2
    cmp = benchmark.compare_reports(base, new)
    print(benchmark.format_comparison(cmp))
    if args.report:
        benchmark.save_report(cmp, args.report)
    return 1 if args.fail_on_regression and cmp["has_regressions"] else 0


def cmd_manifest_organizer(args: argparse.Namespace) -> int:
    if not Path(args.dane).is_dir():
        print(f"Błąd: katalog nie istnieje: {args.dane}", file=sys.stderr)
        return 2
    n = benchmark.build_organizer_manifest(args.dane, args.out)
    print(f"Zapisano manifest ({n} dokumentów): {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return {"classify": cmd_classify, "run": cmd_run, "evaluate": cmd_evaluate, "eval": cmd_eval,
            "eval-compare": cmd_eval_compare, "manifest-organizer": cmd_manifest_organizer}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
