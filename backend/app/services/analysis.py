"""Validation + uncertainty + duplicate analysis for one receipt, and persistence of the results.

Used by the extraction pipeline (after a model call) and by the review service (after every human edit),
so the rules are applied identically in both places.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (DuplicateStatus, Extraction, ExtractionField, FieldStatus, LineItem, Receipt,
                        ReceiptStatus, ValidationCheck)
from app.services.duplicates import DuplicateKey, DuplicateMatch, find_duplicates
from app.services.money import MoneyParseError, parse_minor
from app.services.receipt_data import FIELD_ORDER, Line, ReceiptData, normalize_field
from app.services.uncertainty import Assessment, assess
from app.services.validation import Check, validate


def analyze(data: ReceiptData, *, is_receipt: bool, readability: str, disagreements: dict[str, str] | None = None,
            duplicate_reasons: list[str] | None = None, today: date | None = None) -> tuple[list[Check], Assessment]:
    checks = validate(data, today=today, max_age_days=get_settings().receipt_max_age_days)
    assessment = assess(data, checks, is_receipt=is_receipt, readability=readability,
                        disagreements=disagreements, duplicate_reasons=duplicate_reasons)
    return checks, assessment


def data_from_db(extraction: Extraction) -> ReceiptData:
    data = ReceiptData(tax_included=extraction.tax_included)
    for row in extraction.fields:
        value, error = normalize_field(row.name, row.value)
        data.values[row.name] = value
        data.source_text[row.name] = row.source_text
        if error:
            data.parse_errors[row.name] = error
    for name in FIELD_ORDER:
        data.values.setdefault(name, None)
    for item in extraction.line_items:
        data.lines.append(Line(description=item.description, quantity=item.quantity, unit_price=item.unit_price,
                               line_total=item.line_total, discount=item.discount, tax=item.tax,
                               source_text=item.source_text))
    return data


def duplicate_key(receipt: Receipt) -> DuplicateKey:
    return DuplicateKey(str(receipt.id), receipt.merchant, receipt.receipt_date, receipt.currency,
                        receipt.total_minor, receipt.transaction_reference)


def find_receipt_duplicates(db: Session, receipt: Receipt) -> list[DuplicateMatch]:
    """Indexed candidate lookup (same currency+total, or same reference), then explainable comparison."""
    conditions = []
    if receipt.currency and receipt.total_minor is not None:
        conditions.append((Receipt.currency == receipt.currency) & (Receipt.total_minor == receipt.total_minor))
    if receipt.transaction_reference:
        conditions.append(Receipt.transaction_reference == receipt.transaction_reference)
    if not conditions:
        return []
    rows = db.scalars(select(Receipt).where(
        Receipt.id != receipt.id,
        Receipt.status.in_([ReceiptStatus.needs_review.value, ReceiptStatus.approved.value]),
        Receipt.duplicate_status != DuplicateStatus.confirmed.value,
        or_(*conditions),
    ).order_by(Receipt.created_at).limit(50)).all()
    matches = find_duplicates(duplicate_key(receipt), [duplicate_key(r) for r in rows])
    if receipt.duplicate_status == DuplicateStatus.dismissed.value and receipt.duplicate_of_id:
        matches = [m for m in matches if m.candidate_id != str(receipt.duplicate_of_id)]
    return matches


def update_summary(receipt: Receipt, data: ReceiptData) -> None:
    receipt.merchant = data.get("merchant")
    receipt.receipt_date = data.date
    receipt.currency = data.currency
    try:
        receipt.total_minor = parse_minor(data.get("total"), data.currency) if "total" not in data.parse_errors else None
    except MoneyParseError:
        receipt.total_minor = None
    reference = data.get("transaction_reference")
    receipt.transaction_reference = reference[:128] if reference else None


def run_analysis(db: Session, receipt: Receipt, extraction: Extraction, data: ReceiptData, *,
                 disagreements: dict[str, str] | None = None) -> Assessment:
    """Re-derive checks, field statuses, duplicate flags and review reasons, and store them."""
    update_summary(receipt, data)
    db.flush()
    duplicate_reasons: list[str] = []
    if receipt.duplicate_status != DuplicateStatus.confirmed.value:
        matches = find_receipt_duplicates(db, receipt)
        if matches:
            best = matches[0]
            receipt.duplicate_status = DuplicateStatus.probable.value
            receipt.duplicate_of_id = uuid.UUID(best.candidate_id)
            receipt.duplicate_reasons = best.reasons
            duplicate_reasons = [f"Possible duplicate of an earlier receipt: {' '.join(best.reasons)}"]
        elif receipt.duplicate_status == DuplicateStatus.probable.value:
            receipt.duplicate_status, receipt.duplicate_of_id, receipt.duplicate_reasons = DuplicateStatus.none.value, None, []

    checks, assessment = analyze(data, is_receipt=extraction.is_receipt is not False,
                                 readability=extraction.readability or "clear", disagreements=disagreements,
                                 duplicate_reasons=duplicate_reasons)

    by_name = {f.name: f for f in extraction.fields}
    for name, result in assessment.fields.items():
        row = by_name.get(name)
        if row is None:
            row = ExtractionField(name=name, value=None, extracted_value=None, status=result.status)
            extraction.fields.append(row)
        if row.status == FieldStatus.verified.value and result.status != FieldStatus.invalid.value:
            continue  # a human confirmed it; rules still show their results as checks
        row.status, row.reason = result.status, result.reason
    for item, result in zip(extraction.line_items, assessment.lines):
        if item.status == FieldStatus.verified.value and result.status != FieldStatus.invalid.value:
            continue
        item.status, item.reason = result.status, result.reason

    extraction.checks.clear()
    for check in checks:
        extraction.checks.append(ValidationCheck(code=check.code, severity=check.severity, passed=check.passed,
                                                 fields=check.fields, message=check.message, details=check.details))
    receipt.review_reasons = assessment.reasons
    return assessment


def line_from_input(raw: dict) -> LineItem:
    def dec(key: str) -> Decimal | None:
        value = raw.get(key)
        return None if value in (None, "") else Decimal(str(value))
    return LineItem(description=raw.get("description"), quantity=dec("quantity"), unit_price=dec("unit_price"),
                    line_total=dec("line_total"), discount=dec("discount"), tax=dec("tax"),
                    source_text=raw.get("source_text"))
