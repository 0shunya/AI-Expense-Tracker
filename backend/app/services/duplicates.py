"""Explainable duplicate detection.

* Exact duplicates: the SHA-256 of the uploaded bytes is unique in the database, so an identical file is
  never stored or sent to the model twice (handled at upload time).
* Probable duplicates: a different file whose extracted facts match an earlier receipt. These are only
  flagged for review with the reasons listed; nothing is deleted or merged automatically.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

MERCHANT_SIMILARITY = 0.85
_SUFFIXES = re.compile(r"\b(inc|llc|ltd|limited|pvt|private|co|corp|company|plc|gmbh|store|the)\b")


@dataclass(frozen=True)
class DuplicateKey:
    id: str
    merchant: str | None
    receipt_date: date | None
    currency: str | None
    total_minor: int | None
    reference: str | None


@dataclass
class DuplicateMatch:
    candidate_id: str
    strength: str          # "strong" or "possible"
    reasons: list[str]


def normalize_merchant(name: str | None) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = _SUFFIXES.sub(" ", text)
    return " ".join(text.split())


def normalize_reference(ref: str | None) -> str | None:
    if not ref:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", ref).upper()
    return cleaned if len(cleaned) >= 4 else None


def merchant_similarity(a: str | None, b: str | None) -> float:
    na, nb = normalize_merchant(a), normalize_merchant(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def compare(target: DuplicateKey, other: DuplicateKey) -> DuplicateMatch | None:
    reasons: list[str] = []
    same_amount = (target.total_minor is not None and target.total_minor == other.total_minor
                   and target.currency is not None and target.currency == other.currency)
    similarity = merchant_similarity(target.merchant, other.merchant)
    same_merchant = similarity >= MERCHANT_SIMILARITY
    ref_a, ref_b = normalize_reference(target.reference), normalize_reference(other.reference)
    same_ref = ref_a is not None and ref_a == ref_b
    day_gap = (abs((target.receipt_date - other.receipt_date).days)
               if target.receipt_date and other.receipt_date else None)

    if same_ref and (same_amount or same_merchant):
        reasons.append(f"Same transaction reference ({target.reference}).")
        if same_amount:
            reasons.append("Same currency and total.")
        if same_merchant:
            reasons.append("Same merchant.")
        return DuplicateMatch(other.id, "strong", reasons)
    if same_amount and same_merchant and day_gap == 0:
        reasons = ["Same merchant" + ("" if similarity == 1 else f" (name similarity {similarity:.0%})") + ".",
                   "Same date.", "Same currency and total."]
        return DuplicateMatch(other.id, "strong", reasons)
    if same_amount and same_merchant and (day_gap is None or day_gap <= 1):
        when = "one of the dates is missing" if day_gap is None else "dates are one day apart"
        return DuplicateMatch(other.id, "possible", [f"Same merchant, currency and total; {when}."])
    return None


def find_duplicates(target: DuplicateKey, candidates: list[DuplicateKey]) -> list[DuplicateMatch]:
    matches = [m for c in candidates if c.id != target.id and (m := compare(target, c))]
    return sorted(matches, key=lambda m: m.strength != "strong")
