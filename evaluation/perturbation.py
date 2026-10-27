"""Perturbation test for the validators: how often do the rules flag a value the model got wrong?

The live run produced too few wrong values to measure flag recall, so this test manufactures them. It takes
each recorded, correct Gemini output, injects one realistic misread at a time (a wrong digit, swapped digits,
a misread line amount, a shifted date, a swapped currency) and runs the production normalisation, validation
and uncertainty code. A perturbation counts as caught when the corrupted field (or, for a line amount, that line
or the subtotal) is flagged. This measures the rules, not the model; no model call is made.

    python evaluation/perturbation.py [--raw evaluation/results/published/raw_outputs.jsonl]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE))

from metrics import is_flagged  # noqa: E402

AS_OF = date(2026, 10, 27)


def bump_digit(value: str) -> str | None:
    """Change the first non-zero digit after the decimal point (or the last digit) by +1 mod 10."""
    chars = list(value)
    positions = [i for i, ch in enumerate(chars) if ch.isdigit()]
    if not positions:
        return None
    target = positions[-2] if len(positions) >= 2 else positions[-1]
    chars[target] = str((int(chars[target]) + 1) % 10)
    out = "".join(chars)
    return out if out != value else None


def transpose(value: str) -> str | None:
    digits = [i for i, ch in enumerate(value) if ch.isdigit()]
    for a, b in zip(digits, digits[1:]):
        if b == a + 1 and value[a] != value[b]:
            return value[:a] + value[b] + value[a] + value[b + 1:]
    return None


def perturbations(raw: dict) -> list[tuple[str, str, dict]]:
    """Return (kind, field_checked, perturbed_reply)."""
    out = []

    def variant(kind, path, new, field):
        if new is None:
            return
        reply = copy.deepcopy(raw)
        target = reply
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = new
        out.append((kind, field, reply))

    total = raw["total"]["value"]
    if total:
        variant("total: one wrong digit", ["total", "value"], bump_digit(total), "total")
        variant("total: two digits swapped", ["total", "value"], transpose(total), "total")
    if raw["subtotal"]["value"]:
        variant("subtotal: one wrong digit", ["subtotal", "value"], bump_digit(raw["subtotal"]["value"]), "subtotal")
    if raw["tax"]["value"]:
        variant("tax: one wrong digit", ["tax", "value"], bump_digit(raw["tax"]["value"]), "tax")
    if raw["line_items"] and raw["line_items"][0].get("line_total"):
        variant("line total: one wrong digit", ["line_items", 0, "line_total"], bump_digit(raw["line_items"][0]["line_total"]), "line_items[0]")
    iso = raw["transaction_date"]["value"]
    if iso:
        try:
            d = date.fromisoformat(iso)
            variant("date: year +1 (future)", ["transaction_date", "value"], d.replace(year=d.year + 1).isoformat(), "date")
            variant("date: day off by one", ["transaction_date", "value"], (d + timedelta(days=1)).isoformat(), "date")
        except ValueError:
            pass
    if raw["currency"]["value"]:
        variant("currency: wrong valid code", ["currency", "value"], "EUR" if raw["currency"]["value"] != "EUR" else "GBP", "currency")
    return out


def flagged(field: str, assessment) -> bool:
    if field.startswith("line_items["):
        index = int(field[11:-1])
        line = assessment.lines[index] if index < len(assessment.lines) else None
        return (line is not None and line.status in ("uncertain", "invalid")) or \
            assessment.fields["subtotal"].status in ("uncertain", "invalid")
    return is_flagged(field, assessment.fields[field].status)


def main() -> int:
    from app.llm.gateway import parse_reply
    from app.services.receipt_data import from_extraction
    from app.services.uncertainty import assess
    from app.services.validation import validate

    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=HERE / "results" / "published" / "raw_outputs.jsonl")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.raw.read_text(encoding="utf-8").splitlines()]
    manifest = {json.loads(l)["id"]: json.loads(l) for l in (HERE / "dataset" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()}

    def run(reply):
        extraction, _ = parse_reply(json.dumps(reply))
        data = from_extraction(extraction)
        checks = validate(data, today=AS_OF, max_age_days=730)
        return data, assess(data, checks, is_receipt=extraction.is_receipt, readability=extraction.readability)

    results: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        first = row["passes"][0]
        case = manifest.get(row["id"])
        # Only perturb receipts the model read cleanly and whose arithmetic is consistent, so any flag is caused
        # by the injected error and not by something already wrong.
        if not first["ok"] or not case or not case["ground_truth"] or case["expected"]["reconciles"] is not True:
            continue
        _, base = run(first["raw"])
        if base.needs_review and any(r["code"].startswith("check:total") or r["code"].startswith("check:items")
                                     or r["code"].startswith("check:line") for r in base.reasons):
            continue
        for kind, field, reply in perturbations(first["raw"]):
            if field in ("date",) and is_flagged("date", base.fields["date"].status):
                continue  # already flagged (e.g. ambiguous date) before perturbation
            _, assessment = run(reply)
            results[kind].append(flagged(field, assessment))

    lines = ["# Validator perturbation test", "",
             f"Source outputs: `{args.raw.relative_to(HERE.parent).as_posix()}` (recorded Gemini replies; no new calls).",
             "Each row injects one realistic misread into an otherwise correct, reconciling receipt and checks whether the",
             "deterministic rules flag the corrupted field. This measures the validators, not the model.", "",
             "| Injected error | Caught | Rate |", "|---|---|---|"]
    caught_all = total_all = 0
    summary = {}
    for kind in sorted(results):
        hits = results[kind]
        caught_all += sum(hits)
        total_all += len(hits)
        summary[kind] = {"caught": sum(hits), "of": len(hits)}
        lines.append(f"| {kind} | {sum(hits)}/{len(hits)} | {sum(hits) / len(hits) * 100:.0f}% |")
    lines += ["", f"Overall: {caught_all}/{total_all} injected errors flagged.", "",
              "Arithmetic errors are caught because they break a sum; a wrong value that keeps the arithmetic consistent",
              "(a plausible date one day off, a different but valid currency) cannot be caught by rules alone. Those need a",
              "second opinion (two-pass disagreement, OCR) or the reviewer. Tax misreads on tax-inclusive receipts are",
              "also missed: the VAT line is informational there, so a wrong figure does not change any sum."]
    out_dir = HERE / "results" / "published"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "perturbation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "perturbation.json").write_text(json.dumps({"by_kind": summary, "caught": caught_all, "total": total_all}, indent=2), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
