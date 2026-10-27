"""End-to-end API flows against a real database (SQLite by default) with a scripted model provider."""

from app.llm.providers import ProviderError, ProviderResponse
from app.services.pipeline import run_once
from tests.factories import gateway_factory, image_bytes, pdf_bytes, reply, response, scripted, tv


def upload(client, data=None, name="receipt.png", headers=None):
    return client.post("/api/receipts", files={"file": (name, data or image_bytes(text=name), "image/png")},
                       headers=headers or {})


def process(*items):
    provider = scripted(*items)
    assert run_once(gateway_factory(provider)) is True
    return provider


def upload_and_process(client, model_reply=None, name="receipt.png"):
    created = upload(client, image_bytes(text=name), name)
    assert created.status_code == 202, created.text
    receipt_id = created.json()["receipt"]["id"]
    process(response(model_reply or reply()))
    return receipt_id, client.get(f"/api/receipts/{receipt_id}").json()


def field(detail, name):
    return next(f for f in detail["fields"] if f["name"] == name)


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["database"] == "ok"
    assert body["gemini_configured"] is False and "api_key" not in str(body).lower()


def test_upload_is_queued_and_never_waits_for_the_model(client):
    res = upload(client)
    assert res.status_code == 202
    body = res.json()
    assert body["created"] is True and body["receipt"]["status"] == "queued"


def test_exact_duplicate_file_is_not_stored_twice(client):
    data = image_bytes(text="same bytes")
    first = upload(client, data).json()
    second = upload(client, data)
    assert second.status_code == 200
    assert second.json()["exact_duplicate"] is True
    assert second.json()["receipt"]["id"] == first["receipt"]["id"]
    assert client.get("/api/receipts").json()["total"] == 1


def test_idempotency_key_replays_original(client):
    first = upload(client, image_bytes(text="a"), headers={"Idempotency-Key": "abc-123"}).json()
    replay = upload(client, image_bytes(text="b"), headers={"Idempotency-Key": "abc-123"})
    assert replay.status_code == 200 and replay.json()["receipt"]["id"] == first["receipt"]["id"]


def test_invalid_uploads_return_error_envelope(client):
    res = client.post("/api/receipts", files={"file": ("x.jpg", b"not an image at all", "image/jpeg")})
    assert res.status_code == 415
    assert res.json()["error"]["code"] == "unsupported_type"
    res = client.post("/api/receipts", files={"file": ("x.png", b"", "image/png")})
    assert res.json()["error"]["code"] == "empty_file"
    assert client.post("/api/receipts").status_code == 422


def test_clean_extraction_goes_to_review_with_no_flags(client):
    receipt_id, detail = upload_and_process(client)
    assert detail["receipt"]["status"] == "needs_review"
    assert detail["review_reasons"] == []
    assert field(detail, "total")["status"] == "extracted"
    assert field(detail, "total")["value"] == "13.23"
    assert detail["receipt"]["total_minor"] == 1323
    assert all(c["passed"] for c in detail["checks"])
    assert len(detail["line_items"]) == 2
    assert detail["model_calls"][0]["status"] == "success"
    assert detail["model_calls"][0]["input_tokens"] == 1200
    assert "approve" in detail["allowed_actions"]


def test_auto_approve_when_enabled(client, settings_override):
    settings_override(auto_approve_clean_receipts="true")
    receipt_id, detail = upload_and_process(client)
    assert detail["receipt"]["status"] == "approved" and detail["receipt"]["approved_by"] == "auto_rules"
    assert field(detail, "total")["status"] == "extracted"  # auto-approved values are not "verified"
    assert detail["expense"]["approved_by"] == "auto_rules"


