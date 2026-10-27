"""Seed the database with sample receipts from the evaluation set, using *recorded* Gemini outputs.

No model call is made: each document goes through the real upload, validation, analysis and review code, but
the provider replays the JSON Gemini returned during the published evaluation run. Model-call rows are logged
with provider "replay" so they are never mistaken for live calls.

    cd backend && python -m scripts.seed_demo --limit 12
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import session_scope  # noqa: E402
from app.llm.gateway import ModelGateway  # noqa: E402
from app.llm.providers import ProviderError, ProviderRequest, ProviderResponse  # noqa: E402
from app.services.observability import record_call_to_db  # noqa: E402
from app.services.pipeline import claim_next, ingest_upload, process_receipt  # noqa: E402

DEFAULT_RAW = ROOT / "evaluation" / "results" / "published" / "raw_outputs.jsonl"
MANIFEST = ROOT / "evaluation" / "dataset" / "manifest.jsonl"


class ReplayProvider:
    name = "replay"

    def __init__(self, model: str, record: dict):
        self.model = f"replay:{model}"
        self.record = record

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        first = self.record["passes"][0]
        if not first["ok"]:
            raise ProviderError(first["error_category"], first.get("message", "recorded failure"), retryable=False)
        return ProviderResponse(text=json.dumps(first["raw"]), finish_reason="STOP",
                                input_tokens=first.get("input_tokens"), output_tokens=first.get("output_tokens"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    if not args.raw.exists():
        raise SystemExit(f"{args.raw} not found. Run the evaluation first (evaluation/run_eval.py --publish).")
    model = json.loads((args.raw.parent / "results.json").read_text(encoding="utf-8"))["meta"]["model"]
    recorded = {row["id"]: row for row in map(json.loads, args.raw.read_text(encoding="utf-8").splitlines())}
    cases = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines()]
    seeded = 0
    for case in cases:
        if seeded >= args.limit:
            break
        record = recorded.get(case["id"])
        if record is None:
            continue
        with session_scope() as db:
            result = ingest_upload(db, (MANIFEST.parent / case["file"]).read_bytes(), Path(case["file"]).name)
            if not result.created:
                continue
            receipt_id = claim_next(db)
        provider = ReplayProvider(model, record)
        status = process_receipt(receipt_id, lambda: ModelGateway(provider, record_call_to_db, max_attempts=1))
        print(f"{case['id']}: {status}")
        seeded += 1
    print(f"Seeded {seeded} receipts from recorded outputs of {model}.")


if __name__ == "__main__":
    main()
