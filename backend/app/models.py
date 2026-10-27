"""Relational schema. Money is always integer minor units + ISO 4217 code; never floats.

Extractions are versioned rows: re-running a model never overwrites earlier output. Fields carry a
status and a reason, which is what the review UI and the evaluation metrics read.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index,
                        Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, Uuid)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _in(column: str, enum_cls: type[enum.Enum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class ReceiptStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    needs_review = "needs_review"
    approved = "approved"
    rejected = "rejected"
    failed = "failed"


class FieldStatus(str, enum.Enum):
    extracted = "extracted"   # read by the model and passed every check (still unreviewed)
    uncertain = "uncertain"   # readable, but a rule, ambiguity or disagreement casts doubt on it
    invalid = "invalid"       # value failed deterministic parsing (bad date, unknown currency, non-numeric)
    missing = "missing"       # null: absent or unreadable on the document
    verified = "verified"     # confirmed or corrected by a human reviewer


class DuplicateStatus(str, enum.Enum):
    none = "none"
    probable = "probable"
    dismissed = "dismissed"   # reviewer decided it is a separate expense
    confirmed = "confirmed"   # reviewer decided it is a duplicate (receipt is rejected)


class CheckSeverity(str, enum.Enum):
    error = "error"
    warning = "warning"
    info = "info"


class Decision(str, enum.Enum):
    approved = "approved"
    rejected = "rejected"
    duplicate_dismissed = "duplicate_dismissed"
    duplicate_confirmed = "duplicate_confirmed"
    reprocess = "reprocess"


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        CheckConstraint(_in("status", ReceiptStatus), name="ck_receipts_status"),
        CheckConstraint(_in("duplicate_status", DuplicateStatus), name="ck_receipts_duplicate_status"),
        CheckConstraint("currency IS NULL OR (length(currency) = 3 AND upper(currency) = currency)", name="ck_receipts_currency"),
        CheckConstraint("size_bytes > 0", name="ck_receipts_size"),
        Index("ix_receipts_status_created", "status", "created_at"),
        Index("ix_receipts_dup_lookup", "currency", "total_minor", "receipt_date"),
        Index("ix_receipts_merchant", "merchant"),
        Index("ix_receipts_reference", "transaction_reference"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    storage_backend: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)

    status: Mapped[str] = mapped_column(String(16), default=ReceiptStatus.queued.value)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extractions.id", ondelete="SET NULL", use_alter=True, name="fk_receipts_current_extraction"))

    # Effective values (current extraction + human corrections), denormalised for search and duplicate lookups.
    merchant: Mapped[str | None] = mapped_column(String(255))
    receipt_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(3))
    total_minor: Mapped[int | None] = mapped_column(BigInteger)
    transaction_reference: Mapped[str | None] = mapped_column(String(128))

    review_reasons: Mapped[list] = mapped_column(JSONType, default=list)
    duplicate_status: Mapped[str] = mapped_column(String(16), default=DuplicateStatus.none.value)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("receipts.id", ondelete="SET NULL"))
    duplicate_reasons: Mapped[list] = mapped_column(JSONType, default=list)

    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", foreign_keys="Extraction.receipt_id",
        order_by="Extraction.version")
    current_extraction: Mapped[Extraction | None] = relationship(foreign_keys=[current_extraction_id], post_update=True)
    corrections: Mapped[list[Correction]] = relationship(back_populates="receipt", cascade="all, delete-orphan",
                                                         order_by="Correction.created_at")
    decisions: Mapped[list[ReviewDecision]] = relationship(back_populates="receipt", cascade="all, delete-orphan",
                                                           order_by="ReviewDecision.created_at")
    expense: Mapped[Expense | None] = relationship(back_populates="receipt", cascade="all, delete-orphan", uselist=False)


class Extraction(Base):
    __tablename__ = "extractions"
    __table_args__ = (UniqueConstraint("receipt_id", "version", name="uq_extractions_receipt_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(36))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    passes: Mapped[int] = mapped_column(Integer, default=1)
    schema_valid: Mapped[bool] = mapped_column(Boolean)
    schema_errors: Mapped[list | None] = mapped_column(JSONType)
    raw_output: Mapped[dict | None] = mapped_column(JSONType)
    readability: Mapped[str | None] = mapped_column(String(24))
    is_receipt: Mapped[bool | None] = mapped_column(Boolean)
    tax_included: Mapped[bool | None] = mapped_column(Boolean)
    category: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[list | None] = mapped_column(JSONType)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="extractions", foreign_keys=[receipt_id])
    fields: Mapped[list[ExtractionField]] = relationship(back_populates="extraction", cascade="all, delete-orphan")
    line_items: Mapped[list[LineItem]] = relationship(back_populates="extraction", cascade="all, delete-orphan",
                                                      order_by="LineItem.position")
    checks: Mapped[list[ValidationCheck]] = relationship(back_populates="extraction", cascade="all, delete-orphan")


class ExtractionField(Base):
    __tablename__ = "extraction_fields"
    __table_args__ = (
        UniqueConstraint("extraction_id", "name", name="uq_fields_extraction_name"),
        CheckConstraint(_in("status", FieldStatus), name="ck_fields_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extractions.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    extracted_value: Mapped[str | None] = mapped_column(Text)   # what the model returned (normalised); never edited
    value: Mapped[str | None] = mapped_column(Text)             # effective value after corrections
    source_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)

    extraction: Mapped[Extraction] = relationship(back_populates="fields")


class LineItem(Base):
    __tablename__ = "line_items"
    __table_args__ = (
        UniqueConstraint("extraction_id", "position", name="uq_line_items_position"),
        CheckConstraint(_in("status", FieldStatus), name="ck_line_items_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extractions.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    # Exact decimals in major units: unit prices such as fuel at 1.299/L are not representable in minor units.
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    discount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    source_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=FieldStatus.extracted.value)
    reason: Mapped[str | None] = mapped_column(Text)

    extraction: Mapped[Extraction] = relationship(back_populates="line_items")


class ValidationCheck(Base):
    __tablename__ = "validation_checks"
    __table_args__ = (CheckConstraint(_in("severity", CheckSeverity), name="ck_checks_severity"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extractions.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(8))
    passed: Mapped[bool] = mapped_column(Boolean)
    fields: Mapped[list] = mapped_column(JSONType, default=list)
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    extraction: Mapped[Extraction] = relationship(back_populates="checks")


class Correction(Base):
    """One human edit or confirmation. Old and new values are kept so corrections become labelled eval data."""

    __tablename__ = "corrections"
    __table_args__ = (
        CheckConstraint("action IN ('corrected', 'confirmed')", name="ck_corrections_action"),
        Index("ix_corrections_receipt_created", "receipt_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"))
    extraction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extractions.id", ondelete="CASCADE"))
    field: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(16))
    old_value: Mapped[dict | list | str | None] = mapped_column(JSONType)
    new_value: Mapped[dict | list | str | None] = mapped_column(JSONType)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="corrections")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (CheckConstraint(_in("decision", Decision), name="ck_decisions_decision"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(Text)
    overridden_checks: Mapped[list | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="decisions")


class Expense(Base):
    """The tracker row written when a receipt is approved. One per receipt, enforced by the database."""

    __tablename__ = "expenses"
    __table_args__ = (
        UniqueConstraint("receipt_id", name="uq_expenses_receipt"),
        CheckConstraint("total_minor >= 0", name="ck_expenses_total_nonnegative"),
        CheckConstraint("kind IN ('purchase', 'refund')", name="ck_expenses_kind"),
        CheckConstraint("length(currency) = 3 AND upper(currency) = currency", name="ck_expenses_currency"),
        Index("ix_expenses_date", "expense_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"))
    merchant: Mapped[str] = mapped_column(String(255))
    expense_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    total_minor: Mapped[int] = mapped_column(BigInteger)
    tax_minor: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(10), default="purchase")
    category: Mapped[str] = mapped_column(String(40))
    approved_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="expense")


class ModelCall(Base):
    """One row per provider attempt — successes and failures — written outside the pipeline transaction."""

    __tablename__ = "model_calls"
    __table_args__ = (Index("ix_model_calls_created", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("receipts.id", ondelete="SET NULL"), index=True)
    feature: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    error_category: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(String(300))
    finish_reason: Mapped[str | None] = mapped_column(String(40))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    thinking_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StoredFile(Base):
    """Receipt originals when STORAGE_BACKEND=database (persists across redeploys on ephemeral hosts)."""

    __tablename__ = "stored_files"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
