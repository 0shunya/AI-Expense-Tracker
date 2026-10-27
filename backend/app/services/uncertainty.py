"""Measured doubt: field statuses and review reasons derived from transparent rules.

The model is never asked how sure it is. A field is doubted when:
  1. its value failed deterministic parsing                      -> invalid
  2. it is null                                                  -> missing
  3. a failed validation check implicates it                     -> uncertain
  4. two independent extraction passes disagree on it (optional) -> uncertain
Otherwise it is "extracted" — still unreviewed, never "verified" until a human confirms it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import FieldStatus
from app.services.receipt_data import FIELD_LABELS, FIELD_ORDER, REQUIRED_FIELDS, ReceiptData
from app.services.validation import Check


@dataclass
class FieldAssessment:
    status: str
    reason: str | None = None


@dataclass
class Assessment:
    fields: dict[str, FieldAssessment]
    lines: list[FieldAssessment]
    reasons: list[dict] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.reasons)

    def flagged_fields(self) -> set[str]:
        return {name for name, a in self.fields.items() if a.status != FieldStatus.extracted.value}


def assess(
    data: ReceiptData,
    checks: list[Check],
    *,
    is_receipt: bool = True,
    readability: str = "clear",
    disagreements: dict[str, str] | None = None,
    duplicate_reasons: list[str] | None = None,
) -> Assessment:
    disagreements = disagreements or {}
    failed = [c for c in checks if not c.passed and c.severity in ("error", "warning")]
    implicated: dict[str, str] = {}
    for check in failed:
        for name in check.fields:
            implicated.setdefault(name, check.message)

    fields: dict[str, FieldAssessment] = {}
    for name in FIELD_ORDER:
        if name in data.parse_errors:
            fields[name] = FieldAssessment(FieldStatus.invalid.value, data.parse_errors[name])
        elif data.get(name) is None:
            fields[name] = FieldAssessment(FieldStatus.missing.value, "Not printed on the document or unreadable.")
        elif name in implicated:
            fields[name] = FieldAssessment(FieldStatus.uncertain.value, implicated[name])
        elif name in disagreements:
            fields[name] = FieldAssessment(FieldStatus.uncertain.value, disagreements[name])
        else:
            fields[name] = FieldAssessment(FieldStatus.extracted.value)

    lines: list[FieldAssessment] = []
    for index, line in enumerate(data.lines):
        key = f"line_items[{index}]"
        if line.parse_errors:
            lines.append(FieldAssessment(FieldStatus.invalid.value, "; ".join(line.parse_errors)))
        elif key in implicated:
            lines.append(FieldAssessment(FieldStatus.uncertain.value, implicated[key]))
        elif line.line_total is None:
            lines.append(FieldAssessment(FieldStatus.missing.value, "No amount was read for this line."))
        else:
            lines.append(FieldAssessment(FieldStatus.extracted.value))

    reasons: list[dict] = []

    def add(code: str, message: str, field_name: str | None = None) -> None:
        reasons.append({"code": code, "message": message, "field": field_name})

    if not is_receipt:
        add("not_a_receipt", "The document does not look like a receipt, bill or invoice.")
    if readability == "illegible":
        add("illegible", "The document is largely unreadable; values may be missing or wrong.")
    elif readability == "partially_legible":
        add("partially_legible", "Parts of the document are blurred, cropped or faded; check values against the image.")
    for name in REQUIRED_FIELDS:
        if fields[name].status == FieldStatus.missing.value:
            add("missing_required", f"{FIELD_LABELS[name]} was not found and is required for approval.", name)
    for check in failed:
        add(f"check:{check.code}", check.message, check.fields[0] if check.fields else None)
    for name, message in disagreements.items():
        add("pass_disagreement", message, name)
    for message in duplicate_reasons or []:
        add("probable_duplicate", message)
    return Assessment(fields=fields, lines=lines, reasons=reasons)
