"""Normalisation of schema-valid model output into typed receipt data.

Schema-valid only means the JSON has the right shape. This module parses every value with deterministic
rules (Decimal money, ISO dates, ISO 4217 codes) and records a parse error instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.llm.schema import ReceiptExtraction
from app.services.money import MoneyParseError, is_valid_currency, parse_decimal, parse_quantity, to_minor

MONEY_FIELDS = ("subtotal", "tax", "tip", "discount", "service_charge", "total")
FIELD_ORDER = ("merchant", "merchant_address", "date", "time", "currency", *MONEY_FIELDS,
               "payment_method", "transaction_reference", "gstin")
REQUIRED_FIELDS = ("merchant", "date", "currency", "total")
FIELD_LABELS = {
    "merchant": "Merchant", "merchant_address": "Address", "date": "Date", "time": "Time",
    "currency": "Currency", "subtotal": "Subtotal", "tax": "Tax", "tip": "Tip", "discount": "Discount",
    "service_charge": "Service charge", "total": "Total", "payment_method": "Payment method",
    "transaction_reference": "Reference", "gstin": "GSTIN",
}
_SCHEMA_KEYS = {"merchant": "merchant_name", "date": "transaction_date", "time": "transaction_time"}
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


@dataclass
class Line:
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    discount: Decimal | None = None
    tax: Decimal | None = None
    source_text: str | None = None
    parse_errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"description": self.description,
                "quantity": None if self.quantity is None else str(self.quantity),
                **{k: None if getattr(self, k) is None else str(getattr(self, k))
                   for k in ("unit_price", "line_total", "discount", "tax")}}


@dataclass
class ReceiptData:
    """Effective receipt values as canonical strings, plus why any value could not be parsed."""

    values: dict[str, str | None] = field(default_factory=dict)
    source_text: dict[str, str | None] = field(default_factory=dict)
    parse_errors: dict[str, str] = field(default_factory=dict)
    lines: list[Line] = field(default_factory=list)
    tax_included: bool | None = None

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    @property
    def currency(self) -> str | None:
        code = self.values.get("currency")
        return code if is_valid_currency(code) else None

    def money(self, name: str) -> Decimal | None:
        if name in self.parse_errors:
            return None
        raw = self.values.get(name)
        return None if raw is None else Decimal(raw)

    def minor(self, name: str) -> int | None:
        value = self.money(name)
        try:
            return to_minor(value, self.currency)
        except MoneyParseError:
            return None

    @property
    def date(self) -> date | None:
        raw = self.values.get("date")
        if raw is None or "date" in self.parse_errors:
            return None
        return date.fromisoformat(raw)


def _clean(text: str | None, limit: int = 500) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text[:limit] or None


def normalize_field(name: str, raw: str | None) -> tuple[str | None, str | None]:
    """Return (canonical value, parse error). Money is canonicalised to a plain decimal string."""
    value = _clean(raw)
    if value is None:
        return None, None
    if name in MONEY_FIELDS:
        try:
            amount = parse_decimal(value)
        except MoneyParseError:
            return value, f"'{value}' is not a readable amount."
        return (None, None) if amount is None else (format(amount, "f"), None)
    if name == "date":
        try:
            parsed = date.fromisoformat(value[:10]) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value[:10]) else None
        except ValueError:
            parsed = None
        if parsed is None:
            return value, f"'{value}' is not a valid calendar date (expected YYYY-MM-DD)."
        return parsed.isoformat(), None
    if name == "time":
        match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", value)
        if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
            return value, f"'{value}' is not a valid time (expected HH:MM)."
        return f"{int(match.group(1)):02d}:{match.group(2)}", None
    if name == "currency":
        code = value.upper()
        if not is_valid_currency(code):
            return code, f"'{value}' is not an ISO 4217 currency code."
        return code, None
    if name == "gstin":
        return value.upper().replace(" ", ""), None
    return value, None


def from_extraction(extraction: ReceiptExtraction) -> ReceiptData:
    data = ReceiptData(tax_included=extraction.tax_included_in_prices)
    for name in FIELD_ORDER:
        observed = getattr(extraction, _SCHEMA_KEYS.get(name, name))
        value, error = normalize_field(name, observed.value)
        data.values[name] = value
        data.source_text[name] = _clean(observed.source_text)
        if error:
            data.parse_errors[name] = error
    for item in extraction.line_items:
        data.lines.append(line_from_raw(item.model_dump()))
    return data


def line_from_raw(raw: dict) -> Line:
    line = Line(description=_clean(raw.get("description"), 300), source_text=_clean(raw.get("source_text")))
    qty_raw = raw.get("quantity")
    if qty_raw not in (None, ""):
        line.quantity = parse_quantity(qty_raw)
        if line.quantity is None:
            line.parse_errors.append(f"quantity '{qty_raw}' is not a number")
    for key in ("unit_price", "line_total", "discount", "tax"):
        raw_value = raw.get(key)
        if raw_value in (None, ""):
            continue
        try:
            setattr(line, key, parse_decimal(str(raw_value)))
        except MoneyParseError:
            line.parse_errors.append(f"{key.replace('_', ' ')} '{raw_value}' is not a number")
    return line