def test_wrong_total_is_flagged_then_corrected_and_approved(client):
    receipt_id, detail = upload_and_process(client, reply(total=tv("18.23")))
    assert field(detail, "total")["status"] == "uncertain"
    assert any(r["code"] == "check:total_reconciles" for r in detail["review_reasons"])
    # Approving with a failing check needs an explanation...
    blocked = client.post(f"/api/receipts/{receipt_id}/approve", json={})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "override_note_required"
    # ...or a correction, which is logged and re-validated.
    fixed = client.patch(f"/api/receipts/{receipt_id}/fields/total", json={"value": "13.23"}).json()
    assert field(fixed, "total")["status"] == "verified"
    assert field(fixed, "total")["extracted_value"] == "18.23"
    assert fixed["corrections"][-1] == {**fixed["corrections"][-1], "field": "total", "old_value": "18.23",
                                        "new_value": "13.23", "action": "corrected"}
    assert fixed["review_reasons"] == []
    approved = client.post(f"/api/receipts/{receipt_id}/approve", json={}).json()
    assert approved["receipt"]["status"] == "approved"
    assert approved["expense"]["total_minor"] == 1323 and approved["expense"]["category"] == "dining"
    assert field(approved, "merchant")["status"] == "verified"


def test_override_with_note_is_recorded(client):
    receipt_id, _ = upload_and_process(client, reply(total=tv("18.23")))
    res = client.post(f"/api/receipts/{receipt_id}/approve", json={"note": "Tip was handwritten, total is right"})
    body = res.json()
    assert res.status_code == 200 and body["receipt"]["status"] == "approved"
    assert body["decisions"][-1]["overridden_checks"][0]["code"] == "total_reconciles"


