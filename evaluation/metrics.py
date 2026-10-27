"""Evaluation metrics. Pure functions over per-case records, so they are unit-tested without any model.

Definitions
-----------
* Field accuracy: exact match after normalisation (merchant: case/punctuation/legal-suffix-insensitive;
  money: Decimal equality, i.e. exact to the cent; date: any listed reading for ambiguous dates).
  A null prediction matches a null ground truth (correctly reporting "not visible"). Failed extractions
  count as wrong, so accuracy is over all scored documents, not only the successful ones.
* Line items: a predicted line matches a labelled line when descriptions are similar (>= 0.8) and the line
  totals are equal. Precision/recall/F1 are micro-averaged over all lines.
* Exact-match receipt: every key field correct AND every line item matched with nothing extra.
* Flag recall (roadmap): share of wrong key-field values that the system flagged (uncertain/invalid, or
  missing when required). Review load: share of correct values that were flagged anyway.
* Review detection: receipt-level precision/recall of "needs review" vs the labelled expectation.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

KEY_FIELDS = ("merchant", "date", "currency", "subtotal", "tax", "total")
SECONDARY_FIELDS = ("tip", "discount", "service_charge", "transaction_reference")
MONEY = {"subtotal", "tax", "total", "tip", "discount", "service_charge"}
REQUIRED = {"merchant", "date", "currency", "total"}
_SUFFIX = re.compile(r"\b(inc|llc|ltd|limited|pvt|private|co|corp|company|plc|gmbh)\b")


def norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    text = _SUFFIX.sub(" ", re.sub(r"[^a-z0-9 ]+", " ", text))
    return " ".join(text.split()) or None


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def field_correct(name: str, predicted, truth: dict) -> bool:
    expected = truth.get(name)
    if name == "date" and truth.get("date_any_of"):
        return predicted in truth["date_any_of"]
    if predicted is None or expected is None:
        return predicted is None and expected is None
    if name in MONEY:
        return _dec(predicted) is not None and _dec(predicted) == _dec(expected)
    if name == "merchant":
        return norm_text(predicted) == norm_text(expected)
    if name == "transaction_reference":
        return re.sub(r"[^A-Za-z0-9]", "", predicted).upper() == re.sub(r"[^A-Za-z0-9]", "", expected).upper()
    return str(predicted).strip() == str(expected).strip()


def is_flagged(name: str, status: str | None) -> bool:
    return status in ("uncertain", "invalid") or (status == "missing" and name in REQUIRED)


def match_lines(predicted: list[dict], truth: list[dict]) -> tuple[int, int, int, int]:
    """Return (matched, predicted_count, truth_count, matched_with_correct_qty_and_unit)."""
    remaining = list(predicted)
    matched = detail_ok = 0
    for gt in truth:
        best, best_score = None, 0.0
        for cand in remaining:
            if _dec(cand.get("line_total")) != _dec(gt.get("line_total")):
                continue
            score = SequenceMatcher(None, norm_text(cand.get("description")) or "", norm_text(gt.get("description")) or "").ratio()
            if score >= 0.8 and score > best_score:
                best, best_score = cand, score
        if best is not None:
            remaining.remove(best)
            matched += 1
            if _dec(best.get("quantity")) == _dec(gt.get("quantity")) and _dec(best.get("unit_price")) == _dec(gt.get("unit_price")):
                detail_ok += 1
    return matched, len(predicted), len(truth), detail_ok


def prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (0.0 if precision is not None and recall is not None else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def rate(numerator: int, denominator: int) -> dict:
    return {"value": numerator / denominator if denominator else None, "n": numerator, "of": denominator}


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def summarize(records: list[dict]) -> dict:
    """records: one per document, see run_eval.py for the shape."""
    scored = [r for r in records if r["expected"]["duplicate_kind"] != "exact"]  # exact dups never reach the model
    receipts = [r for r in scored if r["truth"] is not None]                       # excludes the non-receipt doc
    attempted = [r for r in scored if r["model_called"]]

    out: dict = {"documents": len(records), "scored_documents": len(scored), "receipts_with_ground_truth": len(receipts)}
    out["extraction"] = {
        "model_calls_attempted": len(attempted),
        "schema_valid_rate": rate(sum(r["schema_valid"] for r in attempted), len(attempted)),
        "failure_rate": rate(sum(not r["schema_valid"] for r in attempted), len(attempted)),
        "failures_by_category": _count(r["error_category"] for r in attempted if not r["schema_valid"]),
    }

    fields = {}
    for name in KEY_FIELDS + SECONDARY_FIELDS:
        ok = sum(field_correct(name, r["values"].get(name), r["truth"]) for r in receipts)
        fields[name] = rate(ok, len(receipts))
    out["field_accuracy"] = fields
    key_correct = sum(field_correct(n, r["values"].get(n), r["truth"]) for r in receipts for n in KEY_FIELDS)
    out["key_field_accuracy_micro"] = rate(key_correct, len(receipts) * len(KEY_FIELDS))

    tp = pc = tc = detail = 0
    exact = 0
    for r in receipts:
        m, p, t, d = match_lines(r["lines"], r["truth"]["line_items"])
        tp, pc, tc, detail = tp + m, pc + p, tc + t, detail + d
        if m == p == t and all(field_correct(n, r["values"].get(n), r["truth"]) for n in KEY_FIELDS):
            exact += 1
    out["line_items"] = {**prf(tp, pc - tp, tc - tp), "matched_lines_with_correct_qty_and_unit": rate(detail, tp)}
    out["exact_match_receipt_accuracy"] = rate(exact, len(receipts))

    checkable = [r for r in receipts if r["reconciles"] is not None]
    planted = [r for r in receipts if r["expected"]["reconciles"] is False]
    clean = [r for r in checkable if r["expected"]["reconciles"] is True]
    out["reconciliation"] = {
        "pass_rate_on_predictions": rate(sum(r["reconciles"] is True for r in checkable), len(checkable)),
        "planted_discrepancies_caught": rate(sum(r["reconciles"] is False for r in planted), len(planted)),
        "false_alarms_on_consistent_receipts": rate(sum(r["reconciles"] is False for r in clean), len(clean)),
    }

    wrong = flagged_wrong = correct = flagged_correct = 0
    for r in receipts:
        for name in KEY_FIELDS:
            ok = field_correct(name, r["values"].get(name), r["truth"])
            flagged = is_flagged(name, r["statuses"].get(name))
            if ok:
                correct += 1
                flagged_correct += flagged
            else:
                wrong += 1
                flagged_wrong += flagged
    expected_flags = [(r, f) for r in scored for f in r["expected"]["flag_fields"]]
    out["uncertainty"] = {
        "flag_recall_on_wrong_values": rate(flagged_wrong, wrong),
        "review_load_on_correct_values": rate(flagged_correct, correct),
        "designed_hard_fields_flagged": rate(sum(is_flagged(f, r["statuses"].get(f)) for r, f in expected_flags), len(expected_flags)),
        "receipt_review_detection": prf(
            sum(r["needs_review"] and r["expected"]["needs_review"] for r in scored),
            sum(r["needs_review"] and not r["expected"]["needs_review"] for r in scored),
            sum((not r["needs_review"]) and r["expected"]["needs_review"] for r in scored)),
        "receipts_with_any_wrong_key_field_sent_to_review": rate(
            sum(r["needs_review"] for r in receipts if not all(field_correct(n, r["values"].get(n), r["truth"]) for n in KEY_FIELDS)),
            sum(not all(field_correct(n, r["values"].get(n), r["truth"]) for n in KEY_FIELDS) for r in receipts)),
    }

    dup = {}
    for kind in ("exact", "probable"):
        dup[kind] = prf(sum(r["duplicate_predicted"] == kind and r["expected"]["duplicate_kind"] == kind for r in records),
                        sum(r["duplicate_predicted"] == kind and r["expected"]["duplicate_kind"] != kind for r in records),
                        sum(r["duplicate_predicted"] != kind and r["expected"]["duplicate_kind"] == kind for r in records))
    dup["any"] = prf(sum(bool(r["duplicate_predicted"]) and bool(r["expected"]["duplicate_kind"]) for r in records),
                     sum(bool(r["duplicate_predicted"]) and not r["expected"]["duplicate_kind"] for r in records),
                     sum(not r["duplicate_predicted"] and bool(r["expected"]["duplicate_kind"]) for r in records))
    out["duplicates"] = dup

    latencies = [r["latency_ms"] for r in attempted if r["latency_ms"] is not None and r["schema_valid"]]
    tokens_in = [r["input_tokens"] for r in attempted if r.get("input_tokens") is not None]
    tokens_out = [r["output_tokens"] for r in attempted if r.get("output_tokens") is not None]
    out["latency_ms"] = {"n": len(latencies), "p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95),
                         "mean": statistics.fmean(latencies) if latencies else None}
    out["tokens"] = {"calls_reporting_usage": len(tokens_in), "mean_input": statistics.fmean(tokens_in) if tokens_in else None,
                     "mean_output": statistics.fmean(tokens_out) if tokens_out else None}
    return out


def _count(items) -> dict:
    counts: dict = {}
    for item in items:
        counts[item or "unknown"] = counts.get(item or "unknown", 0) + 1
    return counts
