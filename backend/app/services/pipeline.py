"""Receipt lifecycle: ingest -> queue -> claim -> extract -> analyse -> needs_review / approved / failed.

The API never waits on the model: uploads are stored and queued (202), and a worker claims queued rows with
``FOR UPDATE SKIP LOCKED`` so two workers never process the same receipt. A lease lets a crashed worker's
job be reclaimed, and ``max_job_attempts`` stops a poison job from looping forever.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_scope
from app.llm.gateway import ExtractionFailed, ExtractionResult, ModelGateway, build_provider
from app.llm.prompts import PROMPT_VERSION
from app.llm.providers import ProviderError
from app.models import (Extraction, ExtractionField, LineItem, Receipt, ReceiptStatus, utcnow)
from app.services import review
from app.services.analysis import run_analysis
from app.services.files import UploadRejected, inspect_upload, prepare_pages
from app.services.observability import calls_today, record_call_to_db
from app.services.receipt_data import FIELD_LABELS, FIELD_ORDER, MONEY_FIELDS, ReceiptData, from_extraction
from app.services.storage import StorageError, get_storage

log = logging.getLogger(__name__)

# User-facing explanations for failure categories (no internals, no receipt content).
FAILURE_MESSAGES = {
    "config_missing": "Extraction is not configured on the server (missing Gemini API key).",
    "auth_error": "The server's Gemini API key was rejected.",
    "model_not_found": "The configured Gemini model is not available.",
    "rate_limited": "Gemini rate limit or quota was reached. Try again later.",
    "server_error": "Gemini was temporarily unavailable after several retries. Try again later.",
    "timeout": "The model did not respond in time after several retries.",
    "network_error": "The server could not reach Gemini.",
    "blocked": "The model declined to process this document.",
    "truncated": "The model's answer was cut off; the document may be too long.",
    "invalid_json": "The model returned malformed output twice; nothing was saved as data.",
    "schema_mismatch": "The model's output did not match the receipt schema; nothing was saved as data.",
    "empty_response": "The model returned an empty response.",
    "daily_limit_reached": "The daily model-call limit for this deployment has been reached.",
    "preprocess_failed": "The stored file could not be prepared for extraction.",
    "storage_error": "The original file could not be read from storage.",
    "abandoned": "Processing was interrupted too many times and was stopped.",
}


@dataclass
class IngestResult:
    receipt: Receipt
    created: bool
    duplicate_of: Receipt | None = None


def ingest_upload(db: Session, data: bytes, filename: str | None, idempotency_key: str | None = None) -> IngestResult:
    """Validate, deduplicate by content hash, store and queue an upload. Raises UploadRejected."""
    settings = get_settings()
    if idempotency_key:
        existing = db.scalar(select(Receipt).where(Receipt.idempotency_key == idempotency_key))
        if existing is not None:
            return IngestResult(existing, created=False)
    info = inspect_upload(data, max_bytes=settings.max_upload_bytes, max_pdf_pages=settings.max_pdf_pages)
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(Receipt).where(Receipt.sha256 == digest))
    if existing is not None:
        # Exact duplicate: same bytes. Never stored or sent to the model twice.
        return IngestResult(existing, created=False, duplicate_of=existing)
    storage = get_storage()
    safe_name = (filename or "receipt").replace("\\", "/").rsplit("/", 1)[-1][:200] or "receipt"
    key = storage.save(db, data, info.content_type)
    receipt = Receipt(original_filename=safe_name, content_type=info.content_type, size_bytes=len(data),
                      page_count=info.page_count, sha256=digest, storage_backend=storage.name, storage_key=key,
                      idempotency_key=idempotency_key, status=ReceiptStatus.queued.value,
                      review_reasons=[], duplicate_reasons=[])
    db.add(receipt)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request with the same bytes or idempotency key won the race.
        db.rollback()
        if storage.name == "local":
            storage.delete(db, key)
        winner = db.scalar(select(Receipt).where(or_(Receipt.sha256 == digest,
                                                     Receipt.idempotency_key == idempotency_key)))
        if winner is None:
            raise
        return IngestResult(winner, created=False, duplicate_of=winner if winner.sha256 == digest else None)
    return IngestResult(receipt, created=True)


def claim_next(db: Session) -> uuid.UUID | None:
    """Claim one queued receipt (or one whose worker lease expired). Safe with concurrent workers on Postgres."""
    settings = get_settings()
    lease_cutoff = utcnow() - timedelta(seconds=settings.job_lease_seconds)
    stmt = (select(Receipt)
            .where(or_(Receipt.status == ReceiptStatus.queued.value,
                       (Receipt.status == ReceiptStatus.processing.value) & (Receipt.locked_at < lease_cutoff)))
            .order_by(Receipt.created_at).limit(1).with_for_update(skip_locked=True))
    receipt = db.scalar(stmt)
    if receipt is None:
        return None
    if receipt.processing_attempts >= settings.max_job_attempts:
        receipt.status = ReceiptStatus.failed.value
        receipt.error_code, receipt.error_message = "abandoned", FAILURE_MESSAGES["abandoned"]
        receipt.locked_at = None
        db.commit()
        return None
    receipt.status = ReceiptStatus.processing.value
    receipt.locked_at = utcnow()
    receipt.processing_attempts += 1
    db.commit()
    return receipt.id


def _fail(db: Session, receipt: Receipt, code: str, detail: str | None = None) -> None:
    receipt.status = ReceiptStatus.failed.value
    receipt.error_code = code
    receipt.error_message = FAILURE_MESSAGES.get(code, detail or "Extraction failed.")
    receipt.locked_at = None
    receipt.review_reasons = []


def _next_version(db: Session, receipt: Receipt) -> int:
    return (db.scalar(select(func.max(Extraction.version)).where(Extraction.receipt_id == receipt.id)) or 0) + 1


def _disagreements(first: ReceiptData, second: ReceiptData) -> dict[str, str]:
    """Fields where two independent passes read different values (measured doubt, not a model score)."""
    out = {}
    for name in ("merchant", "date", "currency", *MONEY_FIELDS):
        a, b = first.get(name), second.get(name)
        if name == "merchant" and a and b:
            a, b = a.casefold().strip(), b.casefold().strip()
        if a != b:
            out[name] = f"Two extraction passes disagree on {FIELD_LABELS[name].lower()}: '{first.get(name)}' vs '{second.get(name)}'."
    return out


def _store_extraction(db: Session, receipt: Receipt, result: ExtractionResult, data: ReceiptData, passes: int) -> Extraction:
    ext = result.extraction
    extraction = Extraction(
        receipt_id=receipt.id, version=_next_version(db, receipt), trace_id=result.trace_id,
        provider=result.provider, model=result.model, prompt_version=result.prompt_version, passes=passes,
        schema_valid=True, raw_output=result.raw, readability=ext.readability, is_receipt=ext.is_receipt,
        tax_included=ext.tax_included_in_prices, category=ext.category_suggestion, notes=ext.notes[:20],
        latency_ms=result.latency_ms,
    )
    for name in FIELD_ORDER:
        extraction.fields.append(ExtractionField(name=name, extracted_value=data.get(name), value=data.get(name),
                                                 source_text=data.source_text.get(name), status="extracted"))
    for position, line in enumerate(data.lines[:200]):
        extraction.line_items.append(LineItem(
            position=position, description=line.description, quantity=line.quantity, unit_price=line.unit_price,
            line_total=line.line_total, discount=line.discount, tax=line.tax, source_text=line.source_text))
    db.add(extraction)
    db.flush()
    return extraction


GatewayFactory = Callable[[], ModelGateway]


def default_gateway() -> ModelGateway:
    settings = get_settings()
    return ModelGateway(build_provider(settings), record_call_to_db, max_attempts=settings.model_max_attempts)


def process_receipt(receipt_id: uuid.UUID, gateway_factory: GatewayFactory | None = None) -> str:
    """Run extraction for a claimed receipt. Returns the final status. Never raises for expected failures."""
    settings = get_settings()
    with session_scope() as db:
        receipt = db.get(Receipt, receipt_id)
        if receipt is None or receipt.status != ReceiptStatus.processing.value:
            return receipt.status if receipt else "missing"
        try:
            original = get_storage(receipt.storage_backend).load(db, receipt.storage_key)
            pages = prepare_pages(original, receipt.content_type)
        except StorageError:
            _fail(db, receipt, "storage_error")
            return receipt.status
        except Exception:
            log.exception("preprocessing failed", extra={"receipt_id": str(receipt_id)})
            _fail(db, receipt, "preprocess_failed")
            return receipt.status
        if settings.daily_model_call_limit and calls_today(db) >= settings.daily_model_call_limit:
            _fail(db, receipt, "daily_limit_reached")
            return receipt.status
        trace_id = str(uuid.uuid4())
        try:
            gateway = (gateway_factory or default_gateway)()
            result = gateway.extract_receipt(pages, receipt_id=str(receipt.id), trace_id=trace_id)
        except ProviderError as exc:  # e.g. missing API key while building the provider
            _fail(db, receipt, exc.category, exc.message)
            return receipt.status
        except ExtractionFailed as exc:
            # Invalid model output is kept for debugging but never becomes receipt data.
            if exc.raw_text is not None or exc.schema_errors:
                db.add(Extraction(receipt_id=receipt.id, version=_next_version(db, receipt), trace_id=exc.trace_id,
                                  provider=gateway.provider.name, model=gateway.provider.model,
                                  prompt_version=PROMPT_VERSION, passes=1, schema_valid=False,
                                  schema_errors=exc.schema_errors, raw_output={"raw_text": exc.raw_text}))
            _fail(db, receipt, exc.category, exc.message)
            return receipt.status

        data = from_extraction(result.extraction)
        disagreements = None
        passes = 1
        if settings.extraction_passes > 1:
            try:
                second = gateway.extract_receipt(pages, receipt_id=str(receipt.id), trace_id=trace_id)
                disagreements = _disagreements(data, from_extraction(second.extraction))
                passes = 2
            except ExtractionFailed:
                log.warning("second extraction pass failed; continuing with one pass", extra={"trace_id": trace_id})

        extraction = _store_extraction(db, receipt, result, data, passes)
        receipt.current_extraction_id = extraction.id
        receipt.error_code = receipt.error_message = None
        receipt.locked_at = None
        assessment = run_analysis(db, receipt, extraction, data, disagreements=disagreements)
        receipt.status = ReceiptStatus.needs_review.value
        if settings.auto_approve_clean_receipts and not assessment.needs_review:
            review.approve(db, receipt, note="Auto-approved: no field was missing, invalid or doubtful.", by="auto_rules")
        log.info("receipt processed", extra={"receipt_id": str(receipt.id), "trace_id": trace_id,
                                             "status": receipt.status, "review_reasons": len(assessment.reasons)})
        return receipt.status


def run_once(gateway_factory: GatewayFactory | None = None) -> bool:
    """Claim and process one job. Returns False when the queue is empty."""
    with session_scope() as db:
        receipt_id = claim_next(db)
    if receipt_id is None:
        return False
    process_receipt(receipt_id, gateway_factory)
    return True


__all__ = ["IngestResult", "UploadRejected", "ingest_upload", "claim_next", "process_receipt", "run_once"]