def test_invalid_correction_is_rejected_not_saved(client):
    receipt_id, _ = upload_and_process(client)
    res = client.patch(f"/api/receipts/{receipt_id}/fields/total", json={"value": "abc"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_value"
    res = client.patch(f"/api/receipts/{receipt_id}/fields/merchant", json={"value": ""})
    assert res.status_code == 422 and res.json()["error"]["code"] == "required_field"
    res = client.patch(f"/api/receipts/{receipt_id}/fields/nonsense", json={"value": "x"})
    assert res.status_code == 404


def test_missing_required_field_blocks_approval_until_entered(client):
    receipt_id, detail = upload_and_process(client, reply(merchant_name=tv(None)))
    assert field(detail, "merchant")["status"] == "missing"
    res = client.post(f"/api/receipts/{receipt_id}/approve", json={"note": "ok"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "approval_blocked"
    client.patch(f"/api/receipts/{receipt_id}/fields/merchant", json={"value": "Blue Oak Cafe"})
    assert client.post(f"/api/receipts/{receipt_id}/approve", json={}).json()["receipt"]["status"] == "approved"


def test_confirm_field_is_one_tap_and_logged(client):
    receipt_id, _ = upload_and_process(client)
    body = client.post(f"/api/receipts/{receipt_id}/fields/date/confirm").json()
    assert field(body, "date")["status"] == "verified"
    assert body["corrections"][-1]["action"] == "confirmed"


def test_line_item_edit_revalidates(client):
    lines = [{"description": "Flat white", "quantity": "2", "unit_price": "4.50", "line_total": "8.00",
              "discount": None, "tax": None, "source_text": None}]
    receipt_id, detail = upload_and_process(client, reply(line_items=lines, subtotal=tv("8.00"), tax=tv("0.64"), total=tv("8.64")))
    assert detail["line_items"][0]["status"] == "uncertain"
    new = [{"description": "Flat white", "quantity": "2", "unit_price": "4.00", "line_total": "8.00"}]
    body = client.put(f"/api/receipts/{receipt_id}/line-items", json={"items": new}).json()
    assert body["line_items"][0]["status"] == "verified" and body["line_items"][0]["unit_price"] == "4"
    assert body["corrections"][-1]["field"] == "line_items"
    assert all(c["passed"] for c in body["checks"])
    bad = client.put(f"/api/receipts/{receipt_id}/line-items", json={"items": [{"description": "x", "line_total": "ten"}]})
    assert bad.status_code == 422


def test_probable_duplicate_is_flagged_and_resolvable(client):
    first_id, _ = upload_and_process(client, name="first.png")
    client.post(f"/api/receipts/{first_id}/approve", json={})
    second_id, detail = upload_and_process(client, name="rescan.png")  # different bytes, same facts
    assert detail["duplicate"]["status"] == "probable"
    assert detail["duplicate"]["of"]["id"] == first_id
    assert "Same currency and total." in detail["duplicate"]["reasons"]
    assert "resolve_duplicate" in detail["allowed_actions"]
    blocked = client.post(f"/api/receipts/{second_id}/approve", json={"note": "x"})
    assert blocked.status_code == 409
    dismissed = client.post(f"/api/receipts/{second_id}/duplicate", json={"resolution": "not_duplicate"}).json()
    assert dismissed["duplicate"]["status"] == "dismissed"
    assert not any(r["code"] == "probable_duplicate" for r in dismissed["review_reasons"])
    assert client.post(f"/api/receipts/{second_id}/approve", json={}).status_code == 200


def test_confirming_duplicate_rejects_without_deleting(client):
    first_id, _ = upload_and_process(client, name="first.png")
    second_id, _ = upload_and_process(client, name="second.png")
    body = client.post(f"/api/receipts/{second_id}/duplicate", json={"resolution": "duplicate"}).json()
    assert body["receipt"]["status"] == "rejected" and body["duplicate"]["status"] == "confirmed"
    assert client.get("/api/receipts").json()["total"] == 2  # nothing deleted


def test_reject_and_state_transitions(client):
    receipt_id, _ = upload_and_process(client)
    assert client.post(f"/api/receipts/{receipt_id}/reject", json={"note": "personal"}).json()["receipt"]["status"] == "rejected"
    res = client.post(f"/api/receipts/{receipt_id}/approve", json={})
    assert res.status_code == 409 and res.json()["error"]["code"] == "invalid_state"
    res = client.patch(f"/api/receipts/{receipt_id}/fields/total", json={"value": "1.00"})
    assert res.status_code == 409


def test_invalid_model_output_fails_safely_and_keeps_evidence(client):
    receipt_id = upload(client).json()["receipt"]["id"]
    process(ProviderResponse(text="{broken"), ProviderResponse(text='{"is_receipt": true}'))
    detail = client.get(f"/api/receipts/{receipt_id}").json()
    assert detail["receipt"]["status"] == "failed"
    assert detail["receipt"]["error_code"] == "schema_mismatch"
    assert detail["fields"] == [] and detail["extraction"] is None  # nothing became receipt data
    assert detail["extraction_history"][0]["schema_valid"] is False
    assert [c["status"] for c in detail["model_calls"]] == ["error", "error"]
    assert detail["allowed_actions"] == ["reprocess", "reject"]


def test_provider_outage_then_reprocess_creates_new_version(client):
    receipt_id = upload(client).json()["receipt"]["id"]
    process(*[ProviderError("server_error", "503", retryable=True) for _ in range(3)])
    failed = client.get(f"/api/receipts/{receipt_id}").json()
    assert failed["receipt"]["status"] == "failed" and failed["receipt"]["error_code"] == "server_error"
    assert len(failed["model_calls"]) == 3
    assert client.post(f"/api/receipts/{receipt_id}/reprocess", json={}).json()["receipt"]["status"] == "queued"
    process(response())
    done = client.get(f"/api/receipts/{receipt_id}").json()
    assert done["receipt"]["status"] == "needs_review" and done["extraction"]["version"] == 1
    assert len(done["model_calls"]) == 4


def test_missing_api_key_fails_with_clear_reason(client):
    receipt_id = upload(client).json()["receipt"]["id"]
    assert run_once() is True  # default gateway: no key configured in tests
    detail = client.get(f"/api/receipts/{receipt_id}").json()
    assert detail["receipt"]["status"] == "failed" and detail["receipt"]["error_code"] == "config_missing"


def test_unreadable_receipt_is_flagged(client):
    receipt_id, detail = upload_and_process(client, reply(readability="illegible", total=tv(None), subtotal=tv(None),
                                                          tax=tv(None), line_items=[]))
    codes = {r["code"] for r in detail["review_reasons"]}
    assert {"illegible", "missing_required"} <= codes


def test_pdf_upload_multi_page(client):
    res = client.post("/api/receipts", files={"file": ("bill.pdf", pdf_bytes(2), "application/pdf")})
    assert res.status_code == 202 and res.json()["receipt"]["page_count"] == 2
    provider = process(response())
    assert len(provider.requests[0].pages) == 2


def test_original_file_served_with_safe_headers(client):
    data = image_bytes(text="file")
    receipt_id = upload(client, data).json()["receipt"]["id"]
    res = client.get(f"/api/receipts/{receipt_id}/file")
    assert res.status_code == 200 and res.content == data
    assert res.headers["x-content-type-options"] == "nosniff"


def test_list_filters_and_search(client):
    a, _ = upload_and_process(client, reply(merchant_name=tv("Harbor Books"), total=tv("20.00"), subtotal=tv("20.00"),
                                            tax=tv(None), line_items=[]), name="a.png")
    b, _ = upload_and_process(client, name="b.png")
    assert client.get("/api/receipts", params={"q": "harbor"}).json()["total"] == 1
    assert client.get("/api/receipts", params={"merchant": "oak"}).json()["items"][0]["id"] == b
    assert client.get("/api/receipts", params={"status": "needs_review"}).json()["total"] == 2
    assert client.get("/api/receipts", params={"date_from": "2026-03-15"}).json()["total"] == 0
    assert client.get("/api/receipts", params={"status": "bogus"}).status_code == 422
    assert client.get("/api/review-queue").json()["total"] == 2
    assert client.get("/api/receipts/not-a-uuid").json()["error"]["code"] == "not_found"


def test_expenses_csv_dashboard_and_model_calls(client):
    receipt_id, _ = upload_and_process(client)
    pending = client.get("/api/dashboard").json()
    assert pending["counts"]["needs_review"] == 1 and pending["approved_spend"] == []
    assert pending["pending_review_totals"][0]["total_minor"] == 1323  # shown separately from approved spend
    client.post(f"/api/receipts/{receipt_id}/approve", json={})
    dash = client.get("/api/dashboard").json()
    assert dash["approved_spend"][0] == {"currency": "USD", "all_time_minor": 1323, "this_month_minor": 0, "count": 1}
    expenses = client.get("/api/expenses", params={"month": "2026-03"}).json()
    assert expenses["items"][0]["total"] == "13.23" and expenses["totals_by_currency"][0]["total_minor"] == 1323
    assert client.get("/api/expenses", params={"month": "2026-04"}).json()["items"] == []
    csv_text = client.get("/api/expenses/export.csv").text
    assert "Blue Oak Cafe" in csv_text and "13.23" in csv_text
    calls = client.get("/api/model-calls").json()
    assert calls["stats"]["calls"] == 1 and calls["stats"]["latency_p50_ms"] is not None


def test_csv_formula_injection_is_neutralised(client):
    receipt_id, _ = upload_and_process(client, reply(merchant_name=tv("=HYPERLINK(\"x\")")))
    client.post(f"/api/receipts/{receipt_id}/approve", json={})
    assert "'=HYPERLINK" in client.get("/api/expenses/export.csv").text


def test_access_code_gate(client, settings_override):
    from app.main import create_app
    from fastapi.testclient import TestClient

    settings_override(demo_access_code="letmein")
    with TestClient(create_app(start_worker=False)) as gated:
        assert gated.get("/api/health").status_code == 200
        denied = gated.get("/api/receipts")
        assert denied.status_code == 401 and denied.json()["error"]["code"] == "access_code_required"
        assert gated.get("/api/receipts", headers={"X-Access-Code": "letmein"}).status_code == 200


def test_upload_rate_limit(client, settings_override):
    settings_override(uploads_per_hour_per_client="2")
    assert upload(client, image_bytes(text="1")).status_code == 202
    assert upload(client, image_bytes(text="2")).status_code == 202
    res = upload(client, image_bytes(text="3"))
    assert res.status_code == 429 and res.json()["error"]["code"] == "rate_limited"
