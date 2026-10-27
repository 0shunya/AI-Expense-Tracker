"""Deterministic financial validation: runs with no model at all."""

from datetime import date
from decimal import Decimal

from app.services.receipt_data import Line, ReceiptData, normalize_field
from app.services.validation import reconciles, validate

TODAY = date(2026, 10, 27)


def make(lines=None, tax_included=None, **values) -> ReceiptData:
    base = {"merchant": "Shop", "date": "2026-10-01", "currency": "USD", "subtotal": None, "tax": None, "tip": None,
            "discount": None, "service_charge": None, "total": None, "gstin": None}
    base.update(values)
    data = ReceiptData(tax_included=tax_included)
    for name, raw in base.items():
        value, error = normalize_field(name, raw)
        data.values[name] = value
        data.source_text[name] = raw
        if error:
            data.parse_errors[name] = error
    data.lines = lines or []
    return data


def L(desc, qty, unit, total, discount=None):
    d = lambda v: None if v is None else Decimal(v)  # noqa: E731
    return Line(description=desc, quantity=d(qty), unit_price=d(unit), line_total=d(total), discount=d(discount))


def by_code(checks):
    return {c.code: c for c in checks}


def test_clean_receipt_passes_every_check():
    data = make(subtotal="12.25", tax="0.98", total="13.23",
                lines=[L("Flat white", "2", "4.50", "9.00"), L("Croissant", "1", "3.25", "3.25")])
    checks = validate(data, today=TODAY)
    failed = [c for c in checks if not c.passed]
    assert failed == []
    assert reconciles(checks) is True
    assert "tax added on top" in by_code(checks)["total_reconciles"].message


def test_planted_wrong_total_is_flagged():
    data = make(subtotal="12.25", tax="0.98", total="18.23")
    check = by_code(validate(data, today=TODAY))["total_reconciles"]
    assert not check.passed and check.severity == "error"
    assert "total" in check.fields and "off by" in check.message
    assert check.details["expected"] == "13.23"


def test_tax_inclusive_convention_reconciles_without_correction():
    # European VAT receipt: prices include VAT; VAT shown for information only.
    data = make(currency="EUR", subtotal="20.00", tax="3.19", total="20.00", tax_included=True)
    check = by_code(validate(data, today=TODAY))["total_reconciles"]
    assert check.passed and "included" in check.details["convention"]


def test_discount_and_service_charge_and_tip():
    data = make(subtotal="100.00", tax="8.00", service_charge="10.00", tip="5.00", discount="10.00", total="113.00")
    assert by_code(validate(data, today=TODAY))["total_reconciles"].passed


def test_discount_already_applied_in_subtotal_convention():
    data = make(subtotal="90.00", discount="10.00", tax="9.00", total="99.00")
    check = by_code(validate(data, today=TODAY))["total_reconciles"]
    assert check.passed and "discount already applied" in check.details["convention"]


def test_rounding_tolerance_is_one_minor_unit_plus_tax_rounding():
    assert by_code(validate(make(subtotal="10.00", tax="0.83", total="10.84"), today=TODAY))["total_reconciles"].passed
    assert not by_code(validate(make(subtotal="10.00", tax="0.83", total="10.90"), today=TODAY))["total_reconciles"].passed


def test_zero_decimal_currency_tolerance():
    data = make(currency="JPY", subtotal="1200", tax="120", total="1320")
    assert by_code(validate(data, today=TODAY))["total_reconciles"].passed
    data = make(currency="JPY", subtotal="1200", tax="120", total="1330")
    assert not by_code(validate(data, today=TODAY))["total_reconciles"].passed


def test_total_checked_against_line_sum_when_subtotal_missing():
    data = make(total="12.25", lines=[L("A", "1", "9.00", "9.00"), L("B", "1", "3.25", "3.25")])
    check = by_code(validate(data, today=TODAY))["total_reconciles"]
    assert check.passed and "line-item sum" in check.message


def test_total_not_checkable_without_base():
    checks = validate(make(total="12.25"), today=TODAY)
    assert reconciles(checks) is None


def test_line_extension_mismatch():
    data = make(lines=[L("Coffee", "2", "4.50", "8.00")])
    check = by_code(validate(data, today=TODAY))["line_extensions"]
    assert not check.passed and check.fields == ["line_items[0]"]
    assert check.details["failures"][0]["expected"] == "9.00"


def test_line_extension_accepts_line_discount_and_fractional_unit_prices():
    data = make(lines=[L("Fuel", "10.535", "1.299", "13.69"), L("Shirt", "1", "20.00", "15.00", discount="5.00")])
    assert by_code(validate(data, today=TODAY))["line_extensions"].passed


def test_items_do_not_match_subtotal():
    data = make(subtotal="20.00", lines=[L("A", None, None, "9.00"), L("B", None, None, "3.25")])
    check = by_code(validate(data, today=TODAY))["items_match_subtotal"]
    assert not check.passed and set(check.fields) == {"subtotal", "line_items"}


def test_missing_required_fields():
    data = make(merchant=None, date=None, total=None)
    check = by_code(validate(data, today=TODAY))["required_fields"]
    assert not check.passed and set(check.fields) == {"merchant", "date", "total"}


def test_invalid_formats_are_errors_not_guesses():
    data = make(date="14/03/2026", currency="DOLLARS", total="twelve")
    checks = validate(data, today=TODAY)
    format_fields = {c.fields[0] for c in checks if c.code == "field_format"}
    assert format_fields == {"date", "currency", "total"}
    assert data.values["total"] == "twelve"  # nothing was coerced


def test_future_date_and_old_date():
    assert not by_code(validate(make(date="2027-01-05"), today=TODAY))["date_not_future"].passed
    assert not by_code(validate(make(date="2020-01-05"), today=TODAY))["date_in_policy_window"].passed


def test_ambiguous_printed_date_is_flagged():
    data = make(date="2026-04-05")
    data.source_text["date"] = "04/05/26"
    check = by_code(validate(data, today=TODAY))["date_unambiguous"]
    assert not check.passed and check.fields == ["date"]
    data.source_text["date"] = "25/05/26"  # day > 12: unambiguous
    assert "date_unambiguous" not in by_code(validate(data, today=TODAY))


def test_refund_and_negative_amounts():
    checks = by_code(validate(make(subtotal="-20.00", total="-20.00", tax="-1.00"), today=TODAY))
    assert not checks["refund_detected"].passed
    assert not checks["negative_amount"].passed


def test_implausible_tax_rate():
    check = by_code(validate(make(subtotal="10.00", tax="8.00", total="18.00"), today=TODAY))["tax_rate_plausible"]
    assert not check.passed and check.fields == ["tax"]


def test_amount_precision_for_currency():
    check = by_code(validate(make(currency="JPY", total="1200.50"), today=TODAY))["amount_precision"]
    assert not check.passed and check.fields == ["total"]


def test_gstin_shape():
    assert by_code(validate(make(gstin="27AAPFU0939F1ZV"), today=TODAY))["gstin_format"].passed
    assert not by_code(validate(make(gstin="27AAPFU0939F1Z"), today=TODAY))["gstin_format"].passed
