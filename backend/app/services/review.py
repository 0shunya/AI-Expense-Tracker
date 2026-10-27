"""Human-in-the-loop review: corrections, confirmations, duplicate resolution, approval and rejection.

Every action is logged (``corrections`` / ``review_decisions``) so human fixes double as labelled data.
Unreviewed values are never shown as verified: only a reviewer's confirmation, correction or approval sets
a field to ``verified``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import (Correction, Decision, DuplicateStatus, Expense, Extraction, FieldStatus, LineItem, Receipt,
                        ReceiptStatus, ReviewDecision, utcnow)
from app.services.analysis import data_from_db, run_analysis
from app.services.categories import categorize
from app.services.money import MoneyParseError, parse_decimal, parse_minor
from app.services.receipt_data import FIELD_LABELS, FIELD_ORDER, REQUIRED_FIELDS, normalize_field


class ReviewError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def _require(receipt: Receipt, *statuses: ReceiptStatus) -> None:
    if receipt.status not in {s.value for s in statuses}:
        allowed = " or ".join(s.value.replace("_", " ") for s in statuses)
        raise ReviewError("invalid_state", f"This action needs a receipt that is {allowed}; it is {receipt.status.replace('_', ' ')}.")


def _extraction(receipt: Receipt) -> Extraction:
    if receipt.current_extraction is None:
        raise ReviewError("no_extraction", "This receipt has no extraction to review.")
    return receipt.current_extraction


def _refresh(db: Session, receipt: Receipt) -> None:
    extraction = _extraction(receipt)
    run_analysis(db, receipt, extraction, data_from_db(extraction))
    receipt.updated_at = utcnow()


def correct_field(db: Session, receipt: Receipt, name: str, value: str | None, note: str | None = None) -> None:
    _require(receipt, ReceiptStatus.needs_review)
    if name not in FIELD_ORDER:
        raise ReviewError("unknown_field", f"'{name}' is not an editable receipt field.", 404)
    extraction = _extraction(receipt)
    row = next((f for f in extraction.fields if f.name == name), None)
    if row is None:
        raise ReviewError("unknown_field", f"'{name}' is not present on this extraction.", 404)
    canonical, error = normalize_field(name, value)
    if error:
        raise ReviewError("invalid_value", error, 422)
    if name in REQUIRED_FIELDS and canonical is None:
        raise ReviewError("required_field", f"{FIELD_LABELS[name]} is required and cannot be cleared.", 422)
    old = row.value
    db.add(Correction(receipt_id=receipt.id, extraction_id=extraction.id, field=name,
                      action="corrected" if canonical != old else "confirmed",
                      old_value=old, new_value=canonical, note=note))
    row.value = canonical
    row.status = FieldStatus.verified.value
    row.reason = "Corrected by reviewer." if canonical != old else "Confirmed by reviewer."
    _refresh(db, receipt)


def confirm_field(db: Session, receipt: Receipt, name: str) -> None:
    _require(receipt, ReceiptStatus.needs_review)
    extraction = _extraction(receipt)
    row = next((f for f in extraction.fields if f.name == name), None)
    if row is None:
        raise ReviewError("unknown_field", f"'{name}' is not an editable receipt field.", 404)
    if row.status == FieldStatus.invalid.value:
        raise ReviewError("invalid_value", f"{FIELD_LABELS[name]} is invalid; correct it instead of confirming.", 422)
    db.add(Correction(receipt_id=receipt.id, extraction_id=extraction.id, field=name, action="confirmed",
                      old_value=row.value, new_value=row.value))
    row.status = FieldStatus.verified.value
    row.reason = "Confirmed by reviewer."
    _refresh(db, receipt)


def _decimal(raw, label: str) -> Decimal | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return parse_decimal(str(raw))
    except (MoneyParseError, InvalidOperation) as exc:
        raise ReviewError("invalid_value", f"{label} '{raw}' is not a number.", 422) from exc


def replace_line_items(db: Session, receipt: Receipt, items: list[dict], note: str | None = None) -> None:
    _require(receipt, ReceiptStatus.needs_review)
    extraction = _extraction(receipt)
    if len(items) > 200:
        raise ReviewError("too_many_items", "A receipt can have at most 200 line items.", 422)
    parsed: list[LineItem] = []
    for index, item in enumerate(items):
        label = f"Line {index + 1}"
        line = LineItem(position=index, description=(item.get("description") or "").strip()[:300] or None,
                        quantity=_decimal(item.get("quantity"), f"{label} quantity"),
                        unit_price=_decimal(item.get("unit_price"), f"{label} unit price"),
                        line_total=_decimal(item.get("line_total"), f"{label} line total"),
                        discount=_decimal(item.get("discount"), f"{label} discount"),
                        tax=_decimal(item.get("tax"), f"{label} tax"),
                        status=FieldStatus.verified.value, reason="Edited by reviewer.")
        if line.description is None and line.line_total is None:
            raise ReviewError("invalid_value", f"{label} needs a description or an amount.", 422)
        parsed.append(line)
    old = [_line_dict(line) for line in extraction.line_items]
    new = [_line_dict(line) for line in parsed]
    db.add(Correction(receipt_id=receipt.id, extraction_id=extraction.id, field="line_items",
                      action="corrected" if old != new else "confirmed", old_value=old, new_value=new, note=note))
    extraction.line_items.clear()
    db.flush()
    extraction.line_items.extend(parsed)
    db.flush()
    _refresh(db, receipt)


def _line_dict(line: LineItem) -> dict:
    def s(v):
        return None if v is None else format(Decimal(v).normalize(), "f")
    return {"description": line.description, "quantity": s(line.quantity), "unit_price": s(line.unit_price),
            "line_total": s(line.line_total), "discount": s(line.discount), "tax": s(line.tax)}


def resolve_duplicate(db: Session, receipt: Receipt, resolution: str, note: str | None = None) -> None:
    _require(receipt, ReceiptStatus.needs_review)
    if receipt.duplicate_status != DuplicateStatus.probable.value:
        raise ReviewError("no_duplicate_flag", "This receipt has no open duplicate warning.")
    if resolution == "not_duplicate":
        receipt.duplicate_status = DuplicateStatus.dismissed.value
        db.add(ReviewDecision(receipt_id=receipt.id, decision=Decision.duplicate_dismissed.value, note=note))
        _refresh(db, receipt)
    elif resolution == "duplicate":
        receipt.duplicate_status = DuplicateStatus.confirmed.value
        receipt.status = ReceiptStatus.rejected.value
        db.add(ReviewDecision(receipt_id=receipt.id, decision=Decision.duplicate_confirmed.value, note=note))
    else:
        raise ReviewError("invalid_resolution", "Resolution must be 'duplicate' or 'not_duplicate'.", 422)
    receipt.updated_at = utcnow()


def approve(db: Session, receipt: Receipt, note: str | None = None, by: str = "reviewer") -> None:
    _require(receipt, ReceiptStatus.needs_review)
    extraction = _extraction(receipt)
    assessment = run_analysis(db, receipt, extraction, data_from_db(extraction))
    blocking = []
    for name in REQUIRED_FIELDS:
        if assessment.fields[name].status in (FieldStatus.missing.value, FieldStatus.invalid.value):
            blocking.append(f"{FIELD_LABELS[name]} is {assessment.fields[name].status}.")
    for name, result in assessment.fields.items():
        if result.status == FieldStatus.invalid.value and name not in REQUIRED_FIELDS:
            blocking.append(f"{FIELD_LABELS[name]} is invalid ({result.reason}). Correct or clear it.")
    if any(line.status == FieldStatus.invalid.value for line in extraction.line_items):
        blocking.append("A line item has an unreadable number. Edit the line items.")
    if receipt.duplicate_status == DuplicateStatus.probable.value:
        blocking.append("Resolve the duplicate warning first.")
    if blocking:
        raise ReviewError("approval_blocked", "This receipt cannot be approved yet.", 409, {"blocking": blocking})

    failing = [c for c in extraction.checks if not c.passed and c.severity in ("error", "warning")]
    overridden = [{"code": c.code, "message": c.message} for c in failing]
    if failing and by == "reviewer" and not (note and note.strip()):
        raise ReviewError("override_note_required",
                          "Some checks failed. Add a note explaining why the values are correct as entered.",
                          409, {"failed_checks": overridden})

    try:
        total_value = next(f.value for f in extraction.fields if f.name == "total")
        total_minor = parse_minor(total_value, receipt.currency)
        tax_value = next((f.value for f in extraction.fields if f.name == "tax"), None)
        tax_minor = parse_minor(tax_value, receipt.currency) if tax_value else None
    except MoneyParseError as exc:
        raise ReviewError("approval_blocked", str(exc), 409, {"blocking": [str(exc)]}) from exc
    category, _source = categorize(receipt.merchant, [line.description for line in extraction.line_items], extraction.category)
    expense = receipt.expense or Expense(receipt_id=receipt.id)
    expense.merchant = receipt.merchant
    expense.expense_date = receipt.receipt_date
    expense.currency = receipt.currency
    expense.total_minor = abs(total_minor)
    expense.tax_minor = abs(tax_minor) if tax_minor is not None else None
    expense.kind = "refund" if total_minor < 0 else "purchase"
    expense.category = category
    expense.approved_by = by
    receipt.expense = expense

    if by == "reviewer":
        for row in extraction.fields:
            if row.value is not None and row.status != FieldStatus.verified.value:
                row.status, row.reason = FieldStatus.verified.value, "Approved by reviewer."
        for line in extraction.line_items:
            if line.status != FieldStatus.verified.value:
                line.status, line.reason = FieldStatus.verified.value, "Approved by reviewer."
    receipt.status = ReceiptStatus.approved.value
    receipt.approved_at = utcnow()
    receipt.approved_by = by
    db.add(ReviewDecision(receipt_id=receipt.id, decision=Decision.approved.value, note=note,
                          overridden_checks=overridden or None))
    db.flush()


def reject(db: Session, receipt: Receipt, note: str | None = None) -> None:
    _require(receipt, ReceiptStatus.needs_review, ReceiptStatus.failed)
    receipt.status = ReceiptStatus.rejected.value
    receipt.locked_at = None
    receipt.updated_at = utcnow()
    db.add(ReviewDecision(receipt_id=receipt.id, decision=Decision.rejected.value, note=note))


def reprocess(db: Session, receipt: Receipt, note: str | None = None) -> None:
    """Queue a new extraction version. Earlier extractions and corrections are kept as history."""
    _require(receipt, ReceiptStatus.failed, ReceiptStatus.needs_review)
    receipt.status = ReceiptStatus.queued.value
    receipt.processing_attempts = 0
    receipt.error_code = receipt.error_message = None
    receipt.locked_at = None
    receipt.updated_at = utcnow()
    db.add(ReviewDecision(receipt_id=receipt.id, decision=Decision.reprocess.value, note=note))
