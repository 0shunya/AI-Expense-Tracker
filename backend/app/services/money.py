"""Exact money handling with Decimal. Amounts are stored as integer minor units plus an ISO 4217 code."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# ISO 4217 active codes (2024 list). Exponent defaults to 2 unless listed below.
ISO_4217 = frozenset("""
AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD CAD CDF
CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD
HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD
LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK
PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS
TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWG
""".split())

ZERO_DECIMAL = frozenset("BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX VND VUV XAF XOF XPF".split())
THREE_DECIMAL = frozenset("BHD IQD JOD KWD LYD OMR TND".split())


class MoneyParseError(ValueError):
    pass


def is_valid_currency(code: str | None) -> bool:
    return bool(code) and code in ISO_4217


def currency_exponent(currency: str | None) -> int:
    if currency in ZERO_DECIMAL:
        return 0
    if currency in THREE_DECIMAL:
        return 3
    return 2


_EDGES = re.compile(r"^[^\d\-.,]+|[^\d\-.,]+$")
_NUMBER_BODY = re.compile(r"[\d.,\s'  ]+")


def _strip_edges(text: str, raw: object) -> str:
    """Remove a currency symbol or code from either end; a lone letter there is treated as a misread digit."""
    for chunk in _EDGES.findall(text):
        if len(re.sub(r"[^A-Za-z]", "", chunk)) == 1:
            raise MoneyParseError(f"stray letter in amount: {raw!r}")
    return _EDGES.sub("", text)


def parse_decimal(raw: str | int | float | Decimal | None) -> Decimal | None:
    """Parse a printed amount into a Decimal without guessing.

    Handles currency symbols, thousands separators in either convention ("1,234.50" / "1.234,50"),
    accounting negatives "(12.00)" and trailing minus "12.00-". Raises MoneyParseError when the text
    cannot be read as exactly one number.
    """
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        # Floats only arrive from badly-typed JSON; route through str to avoid binary artefacts.
        return Decimal(str(raw))
    text = raw.strip()
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    # Currency symbols/codes may only sit at the edges ("Rs. 1,250", "12.50 EUR"). Anything non-numeric
    # inside the number (e.g. the letter O read for a zero in "1O.5O") is an error, never silently dropped.
    core = _strip_edges(text, raw)
    if core.endswith("-"):
        negative, core = True, core[:-1]
    if core.startswith("-"):
        negative, core = not negative, _strip_edges(core[1:], raw)
    core = core.strip(".,")
    if not core or not _NUMBER_BODY.fullmatch(core) or not re.search(r"\d", core):
        raise MoneyParseError(f"not a number: {raw!r}")
    cleaned = re.sub(r"[\s'  ]", "", core)

    last_dot, last_comma = cleaned.rfind("."), cleaned.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        decimal_sep = "." if last_dot > last_comma else ","
    elif last_comma >= 0:
        # "12,50" is a decimal comma; "1,250" is a thousands separator.
        decimal_sep = "," if len(cleaned) - last_comma - 1 in (1, 2) and cleaned.count(",") == 1 else None
    elif last_dot >= 0:
        decimal_sep = "." if not (cleaned.count(".") > 1) else None
    else:
        decimal_sep = None

    if decimal_sep is None:
        normalized = cleaned.replace(",", "").replace(".", "")
    else:
        thousands = "," if decimal_sep == "." else "."
        integer, _, fraction = cleaned.rpartition(decimal_sep)
        integer = integer.replace(thousands, "")
        if not integer.isdigit() and integer != "":
            raise MoneyParseError(f"ambiguous separators: {raw!r}")
        normalized = f"{integer or '0'}.{fraction}"
    try:
        value = Decimal(normalized)
    except InvalidOperation as exc:
        raise MoneyParseError(f"not a number: {raw!r}") from exc
    if not value.is_finite():
        raise MoneyParseError(f"not finite: {raw!r}")
    return -value if negative else value


def to_minor(amount: Decimal | None, currency: str | None) -> int | None:
    """Convert major units to integer minor units. Raises if precision exceeds the currency's exponent."""
    if amount is None:
        return None
    exponent = currency_exponent(currency)
    scaled = amount * (Decimal(10) ** exponent)
    if scaled != scaled.to_integral_value():
        raise MoneyParseError(
            f"{amount} has more decimal places than {currency or 'the currency'} allows ({exponent})")
    return int(scaled)


def parse_minor(raw: str | int | float | Decimal | None, currency: str | None) -> int | None:
    return to_minor(parse_decimal(raw), currency)


def from_minor(minor: int | None, currency: str | None) -> Decimal | None:
    if minor is None:
        return None
    exponent = currency_exponent(currency)
    return (Decimal(minor) / (Decimal(10) ** exponent)).quantize(Decimal(1).scaleb(-exponent))


def format_minor(minor: int | None, currency: str | None) -> str:
    value = from_minor(minor, currency)
    return "—" if value is None else f"{currency or ''} {value:,}".strip()


def round_to_minor(value: Decimal, currency: str | None) -> int:
    """Round a computed (not extracted) Decimal, e.g. qty × unit price, to minor units half-up."""
    exponent = currency_exponent(currency)
    return int((value * (Decimal(10) ** exponent)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_quantity(raw: str | int | float | Decimal | None) -> Decimal | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = parse_decimal(raw)
    except MoneyParseError:
        return None
    return value
