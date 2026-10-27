"""The receipt schema handed to the model as its response format, and validated again by Pydantic.

Design rules (from the roadmap):
* every field is nullable, so the model can say "unreadable" instead of inventing a value;
* the model transcribes; it never computes. Amounts are decimal strings exactly as printed and the
  application converts them to integer minor units with Decimal;
* there is no confidence field. Doubt is derived from rules, nulls and pass disagreement instead.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CATEGORIES = ("groceries", "dining", "transport", "fuel", "travel", "lodging", "utilities", "office",
              "health", "shopping", "entertainment", "services", "other")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TextValue(_Strict):
    value: str | None = Field(description="Normalised value, or null if absent or unreadable.")
    source_text: str | None = Field(description="The exact characters printed on the document, or null.")


class AmountValue(_Strict):
    value: str | None = Field(
        description="Amount as a plain decimal string in major units using '.' as decimal separator and no "
                    "thousands separators or currency symbols, e.g. '1234.50'. Negative only for refunds. "
                    "Null if not printed or unreadable. Never computed.")
    source_text: str | None = Field(description="The amount exactly as printed, e.g. 'Rs. 1,234.50'.")


class LineItemOut(_Strict):
    description: str | None
    quantity: str | None = Field(description="Quantity as printed, e.g. '2' or '0.535'; null if not printed.")
    unit_price: str | None = Field(description="Unit price decimal string; null if not printed.")
    line_total: str | None = Field(description="Line amount decimal string as printed; null if not printed.")
    discount: str | None = Field(description="Positive discount printed against this line; null if none.")
    tax: str | None = Field(description="Tax printed against this line; null if none.")
    source_text: str | None = Field(description="The printed line, verbatim.")


class ReceiptExtraction(_Strict):
    is_receipt: bool = Field(description="False if the document is not a receipt, bill or invoice.")
    readability: Literal["clear", "partially_legible", "illegible"] = Field(
        description="'partially_legible' if any part is blurred, cropped, faded or obscured.")
    merchant_name: TextValue
    merchant_address: TextValue
    transaction_date: TextValue = Field(
        description="value is ISO 8601 YYYY-MM-DD. source_text is the date as printed. If the day/month order "
                    "cannot be established from the document, still give your best ISO reading in value.")
    transaction_time: TextValue = Field(description="value is 24-hour HH:MM.")
    currency: TextValue = Field(description="value is the ISO 4217 code; source_text is the printed symbol/code.")
    subtotal: AmountValue
    tax: AmountValue = Field(description="Total tax (sum of all tax lines such as VAT, GST, CGST+SGST).")
    tip: AmountValue
    discount: AmountValue = Field(description="Total receipt-level discount as a positive number.")
    service_charge: AmountValue
    total: AmountValue = Field(description="The final amount payable/paid.")
    tax_included_in_prices: bool | None = Field(
        description="True if the document states prices include tax (e.g. 'VAT included'); null if not stated.")
    payment_method: TextValue
    transaction_reference: TextValue = Field(description="Invoice, bill, transaction or order number.")
    gstin: TextValue = Field(description="Indian GSTIN of the merchant if printed, else null.")
    line_items: list[LineItemOut]
    category_suggestion: Literal[CATEGORIES]  # type: ignore[valid-type]
    notes: list[str] = Field(description="Short notes about problems reading the document; empty if none.")


def response_json_schema() -> dict:
    return ReceiptExtraction.model_json_schema()
