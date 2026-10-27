"""HTTP routes. Handlers stay thin: they parse input, call a service, and serialise the result."""

from __future__ import annotations

import csv
import io
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import __version__
from app.api import schemas as s
from app.api.errors import ApiError
from app.api.ratelimit import upload_limiter
from app.config import get_settings
from app.db import get_db
from app.llm.prompts import PROMPT_VERSION
from app.models import Expense, Extraction, ModelCall, Receipt, ReceiptStatus, ReviewDecision
from app.services import review
from app.services.files import UploadRejected
from app.services.money import from_minor
from app.services.pipeline import ingest_upload
from app.services.storage import StorageError, get_storage

router = APIRouter(prefix="/api")


def _load(db: Session, receipt_id: str) -> Receipt:
    try:
        key = uuid.UUID(receipt_id)
    except ValueError as exc:
        raise ApiError(404, "not_found", "Receipt not found.") from exc
    receipt = db.scalar(select(Receipt).where(Receipt.id == key).options(
        selectinload(Receipt.current_extraction).selectinload(Extraction.fields),
        selectinload(Receipt.current_extraction).selectinload(Extraction.line_items),
        selectinload(Receipt.current_extraction).selectinload(Extraction.checks),
        selectinload(Receipt.extractions), selectinload(Receipt.corrections), selectinload(Receipt.decisions),
        selectinload(Receipt.expense)))
    if receipt is None:
        raise ApiError(404, "not_found", "Receipt not found.")
    return receipt


def _detail(db: Session, receipt: Receipt) -> s.ReceiptDetail:
    duplicate_of = db.get(Receipt, receipt.duplicate_of_id) if receipt.duplicate_of_id else None
    calls = db.scalars(select(ModelCall).where(ModelCall.receipt_id == receipt.id)
                       .order_by(ModelCall.created_at.desc()).limit(20)).all()
    return s.detail(receipt, duplicate_of, list(calls))


def _act(db: Session, receipt_id: str, action) -> s.ReceiptDetail:
    receipt = _load(db, receipt_id)
    try:
        action(receipt)
        db.commit()
    except review.ReviewError as exc:
        db.rollback()
        raise ApiError(exc.status_code, exc.code, exc.message, exc.details) from exc
    db.expire_all()
    return _detail(db, _load(db, receipt_id))


