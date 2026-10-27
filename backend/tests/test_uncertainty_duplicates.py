from datetime import date

from app.services.duplicates import DuplicateKey, compare, find_duplicates, merchant_similarity, normalize_reference
from app.services.uncertainty import assess
from app.services.validation import validate
from tests.test_validation import TODAY, L, make


def test_clean_receipt_needs_no_review_and_nothing_is_verified():
    data = make(subtotal="12.25", tax="0.98", total="13.23", lines=[L("A", "1", "12.25", "12.25")])
    result = assess(data, validate(data, today=TODAY))
    assert not result.needs_review
    assert result.fields["total"].status == "extracted"     # unreviewed is never "verified"
    assert result.fields["tip"].status == "missing"          # optional nulls stay explicit
    assert all(a.status != "verified" for a in result.fields.values())


def test_wrong_total_marks_total_and_components_uncertain():
    data = make(subtotal="12.25", tax="0.98", total="18.23")
    result = assess(data, validate(data, today=TODAY))
    assert result.needs_review
    assert result.fields["total"].status == "uncertain"
    assert result.fields["subtotal"].status == "uncertain"
    assert "does not match" in result.fields["total"].reason
    assert any(r["code"] == "check:total_reconciles" for r in result.reasons)


def test_missing_required_and_invalid_fields():
    data = make(merchant=None, currency="ZZZ", total="12.00")
    result = assess(data, validate(data, today=TODAY))
    assert result.fields["merchant"].status == "missing"
    assert result.fields["currency"].status == "invalid"
    codes = {r["code"] for r in result.reasons}
    assert "missing_required" in codes


def test_readability_not_a_receipt_disagreement_and_duplicates_trigger_review():
    data = make(subtotal="10.00", total="10.00")
    checks = validate(data, today=TODAY)
    result = assess(data, checks, is_receipt=False, readability="partially_legible",
                    disagreements={"total": "passes disagree"}, duplicate_reasons=["dup"])
    codes = {r["code"] for r in result.reasons}
    assert {"not_a_receipt", "partially_legible", "pass_disagreement", "probable_duplicate"} <= codes
    assert result.fields["total"].status == "uncertain"


def test_line_item_statuses():
    data = make(lines=[L("ok", "1", "2.00", "2.00"), L("bad", "2", "2.00", "3.00"), L("no amount", None, None, None)])
    result = assess(data, validate(data, today=TODAY))
    assert [a.status for a in result.lines] == ["extracted", "uncertain", "missing"]


def key(id, merchant="Blue Oak Cafe", d=date(2026, 3, 14), cur="USD", total=1323, ref=None):
    return DuplicateKey(id, merchant, d, cur, total, ref)


def test_merchant_normalisation():
    assert merchant_similarity("Blue Oak Cafe, Inc.", "BLUE OAK CAFE") == 1.0
    assert merchant_similarity("Blue Oak Café", "Blue Oak Cafe") == 1.0
    assert merchant_similarity("Blue Oak Cafe", "Red Pine Diner") < 0.6
    assert normalize_reference("A19-55821") == "A1955821"
    assert normalize_reference("#1") is None


def test_same_facts_is_a_strong_duplicate_with_reasons():
    match = compare(key("a"), key("b"))
    assert match and match.strength == "strong"
    assert "Same date." in match.reasons and "Same currency and total." in match.reasons


def test_reference_match():
    match = compare(key("a", ref="TXN-0042", d=date(2026, 3, 1)), key("b", ref="txn0042", d=date(2026, 3, 9)))
    assert match and match.strength == "strong" and "reference" in match.reasons[0]


def test_one_day_apart_or_missing_date_is_possible():
    assert compare(key("a"), key("b", d=date(2026, 3, 15))).strength == "possible"
    assert compare(key("a"), key("b", d=None)).strength == "possible"


def test_near_misses_are_not_duplicates():
    assert compare(key("a"), key("b", total=1324)) is None                  # different total
    assert compare(key("a"), key("b", cur="EUR")) is None                    # different currency
    assert compare(key("a"), key("b", merchant="Harbor Books")) is None      # different merchant
    assert compare(key("a"), key("b", d=date(2026, 3, 20))) is None          # different week
    assert compare(key("a", total=None), key("b", total=None)) is None        # nothing to compare


def test_find_duplicates_orders_strong_first_and_skips_self():
    matches = find_duplicates(key("a"), [key("a"), key("p", d=date(2026, 3, 15)), key("s")])
    assert [m.candidate_id for m in matches] == ["s", "p"]
