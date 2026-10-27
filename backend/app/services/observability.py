"""Model-call logging (its own transaction, so failed pipelines keep their call history) and budget guard."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timezone

from sqlalchemy import func, select

from app.db import session_scope
from app.llm.gateway import CallRecord
from app.models import ModelCall

log = logging.getLogger("model_calls")


def record_call_to_db(record: CallRecord) -> None:
    with session_scope() as db:
        db.add(ModelCall(
            trace_id=record.trace_id,
            receipt_id=uuid.UUID(record.receipt_id) if record.receipt_id else None,
            feature=record.feature, provider=record.provider, model=record.model,
            prompt_version=record.prompt_version, attempt=record.attempt, status=record.status,
            error_category=record.error_category, error_message=record.error_message,
            finish_reason=record.finish_reason, input_tokens=record.input_tokens,
            output_tokens=record.output_tokens, thinking_tokens=record.thinking_tokens,
            total_tokens=record.total_tokens, latency_ms=record.latency_ms,
        ))
    # Structured log line: metadata only, never receipt contents or credentials.
    log.info("model_call", extra={
        "trace_id": record.trace_id, "receipt_id": record.receipt_id, "model": record.model,
        "attempt": record.attempt, "status": record.status, "error_category": record.error_category,
        "latency_ms": record.latency_ms, "input_tokens": record.input_tokens, "output_tokens": record.output_tokens,
    })


def calls_today(db) -> int:
    start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    return db.scalar(select(func.count(ModelCall.id)).where(ModelCall.created_at >= start)) or 0
