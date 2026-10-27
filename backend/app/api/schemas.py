"""API response/request models and serializers. Money is returned as integer minor units plus a decimal string."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.models import DuplicateStatus, Expense, Extraction, ModelCall, Receipt, ReceiptStatus
from app.services.money import from_minor
from app.services.receipt_data import FIELD_LABELS, FIELD_ORDER, MONEY_FIELDS, REQUIRED_FIELDS


def _dec(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(Decimal(value).normalize(), "f")


class ReceiptSummary(BaseModel):
    id: str
    original_filename: str
    content_type: str
    page_count: int | None
    status: str
    merchant: str | None
    receipt_date: date | None
    currency: str | None
    total_minor: int | None
    total: str | None
    review_reason_count: int
    duplicate_status: str
    approved_by: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class FieldOut(BaseModel):
    name: str
    label: str
    required: bool
    money: bool
    value: str | None
    extracted_value: str | None
    source_text: str | None
    status: str
    reason: str | None


class LineItemOut(BaseModel):
    position: int
    description: str | None
    quantity: str | None
    unit_price: str | None
    line_total: str | None
    discount: str | None
    tax: str | None
    source_text: str | None
    status: str
    reason: str | None


class CheckOut(BaseModel):
    code: str
    severity: str
    passed: bool
    fields: list[str]
    message: str
    details: dict | None


class ExtractionOut(BaseModel):
    id: str
    version: int
    provider: str
    model: str
    prompt_version: str
    passes: int
    schema_valid: bool
    readability: str | None
    is_receipt: bool | None
    tax_included: bool | None
    category_suggestion: str | None
    notes: list[str]
    latency_ms: int | None
    trace_id: str
    created_at: datetime


class CorrectionOut(BaseModel):
    field: str
    action: str
    old_value: object | None
    new_value: object | None
    note: str | None
    created_at: datetime


class DecisionOut(BaseModel):
    decision: str
    note: str | None
    overridden_checks: list | None
    created_at: datetime


class ModelCallOut(BaseModel):
    trace_id: str
    receipt_id: str | None
    provider: str
    model: str
    prompt_version: str
    attempt: int
    status: str
    error_category: str | None
    error_message: str | None
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    created_at: datetime


class DuplicateOut(BaseModel):
    status: str
    reasons: list[str]
    of: ReceiptSummary | None


class ExpenseOut(BaseModel):
    id: str
    receipt_id: str
    merchant: str
    expense_date: date
    currency: str
    total_minor: int
    total: str
    tax_minor: int | None
    kind: str
    category: str
    approved_by: str
    created_at: datetime


class ReceiptDetail(BaseModel):
    receipt: ReceiptSummary
    size_bytes: int
    sha256: str
    review_reasons: list[dict]
    duplicate: DuplicateOut
    extraction: ExtractionOut | None
    fields: list[FieldOut]
    line_items: list[LineItemOut]
    checks: list[CheckOut]
    corrections: list[CorrectionOut]
    decisions: list[DecisionOut]
    model_calls: list[ModelCallOut]
    extraction_history: list[dict]
    expense: ExpenseOut | None
    allowed_actions: list[str]


class UploadOut(BaseModel):
    receipt: ReceiptSummary
    created: bool
    exact_duplicate: bool
    message: str


class FieldPatch(BaseModel):
    value: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=1000)


class LineItemIn(BaseModel):
    description: str | None = Field(default=None, max_length=300)
    quantity: str | None = Field(default=None, max_length=40)
    unit_price: str | None = Field(default=None, max_length=40)
    line_total: str | None = Field(default=None, max_length=40)
    discount: str | None = Field(default=None, max_length=40)
    tax: str | None = Field(default=None, max_length=40)


class LineItemsPut(BaseModel):
    items: list[LineItemIn] = Field(max_length=200)
    note: str | None = Field(default=None, max_length=1000)


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class DuplicateResolutionIn(BaseModel):
    resolution: Literal["duplicate", "not_duplicate"]
    note: str | None = Field(default=None, max_length=1000)


def summary(receipt: Receipt) -> ReceiptSummary:
    total = from_minor(receipt.total_minor, receipt.currency)
    return ReceiptSummary(
        id=str(receipt.id), original_filename=receipt.original_filename, content_type=receipt.content_type,
        page_count=receipt.page_count, status=receipt.status, merchant=receipt.merchant,
        receipt_date=receipt.receipt_date, currency=receipt.currency, total_minor=receipt.total_minor,
        total=None if total is None else str(total), review_reason_count=len(receipt.review_reasons or []),
        duplicate_status=receipt.duplicate_status, approved_by=receipt.approved_by, error_code=receipt.error_code,
        error_message=receipt.error_message, created_at=receipt.created_at, updated_at=receipt.updated_at)


def model_call_out(call: ModelCall) -> ModelCallOut:
    return ModelCallOut(
        trace_id=call.trace_id, receipt_id=str(call.receipt_id) if call.receipt_id else None, provider=call.provider,
        model=call.model, prompt_version=call.prompt_version, attempt=call.attempt, status=call.status,
        error_category=call.error_category, error_message=call.error_message, finish_reason=call.finish_reason,
        input_tokens=call.input_tokens, output_tokens=call.output_tokens, thinking_tokens=call.thinking_tokens,
        total_tokens=call.total_tokens, latency_ms=call.latency_ms, created_at=call.created_at)


def expense_out(expense: Expense) -> ExpenseOut:
    return ExpenseOut(
        id=str(expense.id), receipt_id=str(expense.receipt_id), merchant=expense.merchant,
        expense_date=expense.expense_date, currency=expense.currency, total_minor=expense.total_minor,
        total=str(from_minor(expense.total_minor, expense.currency)), tax_minor=expense.tax_minor, kind=expense.kind,
        category=expense.category, approved_by=expense.approved_by, created_at=expense.created_at)


def allowed_actions(receipt: Receipt) -> list[str]:
    status = receipt.status
    actions: list[str] = []
    if status == ReceiptStatus.needs_review.value:
        actions += ["edit", "approve", "reject", "reprocess"]
        if receipt.duplicate_status == DuplicateStatus.probable.value:
            actions.append("resolve_duplicate")
    elif status == ReceiptStatus.failed.value:
        actions += ["reprocess", "reject"]
    return actions


def detail(receipt: Receipt, duplicate_of: Receipt | None, calls: list[ModelCall]) -> ReceiptDetail:
    extraction: Extraction | None = receipt.current_extraction
    fields: list[FieldOut] = []
    lines: list[LineItemOut] = []
    checks: list[CheckOut] = []
    extraction_out = None
    if extraction is not None:
        by_name = {f.name: f for f in extraction.fields}
        for name in FIELD_ORDER:
            row = by_name.get(name)
            if row is None:
                continue
            fields.append(FieldOut(name=name, label=FIELD_LABELS[name], required=name in REQUIRED_FIELDS,
                                   money=name in MONEY_FIELDS, value=row.value, extracted_value=row.extracted_value,
                                   source_text=row.source_text, status=row.status, reason=row.reason))
        lines = [LineItemOut(position=i.position, description=i.description, quantity=_dec(i.quantity),
                             unit_price=_dec(i.unit_price), line_total=_dec(i.line_total), discount=_dec(i.discount),
                             tax=_dec(i.tax), source_text=i.source_text, status=i.status, reason=i.reason)
                 for i in extraction.line_items]
        order = {"error": 0, "warning": 1, "info": 2}
        checks = sorted((CheckOut(code=c.code, severity=c.severity, passed=c.passed, fields=c.fields or [],
                                  message=c.message, details=c.details) for c in extraction.checks),
                        key=lambda c: (c.passed, order.get(c.severity, 3)))
        extraction_out = ExtractionOut(
            id=str(extraction.id), version=extraction.version, provider=extraction.provider, model=extraction.model,
            prompt_version=extraction.prompt_version, passes=extraction.passes, schema_valid=extraction.schema_valid,
            readability=extraction.readability, is_receipt=extraction.is_receipt, tax_included=extraction.tax_included,
            category_suggestion=extraction.category, notes=extraction.notes or [], latency_ms=extraction.latency_ms,
            trace_id=extraction.trace_id, created_at=extraction.created_at)
    history = [{"version": e.version, "model": e.model, "prompt_version": e.prompt_version,
                "schema_valid": e.schema_valid, "created_at": e.created_at,
                "schema_errors": e.schema_errors, "current": e.id == receipt.current_extraction_id}
               for e in receipt.extractions]
    return ReceiptDetail(
        receipt=summary(receipt), size_bytes=receipt.size_bytes, sha256=receipt.sha256,
        review_reasons=receipt.review_reasons or [],
        duplicate=DuplicateOut(status=receipt.duplicate_status, reasons=receipt.duplicate_reasons or [],
                               of=summary(duplicate_of) if duplicate_of else None),
        extraction=extraction_out, fields=fields, line_items=lines, checks=checks,
        corrections=[CorrectionOut(field=c.field, action=c.action, old_value=c.old_value, new_value=c.new_value,
                                   note=c.note, created_at=c.created_at) for c in receipt.corrections],
        decisions=[DecisionOut(decision=d.decision, note=d.note, overridden_checks=d.overridden_checks,
                               created_at=d.created_at) for d in receipt.decisions],
        model_calls=[model_call_out(c) for c in calls], extraction_history=history,
        expense=expense_out(receipt.expense) if receipt.expense else None, allowed_actions=allowed_actions(receipt))

