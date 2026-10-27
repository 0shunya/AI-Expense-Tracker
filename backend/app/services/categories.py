"""Expense categorisation: keyword rules first, the model's suggestion as a fallback."""

from __future__ import annotations

from app.llm.schema import CATEGORIES

RULES: list[tuple[str, tuple[str, ...]]] = [
    ("fuel", ("fuel", "petrol", "diesel", "gas station", "shell", "chevron", "bp ")),
    ("groceries", ("grocer", "supermarket", "market", "mart", "foods", "produce")),
    ("dining", ("cafe", "café", "coffee", "restaurant", "bistro", "pizza", "burger", "bakery", "diner", "kitchen")),
    ("transport", ("taxi", "uber", "lyft", "metro", "transit", "railway", "parking", "toll")),
    ("travel", ("airline", "airways", "air ", "flight", "travel")),
    ("lodging", ("hotel", "inn", "resort", "hostel", "lodge")),
    ("health", ("pharmacy", "chemist", "clinic", "hospital", "medical", "dental")),
    ("office", ("stationery", "office", "printing", "supplies")),
    ("utilities", ("electric", "water board", "internet", "broadband", "telecom", "mobile recharge")),
]


def categorize(merchant: str | None, descriptions: list[str | None], model_suggestion: str | None) -> tuple[str, str]:
    """Return (category, source) where source is 'rules' or 'model'."""
    text = " ".join([merchant or "", *(d or "" for d in descriptions)]).casefold()
    for category, keywords in RULES:
        if any(k in text for k in keywords):
            return category, "rules"
    if model_suggestion in CATEGORIES:
        return model_suggestion, "model"
    return "other", "rules"
