import json

from odwolania2json.evaluation import compare, evaluate_dirs, flatten


def test_flatten_paths():
    assert flatten({"a": [{"b": 1}, {"b": None}], "c": {"d": "x"}}) == {"a[0].b": 1, "a[1].b": None, "c.d": "x"}


def test_compare_counts():
    gold = {"x": "A", "y": None, "z": 1, "w": "B"}
    pred = {"x": "A ", "y": "!", "z": 1.0, "w": None}
    c = compare(pred, gold)
    # x: TP (białe znaki), y: FP (przewidziano przy null), z: TP (liczbowo), w: FN
    assert (c.total.tp, c.total.fp, c.total.fn) == (2, 1, 1)
    assert round(c.total.f1, 3) == 0.667


def test_compare_missing_prediction_is_all_fn():
    c = compare(None, {"a": 1, "b": None})
    assert (c.total.tp, c.total.fp, c.total.fn) == (0, 0, 1)


def test_evaluate_dirs_with_alias(tmp_path):
    gold, pred = tmp_path / "gold" / "c1", tmp_path / "pred" / "c1"
    gold.mkdir(parents=True)
    pred.mkdir(parents=True)
    (gold / "przelew.json").write_text(json.dumps({"k": 1}))
    (pred / "potwierdzenie_przelewu.json").write_text(json.dumps({"k": 1}))
    (gold / "odwolanie.json").write_text(json.dumps({"k": 2}))
    (pred / "odwolanie.json").write_text("")                  # pusty plik = brak predykcji
    c = evaluate_dirs(tmp_path / "pred", tmp_path / "gold")
    assert (c.total.tp, c.total.fn) == (1, 1)
