"""Evaluation harness tests. These check the *scorer*, not the model: no Gemini call is made."""

import json
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVAL))

from metrics import field_correct, is_flagged, match_lines, percentile, prf, summarize  # noqa: E402
from run_eval import load_manifest, score_case  # noqa: E402


def test_field_matching_rules():
    truth = {"merchant": "Sharma Electronics Pvt Ltd", "total": "2238.46", "date": "2026-04-05",
             "date_any_of": ["2026-04-05", "2026-05-04"], "tax": None}
    assert field_correct("merchant", "SHARMA ELECTRONICS", truth)
    assert field_correct("total", "2238.460", truth)
    assert not field_correct("total", "2238.47", truth)          # exact to the cent, no tolerance
    assert field_correct("date", "2026-05-04", truth)            # either reading of an ambiguous date
    assert field_correct("tax", None, truth)                      # correctly reporting "not visible"
    assert not field_correct("tax", "0.00", truth)                # inventing a zero is wrong


def test_flag_definition():
    assert is_flagged("total", "uncertain") and is_flagged("total", "invalid") and is_flagged("total", "missing")
    assert not is_flagged("tax", "missing")  # absent optional field is not review load
    assert not is_flagged("total", "extracted")


def test_line_matching_and_prf():
    truth = [{"description": "Flat white", "quantity": "2", "unit_price": "4.50", "line_total": "9.00"},
             {"description": "Croissant", "quantity": "1", "unit_price": "3.25", "line_total": "3.25"}]
    pred = [{"description": "FLAT WHITE", "quantity": "2", "unit_price": "4.5", "line_total": "9"},
            {"description": "Croissant", "quantity": "1", "unit_price": "3.25", "line_total": "3.20"},
            {"description": "Bag", "quantity": "1", "unit_price": "0.10", "line_total": "0.10"}]
    assert match_lines(pred, truth) == (1, 3, 2, 1)
    assert prf(1, 2, 1)["precision"] == pytest.approx(1 / 3)
    assert percentile([100, 200, 300, 400], 0.5) == 250


def _oracle(case: dict) -> dict:
    """A perfect model reply built from the labels (used only to test the harness)."""
    gt = case["ground_truth"]
    tv = lambda v: {"value": v, "source_text": v}  # noqa: E731
    return {"is_receipt": True, "readability": "clear", "merchant_name": tv(gt["merchant"]), "merchant_address": tv(None),
            "transaction_date": tv(gt["date"]), "transaction_time": tv(None), "currency": tv(gt["currency"]),
            "subtotal": tv(gt["subtotal"]), "tax": tv(gt["tax"]), "tip": tv(gt["tip"]), "discount": tv(gt["discount"]),
            "service_charge": tv(gt["service_charge"]), "total": tv(gt["total"]),
            "tax_included_in_prices": "vat_included" in case["tags"] or None, "payment_method": tv(None),
            "transaction_reference": tv(gt["transaction_reference"]), "gstin": tv(None),
            "line_items": [{**li, "tax": None, "source_text": None} for li in gt["line_items"]],
            "category_suggestion": "other", "notes": []}


def test_oracle_outputs_score_perfectly_and_planted_errors_are_caught():
    cases = {c["id"]: c for c in load_manifest(EVAL / "dataset" / "manifest.jsonl")}
    chosen = [cases[i] for i in ("r001", "r004", "r013", "r016", "r037")]
    prior: list = []
    records = [score_case(c, {"passes": [{"ok": True, "raw": _oracle(c), "latency_ms": 10}]}, prior) for c in chosen]
    m = summarize(records)
    assert m["key_field_accuracy_micro"]["value"] == 1.0
    assert m["exact_match_receipt_accuracy"]["value"] == 1.0
    assert m["line_items"]["f1"] == 1.0
    assert m["reconciliation"]["planted_discrepancies_caught"]["value"] == 1.0   # r037 has a planted wrong total
    assert m["reconciliation"]["false_alarms_on_consistent_receipts"]["value"] == 0.0
    r037 = next(r for r in records if r["id"] == "r037")
    assert r037["needs_review"] and r037["statuses"]["total"] == "uncertain"


def test_failed_extraction_counts_as_wrong_and_flagged():
    case = load_manifest(EVAL / "dataset" / "manifest.jsonl")[0]
    record = score_case(case, {"passes": [{"ok": False, "error_category": "server_error", "latency_ms": 5}]}, [])
    m = summarize([record])
    assert m["extraction"]["failure_rate"]["value"] == 1.0
    assert m["field_accuracy"]["total"]["value"] == 0.0
    assert m["uncertainty"]["flag_recall_on_wrong_values"]["value"] == 1.0


def test_manifest_is_consistent():
    rows = load_manifest(EVAL / "dataset" / "manifest.jsonl")
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)) >= 50
    for r in rows:
        assert (EVAL / "dataset" / r["file"]).exists()
        if r["expected"]["duplicate_of"]:
            assert r["expected"]["duplicate_of"] in ids[: ids.index(r["id"])]
    assert json.dumps(rows).count("planted_wrong_total") >= 3


def test_perturbations_and_flagging():
    from perturbation import bump_digit, flagged, perturbations, transpose
    from app.services.uncertainty import assess
    from app.services.receipt_data import from_extraction
    from app.services.validation import validate
    from app.llm.gateway import parse_reply
    from tests.factories import reply

    assert bump_digit("13.23") == "13.33" and transpose("13.23") == "31.23"
    kinds = {k for k, _, _ in perturbations(reply())}
    assert {"total: one wrong digit", "tax: one wrong digit", "date: day off by one"} <= kinds
    wrong = next(r for k, _, r in perturbations(reply()) if k == "total: one wrong digit")
    data = from_extraction(parse_reply(json.dumps(wrong))[0])
    assert flagged("total", assess(data, validate(data)))
