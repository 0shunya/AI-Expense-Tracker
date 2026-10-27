"""Run the extraction evaluation.

Live (calls Gemini through the same gateway, validators and rules the app uses):
    python evaluation/run_eval.py
    python evaluation/run_eval.py --model gemini-3.8-flash --passes 2 --publish

Re-score saved model outputs without any model call (e.g. after changing a validator):
    python evaluation/run_eval.py --replay evaluation/results/runs/<run>/raw_outputs.jsonl

Outputs evaluation/results/runs/<run>/{results.json, cases.jsonl, raw_outputs.jsonl, report.md}.
--publish also copies results.json and report.md to evaluation/results/published/ (committed to the repo).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))

from metrics import KEY_FIELDS, summarize  # noqa: E402

AS_OF = date(2026, 10, 27)


def load_manifest(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def extract_live(case: dict, gateway, dataset_dir: Path, passes: int) -> dict:
    from app.llm.gateway import ExtractionFailed
    from app.services.files import inspect_upload, prepare_pages

    data = (dataset_dir / case["file"]).read_bytes()
    info = inspect_upload(data, max_bytes=20 * 1024 * 1024, max_pdf_pages=20)
    pages = prepare_pages(data, info.content_type)
    outputs = []
    for _ in range(passes):
        started = time.perf_counter()
        try:
            result = gateway.extract_receipt(pages, trace_id=f"eval-{case['id']}")
            outputs.append({"ok": True, "raw": result.raw, "latency_ms": round((time.perf_counter() - started) * 1000),
                            "attempts": result.attempts, "input_tokens": result.input_tokens, "output_tokens": result.output_tokens})
        except ExtractionFailed as exc:
            outputs.append({"ok": False, "error_category": exc.category, "message": exc.message, "attempts": exc.attempts,
                            "latency_ms": round((time.perf_counter() - started) * 1000)})
    return {"id": case["id"], "passes": outputs}


def score_case(case: dict, raw: dict | None, prior_keys: list) -> dict:
    """Apply the production normalisation, validation, uncertainty and duplicate rules to one model output."""
    from app.llm.gateway import parse_reply
    from app.services.duplicates import DuplicateKey, find_duplicates
    from app.services.money import MoneyParseError, parse_minor
    from app.services.pipeline import _disagreements
    from app.services.receipt_data import from_extraction
    from app.services.uncertainty import assess
    from app.services.validation import reconciles, validate

    expected = case["expected"]
    record = {"id": case["id"], "tags": case["tags"], "truth": case["ground_truth"], "expected": expected,
              "model_called": raw is not None, "schema_valid": False, "error_category": None, "values": {}, "statuses": {},
              "lines": [], "needs_review": True, "reasons": [], "reconciles": None, "duplicate_predicted": None,
              "duplicate_of_predicted": None, "latency_ms": None, "input_tokens": None, "output_tokens": None, "attempts": None}
    if raw is None:
        return record
    first = raw["passes"][0]
    record.update(latency_ms=first.get("latency_ms"), attempts=first.get("attempts"),
                  input_tokens=first.get("input_tokens"), output_tokens=first.get("output_tokens"))
    if not first["ok"]:
        record["error_category"] = first["error_category"]
        record["statuses"] = {name: "invalid" for name in KEY_FIELDS}  # a failed receipt always goes to a human
        record["reasons"] = [{"code": f"failed:{first['error_category']}"}]
        return record
    extraction, _ = parse_reply(json.dumps(first["raw"]))
    data = from_extraction(extraction)
    disagreements = None
    if len(raw["passes"]) > 1 and raw["passes"][1]["ok"]:
        disagreements = _disagreements(data, from_extraction(parse_reply(json.dumps(raw["passes"][1]["raw"]))[0]))

    try:
        total_minor = parse_minor(data.get("total"), data.currency) if "total" not in data.parse_errors else None
    except MoneyParseError:
        total_minor = None
    key = DuplicateKey(case["id"], data.get("merchant"), data.date, data.currency, total_minor, data.get("transaction_reference"))
    matches = find_duplicates(key, prior_keys)
    duplicate_reasons = [" ".join(matches[0].reasons)] if matches else None
    if extraction.is_receipt:
        prior_keys.append(key)

    checks = validate(data, today=AS_OF, max_age_days=730)
    assessment = assess(data, checks, is_receipt=extraction.is_receipt, readability=extraction.readability,
                        disagreements=disagreements, duplicate_reasons=duplicate_reasons)
    record.update(
        schema_valid=True,
        values={name: data.get(name) for name in data.values},
        statuses={name: a.status for name, a in assessment.fields.items()},
        lines=[line.as_dict() for line in data.lines],
        needs_review=assessment.needs_review,
        reasons=assessment.reasons,
        reconciles=reconciles(checks),
        duplicate_predicted="probable" if matches else None,
        duplicate_of_predicted=matches[0].candidate_id if matches else None,
        readability=extraction.readability,
        is_receipt=extraction.is_receipt,
    )
    return record


def pct(entry) -> str:
    value = entry["value"] if isinstance(entry, dict) and "value" in entry else entry
    if value is None:
        return "n/a"
    suffix = f" ({entry['n']}/{entry['of']})" if isinstance(entry, dict) and "of" in entry else ""
    return f"{value * 100:.1f}%{suffix}"


def report(meta: dict, m: dict, records: list[dict]) -> str:
    f = m["field_accuracy"]
    u = m["uncertainty"]
    lines = [
        f"# Evaluation report — {meta['run_id']}", "",
        f"- Model: `{meta['model']}` · prompt `{meta['prompt_version']}` · passes {meta['passes']} · mode **{meta['mode']}**",
        f"- Dataset: {m['documents']} documents ({m['receipts_with_ground_truth']} labelled receipts, 1 non-receipt, "
        f"{m['documents'] - m['scored_documents']} exact duplicates) · manifest sha256 `{meta['manifest_sha256'][:12]}`",
        f"- Git commit: `{meta['git_sha']}` · started {meta['started_at']}", "",
        "Synthetic, rendered receipts (see `evaluation/dataset/README.md`). Treat these numbers as performance on clean "
        "printed receipts with planted difficulties, not as real-world photo accuracy.", "",
        "## Schema validity vs. correctness", "",
        "| Metric | Result |", "|---|---|",
        f"| Model calls attempted | {m['extraction']['model_calls_attempted']} |",
        f"| Schema-valid responses | {pct(m['extraction']['schema_valid_rate'])} |",
        f"| Extraction failures | {pct(m['extraction']['failure_rate'])} {m['extraction']['failures_by_category'] or ''} |",
        f"| Key-field accuracy (micro, 6 fields) | {pct(m['key_field_accuracy_micro'])} |",
        f"| Exact-match receipts (all key fields + all line items) | {pct(m['exact_match_receipt_accuracy'])} |", "",
        "## Field accuracy", "", "| Field | Accuracy |", "|---|---|",
        *[f"| {name} | {pct(f[name])} |" for name in f], "",
        "## Line items", "",
        f"Precision {pct(m['line_items']['precision'])} · recall {pct(m['line_items']['recall'])} · F1 {pct(m['line_items']['f1'])} · "
        f"matched lines with correct qty and unit price {pct(m['line_items']['matched_lines_with_correct_qty_and_unit'])}", "",
        "## Validation and review", "", "| Metric | Result |", "|---|---|",
        f"| Reconciliation pass rate (checkable predictions) | {pct(m['reconciliation']['pass_rate_on_predictions'])} |",
        f"| Planted arithmetic errors caught | {pct(m['reconciliation']['planted_discrepancies_caught'])} |",
        f"| False reconciliation alarms on consistent receipts | {pct(m['reconciliation']['false_alarms_on_consistent_receipts'])} |",
        f"| Flag recall: wrong key-field values flagged | {pct(u['flag_recall_on_wrong_values'])} |",
        f"| Review load: correct values flagged anyway | {pct(u['review_load_on_correct_values'])} |",
        f"| Designed hard fields flagged | {pct(u['designed_hard_fields_flagged'])} |",
        f"| Receipts with a wrong key field sent to review | {pct(u['receipts_with_any_wrong_key_field_sent_to_review'])} |",
        f"| Receipt-level review detection P / R / F1 | {pct(u['receipt_review_detection']['precision'])} / "
        f"{pct(u['receipt_review_detection']['recall'])} / {pct(u['receipt_review_detection']['f1'])} |", "",
        "## Duplicate detection", "", "| Kind | Precision | Recall |", "|---|---|---|",
        *[f"| {k} | {pct(v['precision'])} | {pct(v['recall'])} |" for k, v in m["duplicates"].items()], "",
        "## Latency and tokens (first pass, successful calls)", "",
        f"p50 {m['latency_ms']['p50'] and round(m['latency_ms']['p50'])} ms · p95 {m['latency_ms']['p95'] and round(m['latency_ms']['p95'])} ms · "
        f"n={m['latency_ms']['n']} · mean input tokens {m['tokens']['mean_input'] and round(m['tokens']['mean_input'])} · "
        f"mean output tokens {m['tokens']['mean_output'] and round(m['tokens']['mean_output'])}. No cost is estimated.", "",
        "## Errors on key fields", "",
    ]
    from metrics import field_correct, is_flagged
    rows = []
    for r in records:
        if not r["truth"]:
            continue
        for name in KEY_FIELDS:
            if not field_correct(name, r["values"].get(name), r["truth"]):
                rows.append(f"| {r['id']} | {name} | `{r['truth'].get(name)}` | `{r['values'].get(name)}` | "
                            f"{'yes' if is_flagged(name, r['statuses'].get(name)) else '**no**'} |")
    lines += (["| Case | Field | Truth | Predicted | Flagged |", "|---|---|---|---|---|", *rows] if rows else ["None."])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=HERE / "dataset" / "manifest.jsonl")
    parser.add_argument("--replay", type=Path, help="re-score a saved raw_outputs.jsonl instead of calling the model")
    parser.add_argument("--model", help="override GEMINI_MODEL")
    parser.add_argument("--passes", type=int, default=1, choices=(1, 2))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--min-interval", type=float, default=0.0,
                        help="seconds between call starts, to stay under a per-minute quota (e.g. 8 for ~7 RPM)")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()

    if args.model:
        os.environ["GEMINI_MODEL"] = args.model
    from app.config import get_settings
    from app.llm.gateway import CallRecord, ModelGateway, build_provider
    from app.llm.prompts import PROMPT_VERSION

    settings = get_settings()
    cases = load_manifest(args.manifest)[: args.limit]
    dataset_dir = args.manifest.parent
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    calls: list[CallRecord] = []

    # Exact duplicates are caught by SHA-256 before any model call, exactly as at upload time.
    hashes: dict[str, str] = {}
    exact_dup_of: dict[str, str] = {}
    for case in cases:
        digest = hashlib.sha256((dataset_dir / case["file"]).read_bytes()).hexdigest()
        if digest in hashes:
            exact_dup_of[case["id"]] = hashes[digest]
        else:
            hashes[digest] = case["id"]
    to_call = [c for c in cases if c["id"] not in exact_dup_of]

    if args.replay:
        raws = {row["id"]: row for row in map(json.loads, args.replay.read_text(encoding="utf-8").splitlines())}
        saved = json.loads((args.replay.parent / "results.json").read_text(encoding="utf-8"))["meta"]
        model, passes, mode = saved["model"], saved["passes"], "replay"
    else:
        if not settings.gemini_api_key:
            print("GEMINI_API_KEY is not set: live evaluation cannot run. Nothing was measured.", file=sys.stderr)
            return 2
        provider = build_provider(settings)
        gateway = ModelGateway(provider, calls.append, max_attempts=4)
        model, passes, mode = settings.gemini_model, args.passes, "live"
        raws = {}
        print(f"Calling {model} on {len(to_call)} documents ({len(cases) - len(to_call)} exact duplicates skipped)…", flush=True)
        pacing = threading.Lock()
        last = [0.0]

        def paced(case):
            with pacing:
                wait = args.min_interval - (time.monotonic() - last[0])
                if wait > 0:
                    time.sleep(wait)
                last[0] = time.monotonic()
            return extract_live(case, gateway, dataset_dir, passes)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for done, result in enumerate(pool.map(paced, to_call), start=1):
                raws[result["id"]] = result
                p = result["passes"][0]
                print(f"  [{done:02d}/{len(to_call)}] {result['id']}: {'ok' if p['ok'] else p['error_category']} {p['latency_ms']} ms", flush=True)

    prior_keys: list = []
    records = []
    for case in cases:
        if case["id"] in exact_dup_of:
            record = score_case(case, None, prior_keys)
            record.update(duplicate_predicted="exact", duplicate_of_predicted=exact_dup_of[case["id"]], needs_review=True)
            records.append(record)
        else:
            records.append(score_case(case, raws.get(case["id"]), prior_keys))

    metrics = summarize(records)
    try:
        git_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or "unknown"
    except OSError:
        git_sha = "unknown"
    meta = {"run_id": run_id, "mode": mode, "model": model, "prompt_version": PROMPT_VERSION, "passes": passes,
            "started_at": started.isoformat(timespec="seconds"), "git_sha": git_sha, "as_of": AS_OF.isoformat(),
            "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
            "gateway_calls": len(calls), "gateway_call_errors": _errors(calls)}
    out = HERE / "results" / "runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"meta": meta, "metrics": metrics}, indent=2, default=str), encoding="utf-8")
    with (out / "cases.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
    with (out / "raw_outputs.jsonl").open("w", encoding="utf-8") as fh:
        for row in raws.values():
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out / "report.md").write_text(report(meta, metrics, records), encoding="utf-8")
    if args.publish:
        published = HERE / "results" / "published"
        published.mkdir(parents=True, exist_ok=True)
        for name in ("results.json", "report.md", "cases.jsonl", "raw_outputs.jsonl"):
            shutil.copy(out / name, published / name)
    print(f"\nWrote {out}")
    print(json.dumps({"key_field_accuracy": metrics["key_field_accuracy_micro"], "exact_match": metrics["exact_match_receipt_accuracy"],
                      "flag_recall": metrics["uncertainty"]["flag_recall_on_wrong_values"]}, default=str))
    return 0


def _errors(calls) -> dict:
    counts: dict = {}
    for c in calls:
        if c.status != "success":
            counts[c.error_category] = counts.get(c.error_category, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
