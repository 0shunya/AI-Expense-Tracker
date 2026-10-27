from decimal import Decimal

import pytest

from app.services.money import (MoneyParseError, currency_exponent, format_minor, from_minor, is_valid_currency,
                                parse_decimal, parse_minor, round_to_minor, to_minor)


@pytest.mark.parametrize("raw, expected", [
    ("12.50", Decimal("12.50")),
    ("$1,234.56", Decimal("1234.56")),
    ("1.234,56", Decimal("1234.56")),
    ("€12,50", Decimal("12.50")),
    ("Rs. 1,250", Decimal("1250")),
    ("(4.00)", Decimal("-4.00")),
    ("4.00-", Decimal("-4.00")),
    ("-0.99", Decimal("-0.99")),
    ("1 234.00", Decimal("1234.00")),
    ("  ", None),
    (None, None),
])
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "--", "1-2", "N/A"])
def test_parse_decimal_rejects_non_numbers(raw):
    with pytest.raises(MoneyParseError):
        parse_decimal(raw)


def test_float_input_does_not_carry_binary_error():
    assert parse_decimal(0.1) + parse_decimal(0.2) == Decimal("0.3")
    assert 0.1 + 0.2 != 0.3  # the reason money never touches floats


@pytest.mark.parametrize("currency, exponent", [("USD", 2), ("JPY", 0), ("KWD", 3), ("INR", 2), (None, 2)])
def test_currency_exponent(currency, exponent):
    assert currency_exponent(currency) == exponent


def test_to_minor_respects_currency_precision():
    assert to_minor(Decimal("12.34"), "USD") == 1234
    assert to_minor(Decimal("1250"), "JPY") == 1250
    assert to_minor(Decimal("1.234"), "KWD") == 1234
    with pytest.raises(MoneyParseError):
        to_minor(Decimal("12.5"), "JPY")
    with pytest.raises(MoneyParseError):
        to_minor(Decimal("1.005"), "USD")


def test_round_trip_and_format():
    assert from_minor(1234, "USD") == Decimal("12.34")
    assert from_minor(1234, "KWD") == Decimal("1.234")
    assert parse_minor("1,234.50", "EUR") == 123450
    assert format_minor(123450, "EUR") == "EUR 1,234.50"
    assert format_minor(None, "EUR") == "—"


def test_round_to_minor_half_up():
    assert round_to_minor(Decimal("3") * Decimal("1.299"), "USD") == 390  # 3.897 -> 3.90
    assert round_to_minor(Decimal("0.005"), "USD") == 1


def test_currency_codes():
    assert is_valid_currency("INR") and is_valid_currency("JPY")
    assert not is_valid_currency("XYZ") and not is_valid_currency("usd") and not is_valid_currency(None)


@pytest.mark.parametrize("raw", ["1O.5O", "12.5O", "1,2a3.00", "4 . 00 x"])
def test_letters_inside_numbers_are_errors_not_dropped(raw):
    with pytest.raises(MoneyParseError):
        parse_decimal(raw)


@pytest.mark.parametrize("raw, expected", [("-$4.00", Decimal("-4.00")), ("12.50 EUR", Decimal("12.50")),
                                           ("CHF 1'234.50", Decimal("1234.50")), ("Rs.12.50", Decimal("12.50"))])
def test_symbols_at_edges(raw, expected):
    assert parse_decimal(raw) == expected
