"""Deterministic financial validation. No model calls; every result explains itself.

Receipts follow different conventions (tax added on top vs. included in prices, discounts printed before or
after the subtotal), so reconciliation tries each documented convention and reports which one matched.
A discrepancy produces an explained check result. Values are never altered to make the numbers add up.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.services.money import currency_exponent, is_valid_currency
from app.services.receipt_data import FIELD_LABELS, GSTIN_RE, MONEY_FIELDS, REQUIRED_FIELDS, ReceiptData

MAX_PLAUSIBLE_TAX_RATE = Decimal("0.30")
_AMBIGUOUS_DATE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")


@dataclass
class Check:
    code: str
    severity: str           # error | warning | info
    passed: bool
    message: str
    fields: list[str] = field(default_factory=list)
    details: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _unit(currency: str | None) -> Decimal:
    """One minor unit of the currency, e.g. 0.01 for USD, 1 for JPY, 0.001 for KWD."""
    return Decimal(1).scaleb(-currency_exponent(currency))


def _fmt(value: Decimal | None, currency: str | None) -> str:
    if value is None:
        return "—"
    exp = currency_exponent(currency)
    return f"{value.quantize(Decimal(1).scaleb(-exp)):,}"


def check_required(data: ReceiptData) -> Check:
    missing = [name for name in REQUIRED_FIELDS if data.get(name) is None]
    if missing:
        labels = ", ".join(FIELD_LABELS[name].lower() for name in missing)
        return Check("required_fields", "error", False, f"Required for the tracker but not found: {labels}.", missing)
    return Check("required_fields", "info", True, "Merchant, date, currency and total are all present.")


def check_formats(data: ReceiptData) -> list[Check]:
    checks = [Check("field_format", "error", False, f"{FIELD_LABELS[name]}: {reason}", [name])
              for name, reason in data.parse_errors.items()]
    currency = data.currency
    if currency:
        for name in MONEY_FIELDS:
            value = data.money(name)
            if value is not None and value != value.quantize(_unit(currency)):
                checks.append(Check("amount_precision", "error", False,
                                    f"{FIELD_LABELS[name]} {value} has more decimal places than {currency} allows.", [name]))
    gstin = data.get("gstin")
    if gstin and "gstin" not in data.parse_errors:
        ok = bool(GSTIN_RE.fullmatch(gstin))
        checks.append(Check("gstin_format", "warning" if not ok else "info", ok,
                            "GSTIN matches the 15-character shape." if ok else
                            f"GSTIN '{gstin}' does not match the 15-character GSTIN shape.", [] if ok else ["gstin"]))
    return checks


def check_currency(data: ReceiptData) -> Check | None:
    code = data.get("currency")
    if code is None or "currency" in data.parse_errors:
        return None
    ok = is_valid_currency(code)
    return Check("currency_code", "info" if ok else "error", ok,
                 f"{code} is a valid ISO 4217 code." if ok else f"{code} is not an ISO 4217 code.", [] if ok else ["currency"])


def check_dates(data: ReceiptData, today: date, max_age_days: int) -> list[Check]:
    checks: list[Check] = []
    parsed = data.date
    if parsed is not None:
        if parsed > today + timedelta(days=1):
            checks.append(Check("date_not_future", "error", False,
                                f"Date {parsed.isoformat()} is in the future — likely a misread day, month or year.", ["date"]))
        elif (today - parsed).days > max_age_days:
            checks.append(Check("date_in_policy_window", "warning", False,
                                f"Date {parsed.isoformat()} is more than {max_age_days} days old; confirm the year.", ["date"]))
        else:
            checks.append(Check("date_plausible", "info", True, "Date parses and is within the accepted window."))
    source = data.source_text.get("date") or ""
    match = _AMBIGUOUS_DATE.search(source)
    if match:
        first, second = int(match.group(1)), int(match.group(2))
        if first <= 12 and second <= 12 and first != second:
            checks.append(Check(
                "date_unambiguous", "warning", False,
                f"Printed date '{match.group(0)}' could be day/month or month/day; confirm which was meant.",
                ["date"], {"printed": match.group(0)}))
    return checks


def check_signs(data: ReceiptData) -> list[Check]:
    checks: list[Check] = []
    total = data.money("total")
    if total is not None and total < 0:
        checks.append(Check("refund_detected", "warning", False,
                            "Total is negative, which indicates a refund or credit note. Confirm before approving.", ["total"]))
    for name in ("tax", "tip", "service_charge"):
        value = data.money(name)
        if value is not None and value < 0:
            checks.append(Check("negative_amount", "warning", False, f"{FIELD_LABELS[name]} is negative, which is unusual.", [name]))
    discount = data.money("discount")
    if discount is not None and discount < 0:
        checks.append(Check("discount_sign", "info", True,
                            "Discount was printed as a negative number; it is treated as a reduction.", []))
    return checks


def check_line_items(data: ReceiptData) -> list[Check]:
    currency = data.currency
    tolerance = _unit(currency)
    failures, checked = [], 0
    for index, line in enumerate(data.lines):
        if line.quantity is None or line.unit_price is None or line.line_total is None:
            continue
        checked += 1
        gross = line.quantity * line.unit_price
        candidates = [gross]
        if line.discount:
            candidates.append(gross - abs(line.discount))
        if not any(abs(c - line.line_total) <= tolerance for c in candidates):
            failures.append({"line": index, "description": line.description,
                             "expected": _fmt(gross, currency), "printed": _fmt(line.line_total, currency)})
    if not checked:
        return []
    if failures:
        first = failures[0]
        msg = (f"{len(failures)} line(s) where quantity × unit price ≠ line total, e.g. "
               f"'{first['description'] or 'line ' + str(first['line'] + 1)}': expected {first['expected']}, printed {first['printed']}.")
        return [Check("line_extensions", "warning", False, msg, [f"line_items[{f['line']}]" for f in failures], {"failures": failures})]
    return [Check("line_extensions", "info", True, f"Quantity × unit price matches the line total on {checked} line(s).")]


def _items_sum(data: ReceiptData) -> tuple[Decimal | None, Decimal | None]:
    totals = [line.line_total for line in data.lines]
    if not totals or any(t is None for t in totals):
        return None, None
    gross = sum(totals, Decimal(0))
    line_discounts = sum((abs(line.discount) for line in data.lines if line.discount), Decimal(0))
    return gross, gross - line_discounts


def check_subtotal(data: ReceiptData) -> Check | None:
    subtotal = data.money("subtotal")
    gross, net = _items_sum(data)
    if subtotal is None or gross is None:
        return None
    currency = data.currency
    tolerance = _unit(currency)
    for label, candidate in (("sum of line totals", gross), ("sum of line totals after line discounts", net)):
        if abs(candidate - subtotal) <= tolerance:
            return Check("items_match_subtotal", "info", True, f"Subtotal equals the {label} ({_fmt(candidate, currency)}).")
    diff = subtotal - gross
    return Check("items_match_subtotal", "warning", False,
                 f"Line items add up to {_fmt(gross, currency)} but the subtotal reads {_fmt(subtotal, currency)} "
                 f"(difference {_fmt(diff, currency)}). A line item may be missing or misread.",
                 ["subtotal", "line_items"], {"items_sum": _fmt(gross, currency), "subtotal": _fmt(subtotal, currency)})


def check_total(data: ReceiptData) -> Check | None:
    """Try each accounting convention; pass if any explains the printed total within rounding tolerance."""
    total = data.money("total")
    if total is None:
        return None
    currency = data.currency
    subtotal = data.money("subtotal")
    base_label = "subtotal"
    if subtotal is None:
        gross, _ = _items_sum(data)
        if gross is None:
            return Check("total_reconciles", "info", True,
                         "Total could not be cross-checked: no subtotal or complete line items were found.", [],
                         {"checkable": False})
        subtotal, base_label = gross, "line-item sum"
    tax = data.money("tax") or Decimal(0)
    tip = data.money("tip") or Decimal(0)
    service = data.money("service_charge") or Decimal(0)
    discount = abs(data.money("discount") or Decimal(0))
    conventions = [
        ("tax added on top of the subtotal", subtotal + tax + tip + service - discount),
        ("tax already included in prices", subtotal + tip + service - discount),
        ("discount already applied within the subtotal", subtotal + tax + tip + service),
        ("tax included and discount already applied", subtotal + tip + service),
    ]
    if data.tax_included is True:
        conventions.insert(0, conventions.pop(1))
    # Rounding: allow one minor unit, plus one more when a separately-rounded tax line is involved.
    tolerance = _unit(currency) * (2 if tax else 1)
    for label, expected in conventions:
        if abs(expected - total) <= tolerance:
            if label == conventions[0][0] or (tax == 0 and discount == 0):
                msg = f"Total reconciles: {base_label} {_fmt(subtotal, currency)} with {label} = {_fmt(expected, currency)}."
            else:
                msg = f"Total reconciles assuming {label} ({_fmt(expected, currency)})."
            note = " Difference is within rounding tolerance." if expected != total else ""
            return Check("total_reconciles", "info", True, msg + note, [], {"convention": label, "expected": _fmt(expected, currency)})
    closest_label, closest = min(conventions, key=lambda c: abs(c[1] - total))
    implicated = ["total"] + [name for name in ("subtotal", "tax", "tip", "service_charge", "discount") if data.get(name) is not None]
    return Check(
        "total_reconciles", "error", False,
        f"Printed total {_fmt(total, currency)} does not match the {base_label} and adjustments under any convention; "
        f"closest is {_fmt(closest, currency)} ({closest_label}), off by {_fmt(total - closest, currency)}.",
        implicated, {"closest_convention": closest_label, "expected": _fmt(closest, currency), "printed": _fmt(total, currency)})


def check_tax_rate(data: ReceiptData) -> Check | None:
    tax, subtotal = data.money("tax"), data.money("subtotal")
    if tax is None or not subtotal or subtotal <= 0 or tax <= 0:
        return None
    rate = tax / subtotal
    if rate > MAX_PLAUSIBLE_TAX_RATE:
        return Check("tax_rate_plausible", "warning", False,
                     f"Tax is {rate:.0%} of the subtotal, above the {MAX_PLAUSIBLE_TAX_RATE:.0%} plausibility limit; "
                     f"the tax or subtotal may be misread.", ["tax"], {"rate": f"{rate:.4f}"})
    return Check("tax_rate_plausible", "info", True, f"Tax is {rate:.1%} of the subtotal.")


def check_line_parse(data: ReceiptData) -> Check | None:
    bad = [(i, e) for i, line in enumerate(data.lines) for e in line.parse_errors]
    if not bad:
        return None
    return Check("line_item_format", "error", False,
                 f"{len(bad)} line-item value(s) could not be read as numbers, e.g. line {bad[0][0] + 1}: {bad[0][1]}.",
                 sorted({f"line_items[{i}]" for i, _ in bad}))


def validate(data: ReceiptData, today: date | None = None, max_age_days: int = 730) -> list[Check]:
    today = today or date.today()
    checks: list[Check] = [check_required(data), *check_formats(data)]
    for maybe in (check_currency(data),):
        if maybe:
            checks.append(maybe)
    checks += check_dates(data, today, max_age_days)
    checks += check_signs(data)
    checks += check_line_items(data)
    for maybe in (check_line_parse(data), check_subtotal(data), check_total(data), check_tax_rate(data)):
        if maybe:
            checks.append(maybe)
    return checks


def reconciles(checks: list[Check]) -> bool | None:
    """True/False when the total could be cross-checked; None when there was nothing to check against."""
    for check in checks:
        if check.code == "total_reconciles":
            if check.details and check.details.get("checkable") is False:
                return None
            return check.passed
    return None
