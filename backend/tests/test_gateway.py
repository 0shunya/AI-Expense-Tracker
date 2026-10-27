"""Model gateway: response parsing, retry policy and call logging — with a scripted provider, no network."""

import json

import pytest

from app.llm.gateway import ExtractionFailed, ModelGateway, parse_reply
from app.llm.providers import ProviderError, ProviderResponse, classify_status
from app.services.files import Page
from app.services.receipt_data import from_extraction
from tests.factories import reply, response, scripted

PAGES = [Page(b"fake-jpeg", "image/jpeg")]


def make_gateway(provider, max_attempts=3):
    calls, sleeps = [], []
    gateway = ModelGateway(provider, calls.append, max_attempts=max_attempts, sleep=sleeps.append)
    return gateway, calls, sleeps


def test_parse_valid_reply_and_normalise():
    extraction, raw = parse_reply(json.dumps(reply()))
    data = from_extraction(extraction)
    assert raw["merchant_name"]["value"] == "Blue Oak Cafe"
    assert data.get("total") == "13.23" and data.get("date") == "2026-03-14"
    assert data.source_text["date"] == "14/03/2026"
    assert data.lines[0].quantity == 2


def test_parse_accepts_code_fences():
    extraction, _ = parse_reply("```json\n" + json.dumps(reply()) + "\n```")
    assert extraction.currency.value == "USD"


@pytest.mark.parametrize("text, category", [
    ("", "empty_response"),
    ("{not json", "invalid_json"),
    ("[1, 2]", "schema_mismatch"),
    (json.dumps({"merchant_name": {"value": "x"}}), "schema_mismatch"),
    (json.dumps(reply(readability="blurry")), "schema_mismatch"),
])
def test_parse_rejects_bad_output(text, category):
    from app.llm.gateway import InvalidOutput
    with pytest.raises(InvalidOutput) as info:
        parse_reply(text)
    assert info.value.category == category


def test_schema_valid_but_wrong_types_are_caught_by_normalisation():
    # Shape is valid JSON-schema-wise, but values are not usable numbers/dates: parse errors, not guesses.
    data = from_extraction(parse_reply(json.dumps(reply(total={"value": "1O.5O", "source_text": "1O.5O"},
                                                        transaction_date={"value": "March 14", "source_text": "March 14"})))[0])
    assert "total" in data.parse_errors and "date" in data.parse_errors


def test_success_records_tokens_and_latency():
    gateway, calls, _ = make_gateway(scripted(response(input_tokens=1500, output_tokens=420)))
    result = gateway.extract_receipt(PAGES, receipt_id=None, trace_id="t-1")
    assert result.attempts == 1 and result.trace_id == "t-1"
    assert len(calls) == 1 and calls[0].status == "success"
    assert calls[0].input_tokens == 1500 and calls[0].output_tokens == 420
    assert calls[0].prompt_version and calls[0].latency_ms >= 0


def test_transient_errors_are_retried_with_backoff():
    provider = scripted(ProviderError("server_error", "503", retryable=True, status_code=503),
                        ProviderError("rate_limited", "429", retryable=True, retry_after=7.0),
                        response())
    gateway, calls, sleeps = make_gateway(provider)
    result = gateway.extract_receipt(PAGES)
    assert result.attempts == 3
    assert [c.status for c in calls] == ["error", "error", "success"]
    assert [c.attempt for c in calls] == [1, 2, 3]
    assert sleeps[1] == 7.0  # honours the provider's retry delay
    assert len({c.trace_id for c in calls}) == 1


def test_retries_are_bounded():
    provider = scripted(*[ProviderError("timeout", "slow", retryable=True) for _ in range(5)])
    gateway, calls, _ = make_gateway(provider, max_attempts=3)
    with pytest.raises(ExtractionFailed) as info:
        gateway.extract_receipt(PAGES)
    assert info.value.category == "timeout" and len(calls) == 3


@pytest.mark.parametrize("category", ["auth_error", "bad_request", "model_not_found", "blocked", "truncated"])
def test_permanent_errors_are_not_retried(category):
    gateway, calls, _ = make_gateway(scripted(ProviderError(category, "nope", retryable=False), response()))
    with pytest.raises(ExtractionFailed) as info:
        gateway.extract_receipt(PAGES)
    assert info.value.category == category and len(calls) == 1


def test_invalid_output_gets_exactly_one_more_try():
    bad = ProviderResponse(text="{oops", finish_reason="STOP")
    gateway, calls, _ = make_gateway(scripted(bad, ProviderResponse(text="still not json"), response()), max_attempts=3)
    with pytest.raises(ExtractionFailed) as info:
        gateway.extract_receipt(PAGES)
    assert info.value.category == "invalid_json" and len(calls) == 2
    assert info.value.raw_text == "still not json"


def test_invalid_output_then_valid_succeeds():
    gateway, calls, _ = make_gateway(scripted(ProviderResponse(text="{oops"), response()))
    assert gateway.extract_receipt(PAGES).attempts == 2
    assert [c.error_category for c in calls] == ["invalid_json", None]


def test_logging_failure_never_breaks_extraction():
    def broken(_record):
        raise RuntimeError("db down")
    gateway = ModelGateway(scripted(response()), broken, sleep=lambda s: None)
    assert gateway.extract_receipt(PAGES).attempts == 1


@pytest.mark.parametrize("code, category, retryable", [
    (429, "rate_limited", True), (500, "server_error", True), (503, "server_error", True),
    (401, "auth_error", False), (403, "auth_error", False), (404, "model_not_found", False), (400, "bad_request", False),
])
def test_http_status_classification(code, category, retryable):
    error = classify_status(code, "message")
    assert (error.category, error.retryable) == (category, retryable)


def test_retry_delay_parsed_from_details():
    details = {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "23s"}]}}
    assert classify_status(429, "quota", details).retry_after == 23.0


def test_request_contains_schema_and_all_pages():
    provider = scripted(response())
    gateway, _, _ = make_gateway(provider)
    gateway.extract_receipt([Page(b"1", "image/jpeg"), Page(b"2", "image/jpeg")])
    request = provider.requests[0]
    assert len(request.pages) == 2 and "2 page" in request.user_text
    assert "total" in request.response_schema["properties"]
    assert "confidence" not in json.dumps(request.response_schema).lower()


def test_daily_quota_429_is_not_retried():
    details = {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
                                     {"retryDelay": "42021s"}]}}
    error = classify_status(429, "You exceeded your current quota", details)
    assert error.category == "quota_exhausted" and not error.retryable
    gateway, calls, _ = make_gateway(scripted(error, response()))
    with pytest.raises(ExtractionFailed):
        gateway.extract_receipt(PAGES)
    assert len(calls) == 1