@router.get("/health")
def health(db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        db.execute(select(1))
        database = "ok"
    except Exception:
        database = "unavailable"
    body = {"status": "ok" if database == "ok" else "degraded", "version": __version__, "database": database,
            "environment": settings.app_env, "gemini_configured": settings.gemini_configured,
            "model": settings.gemini_model, "prompt_version": PROMPT_VERSION, "storage": settings.storage_backend,
            "worker": settings.worker_mode, "access_code_required": bool(settings.demo_access_code),
            "max_upload_mb": settings.max_upload_mb, "max_pdf_pages": settings.max_pdf_pages}
    return JSONResponse(body, status_code=200 if database == "ok" else 503)


@router.post("/receipts", status_code=202, response_model=s.UploadOut)
async def upload_receipt(request: Request, file: UploadFile = File(...),
                         idempotency_key: str | None = Header(default=None, max_length=128),
                         db: Session = Depends(get_db)):
    settings = get_settings()
    upload_limiter.check(request)
    data = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    try:
        result = ingest_upload(db, data, file.filename, idempotency_key)
    except UploadRejected as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc
    if result.duplicate_of is not None:
        body = s.UploadOut(receipt=s.summary(result.receipt), created=False, exact_duplicate=True,
                           message="This exact file was already uploaded; showing the existing receipt.")
        return JSONResponse(body.model_dump(mode="json"), status_code=200)
    if not result.created:
        body = s.UploadOut(receipt=s.summary(result.receipt), created=False, exact_duplicate=False,
                           message="Repeated request (same Idempotency-Key); returning the original upload.")
        return JSONResponse(body.model_dump(mode="json"), status_code=200)
    return s.UploadOut(receipt=s.summary(result.receipt), created=True, exact_duplicate=False,
                       message="Uploaded. Extraction is queued.")


@router.get("/receipts")
def list_receipts(q: str | None = Query(default=None, max_length=100),
                  merchant: str | None = Query(default=None, max_length=100),
                  status: str | None = Query(default=None, description="Comma-separated statuses"),
                  date_from: date | None = None, date_to: date | None = None,
                  duplicates_only: bool = False,
                  limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0),
                  db: Session = Depends(get_db)):
    stmt = select(Receipt)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Receipt.merchant.ilike(like), Receipt.original_filename.ilike(like),
                              Receipt.transaction_reference.ilike(like)))
    if merchant:
        stmt = stmt.where(Receipt.merchant.ilike(f"%{merchant.strip()}%"))
    if status:
        wanted = [part.strip() for part in status.split(",") if part.strip()]
        valid = {st.value for st in ReceiptStatus}
        if any(w not in valid for w in wanted):
            raise ApiError(422, "invalid_status", f"Status must be one of: {', '.join(sorted(valid))}.")
        stmt = stmt.where(Receipt.status.in_(wanted))
    if date_from:
        stmt = stmt.where(Receipt.receipt_date >= date_from)
    if date_to:
        stmt = stmt.where(Receipt.receipt_date <= date_to)
    if duplicates_only:
        stmt = stmt.where(Receipt.duplicate_status.in_(["probable", "confirmed", "dismissed"]))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Receipt.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [s.summary(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/review-queue")
def review_queue(db: Session = Depends(get_db)):
    rows = db.scalars(select(Receipt).where(Receipt.status == ReceiptStatus.needs_review.value)
                      .order_by(Receipt.created_at)).all()
    return {"items": [s.summary(r) for r in rows], "total": len(rows)}


@router.get("/receipts/{receipt_id}", response_model=s.ReceiptDetail)
def get_receipt(receipt_id: str, db: Session = Depends(get_db)):
    return _detail(db, _load(db, receipt_id))


@router.get("/receipts/{receipt_id}/file")
def get_receipt_file(receipt_id: str, db: Session = Depends(get_db)):
    receipt = _load(db, receipt_id)
    try:
        content = get_storage(receipt.storage_backend).load(db, receipt.storage_key)
    except StorageError as exc:
        raise ApiError(404, "file_missing", "The original file is no longer available in storage.") from exc
    return Response(content, media_type=receipt.content_type, headers={
        "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600", "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.patch("/receipts/{receipt_id}/fields/{name}", response_model=s.ReceiptDetail)
def patch_field(receipt_id: str, name: str, body: s.FieldPatch, db: Session = Depends(get_db)):
    return _act(db, receipt_id, lambda r: review.correct_field(db, r, name, body.value, body.note))


@router.post("/receipts/{receipt_id}/fields/{name}/confirm", response_model=s.ReceiptDetail)
def confirm_field(receipt_id: str, name: str, db: Session = Depends(get_db)):
    return _act(db, receipt_id, lambda r: review.confirm_field(db, r, name))


@router.put("/receipts/{receipt_id}/line-items", response_model=s.ReceiptDetail)
def put_line_items(receipt_id: str, body: s.LineItemsPut, db: Session = Depends(get_db)):
    items = [item.model_dump() for item in body.items]
    return _act(db, receipt_id, lambda r: review.replace_line_items(db, r, items, body.note))


@router.post("/receipts/{receipt_id}/duplicate", response_model=s.ReceiptDetail)
def resolve_duplicate(receipt_id: str, body: s.DuplicateResolutionIn, db: Session = Depends(get_db)):
    return _act(db, receipt_id, lambda r: review.resolve_duplicate(db, r, body.resolution, body.note))


@router.post("/receipts/{receipt_id}/approve", response_model=s.ReceiptDetail)
def approve(receipt_id: str, body: s.NoteIn, db: Session = Depends(get_db)):
    return _act(db, receipt_id, lambda r: review.approve(db, r, body.note))


@router.post("/receipts/{receipt_id}/reject", response_model=s.ReceiptDetail)
def reject(receipt_id: str, body: s.NoteIn, db: Session = Depends(get_db)):
    return _act(db, receipt_id, lambda r: review.reject(db, r, body.note))


@router.post("/receipts/{receipt_id}/reprocess", response_model=s.ReceiptDetail)
def reprocess(receipt_id: str, body: s.NoteIn, db: Session = Depends(get_db)):
    return _act(db, receipt_id, lambda r: review.reprocess(db, r, body.note))


def _month_bounds(month: str) -> tuple[date, date]:
    try:
        start = datetime.strptime(month, "%Y-%m").date()
    except ValueError as exc:
        raise ApiError(422, "invalid_month", "Month must look like 2026-09.") from exc
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


def _expense_query(month: str | None, category: str | None):
    stmt = select(Expense)
    if month:
        start, end = _month_bounds(month)
        stmt = stmt.where(Expense.expense_date >= start, Expense.expense_date < end)
    if category:
        stmt = stmt.where(Expense.category == category)
    return stmt.order_by(Expense.expense_date.desc(), Expense.created_at.desc())


@router.get("/expenses")
def list_expenses(month: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    rows = db.scalars(_expense_query(month, category)).all()
    totals: dict[tuple[str, str], int] = defaultdict(int)
    by_currency: dict[str, int] = defaultdict(int)
    for row in rows:
        signed = -row.total_minor if row.kind == "refund" else row.total_minor
        totals[(row.currency, row.category)] += signed
        by_currency[row.currency] += signed
    months = db.execute(select(Expense.expense_date)).scalars().all()
    return {
        "items": [s.expense_out(r) for r in rows],
        "totals_by_category": [{"currency": c, "category": cat, "total_minor": v}
                               for (c, cat), v in sorted(totals.items(), key=lambda kv: (kv[0][0], -kv[1]))],
        "totals_by_currency": [{"currency": c, "total_minor": v} for c, v in sorted(by_currency.items())],
        "available_months": sorted({d.strftime("%Y-%m") for d in months}, reverse=True),
    }


@router.get("/expenses/export.csv")
def export_expenses(month: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    rows = db.scalars(_expense_query(month, category)).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "merchant", "category", "kind", "currency", "total", "tax", "approved_by", "receipt_id"])

    def safe(text: str) -> str:  # neutralise spreadsheet formula injection
        return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text

    for r in rows:
        writer.writerow([r.expense_date.isoformat(), safe(r.merchant), r.category, r.kind, r.currency,
                         from_minor(r.total_minor, r.currency),
                         "" if r.tax_minor is None else from_minor(r.tax_minor, r.currency), r.approved_by, str(r.receipt_id)])
    name = f"expenses-{month or 'all'}.csv"
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
    return ordered[index]


@router.get("/model-calls")
def model_calls(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    recent = db.scalars(select(ModelCall).order_by(ModelCall.created_at.desc()).limit(limit)).all()
    since = datetime.now(timezone.utc) - timedelta(days=30)
    window = db.scalars(select(ModelCall).where(ModelCall.created_at >= since)).all()
    ok = [c for c in window if c.status == "success"]
    errors: dict[str, int] = defaultdict(int)
    for c in window:
        if c.status != "success":
            errors[c.error_category or "unknown"] += 1
    with_tokens = [c for c in ok if c.input_tokens is not None]
    return {
        "window_days": 30,
        "stats": {
            "calls": len(window), "successes": len(ok), "failures": len(window) - len(ok),
            "success_rate": (len(ok) / len(window)) if window else None,
            "latency_p50_ms": _percentile([c.latency_ms for c in ok], 0.5),
            "latency_p95_ms": _percentile([c.latency_ms for c in ok], 0.95),
            "avg_input_tokens": round(sum(c.input_tokens for c in with_tokens) / len(with_tokens)) if with_tokens else None,
            "avg_output_tokens": round(sum((c.output_tokens or 0) for c in with_tokens) / len(with_tokens)) if with_tokens else None,
            "calls_with_token_usage": len(with_tokens),
            "errors_by_category": dict(errors),
        },
        "items": [s.model_call_out(c) for c in recent],
    }


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Receipt.status, func.count()).group_by(Receipt.status)).all())
    expenses = db.scalars(select(Expense)).all()
    today = date.today()
    month_start = today.replace(day=1)
    approved: dict[str, dict[str, int]] = defaultdict(lambda: {"all_time_minor": 0, "this_month_minor": 0, "count": 0})
    monthly: dict[tuple[str, str], int] = defaultdict(int)
    for e in expenses:
        signed = -e.total_minor if e.kind == "refund" else e.total_minor
        bucket = approved[e.currency]
        bucket["all_time_minor"] += signed
        bucket["count"] += 1
        if e.expense_date >= month_start:
            bucket["this_month_minor"] += signed
        monthly[(e.expense_date.strftime("%Y-%m"), e.currency)] += signed
    pending = db.execute(select(Receipt.currency, func.sum(Receipt.total_minor), func.count())
                         .where(Receipt.status == ReceiptStatus.needs_review.value, Receipt.currency.is_not(None),
                                Receipt.total_minor.is_not(None)).group_by(Receipt.currency)).all()
    recent_receipts = db.scalars(select(Receipt).order_by(Receipt.updated_at.desc()).limit(8)).all()
    recent_decisions = db.execute(select(ReviewDecision, Receipt.merchant, Receipt.original_filename)
                                  .join(Receipt, Receipt.id == ReviewDecision.receipt_id)
                                  .order_by(ReviewDecision.created_at.desc()).limit(8)).all()
    activity = [{"kind": "decision", "decision": d.decision, "receipt_id": str(d.receipt_id),
                 "label": merchant or filename, "note": d.note, "at": d.created_at}
                for d, merchant, filename in recent_decisions]
    decided = {a["receipt_id"] for a in activity}
    activity += [{"kind": "receipt", "status": r.status, "receipt_id": str(r.id), "label": r.merchant or r.original_filename,
                  "at": r.updated_at} for r in recent_receipts if str(r.id) not in decided]
    activity.sort(key=lambda a: a["at"], reverse=True)
    return {
        "counts": {st.value: int(counts.get(st.value, 0)) for st in ReceiptStatus},
        "total_receipts": int(sum(counts.values())),
        "approved_spend": [{"currency": c, **v} for c, v in sorted(approved.items(), key=lambda kv: (-kv[1]["count"], kv[0]))],
        "pending_review_totals": [{"currency": c, "total_minor": int(t or 0), "count": n} for c, t, n in pending],
        "monthly_approved": [{"month": m, "currency": c, "total_minor": v} for (m, c), v in sorted(monthly.items())][-24:],
        "recent_activity": activity[:10],
    }
