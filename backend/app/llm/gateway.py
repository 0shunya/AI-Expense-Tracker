"""The model gateway: one door for every model call.

It builds the request, calls the provider, parses and schema-validates the reply, retries only what is
worth retrying (bounded, exponential backoff with jitter), and records every attempt — success or failure —
through ``record_call``. Business code never touches a vendor SDK.
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.llm.prompts import PROMPT_VERSION, SYSTEM_INSTRUCTION, USER_TEXT
from app.llm.providers import GeminiProvider, Provider, ProviderError, ProviderRequest, ProviderResponse
from app.llm.schema import ReceiptExtraction, response_json_schema
from app.services.files import Page

log = logging.getLogger(__name__)

FEATURE = "receipt_extraction"
INVALID_OUTPUT_RETRIES = 1  # malformed/schema-invalid replies get one more try, never an endless loop
MAX_BACKOFF_SECONDS = 30.0


@dataclass
class CallRecord:
    trace_id: str
    receipt_id: str | None
    feature: str
    provider: str
    model: str
    prompt_version: str
    attempt: int
    status: str                 # success | error
    latency_ms: int
    error_category: str | None = None
    error_message: str | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class ExtractionResult:
    extraction: ReceiptExtraction
    raw: dict
    trace_id: str
    attempts: int
    latency_ms: int
    provider: str
    model: str
    prompt_version: str = PROMPT_VERSION
    input_tokens: int | None = None
    output_tokens: int | None = None


class ExtractionFailed(Exception):
    def __init__(self, category: str, message: str, *, trace_id: str, attempts: int,
                 raw_text: str | None = None, schema_errors: list | None = None):
        super().__init__(message)
        self.category = category
        self.message = message
        self.trace_id = trace_id
        self.attempts = attempts
        self.raw_text = raw_text
        self.schema_errors = schema_errors


class InvalidOutput(Exception):
    def __init__(self, category: str, message: str, raw_text: str, schema_errors: list | None = None):
        super().__init__(message)
        self.category = category
        self.message = message
        self.raw_text = raw_text
        self.schema_errors = schema_errors


def parse_reply(text: str) -> tuple[ReceiptExtraction, dict]:
    """Second line of defence after provider-side structured output: JSON parse + Pydantic validation."""
    if not text or not text.strip():
        raise InvalidOutput("empty_response", "The model returned an empty response.", text or "")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise InvalidOutput("invalid_json", f"The response was not valid JSON ({exc.msg}).", text) from exc
    if not isinstance(raw, dict):
        raise InvalidOutput("schema_mismatch", "The response JSON was not an object.", text)
    try:
        return ReceiptExtraction.model_validate(raw), raw
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()[:20]]
        raise InvalidOutput("schema_mismatch", f"The response did not match the receipt schema ({len(exc.errors())} error(s)).",
                            text, errors) from exc


def _backoff(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(retry_after, MAX_BACKOFF_SECONDS)
    return min(MAX_BACKOFF_SECONDS, (2 ** (attempt - 1)) + random.uniform(0, 0.5))


def build_provider(settings: Settings | None = None) -> Provider:
    settings = settings or get_settings()
    if not settings.gemini_api_key:
        raise ProviderError("config_missing", "GEMINI_API_KEY is not set on the server.", retryable=False)
    return GeminiProvider(settings.gemini_api_key, settings.gemini_model,
                          timeout_seconds=settings.model_timeout_seconds,
                          thinking_level=settings.gemini_thinking_level)


@dataclass
class ModelGateway:
    provider: Provider
    record_call: Callable[[CallRecord], None]
    max_attempts: int = 3
    sleep: Callable[[float], None] = field(default=time.sleep)

    def extract_receipt(self, pages: list[Page], *, receipt_id: str | None = None,
                        trace_id: str | None = None) -> ExtractionResult:
        trace_id = trace_id or str(uuid.uuid4())
        request = ProviderRequest(SYSTEM_INSTRUCTION, USER_TEXT.format(pages=len(pages)), pages, response_json_schema())
        invalid_retries_left = INVALID_OUTPUT_RETRIES
        last_error: ExtractionFailed | None = None
        total_latency = 0
        for attempt in range(1, self.max_attempts + 1):
            started = time.perf_counter()
            response: ProviderResponse | None = None
            try:
                response = self.provider.generate(request)
                extraction, raw = parse_reply(response.text)
            except ProviderError as exc:
                latency = round((time.perf_counter() - started) * 1000)
                total_latency += latency
                self._record(trace_id, receipt_id, attempt, "error", latency, exc.category, exc.message,
                             exc.finish_reason, None)
                last_error = ExtractionFailed(exc.category, exc.message, trace_id=trace_id, attempts=attempt)
                if not exc.retryable or attempt == self.max_attempts:
                    raise last_error from exc
                delay = _backoff(attempt, exc.retry_after)
                log.warning("model call failed; retrying", extra={"trace_id": trace_id, "attempt": attempt,
                                                                   "error_category": exc.category, "delay_s": round(delay, 2)})
                self.sleep(delay)
                continue
            except InvalidOutput as exc:
                latency = round((time.perf_counter() - started) * 1000)
                total_latency += latency
                self._record(trace_id, receipt_id, attempt, "error", latency, exc.category, exc.message,
                             response.finish_reason if response else None, response)
                last_error = ExtractionFailed(exc.category, exc.message, trace_id=trace_id, attempts=attempt,
                                              raw_text=exc.raw_text[:20000], schema_errors=exc.schema_errors)
                if invalid_retries_left <= 0 or attempt == self.max_attempts:
                    raise last_error from exc
                invalid_retries_left -= 1
                continue
            latency = round((time.perf_counter() - started) * 1000)
            total_latency += latency
            self._record(trace_id, receipt_id, attempt, "success", latency, None, None, response.finish_reason, response)
            return ExtractionResult(extraction, raw, trace_id, attempt, latency, self.provider.name,
                                    self.provider.model, input_tokens=response.input_tokens,
                                    output_tokens=response.output_tokens)
        assert last_error is not None
        raise last_error

    def _record(self, trace_id, receipt_id, attempt, status, latency, category, message, finish, response) -> None:
        record = CallRecord(
            trace_id=trace_id, receipt_id=receipt_id, feature=FEATURE, provider=self.provider.name,
            model=self.provider.model, prompt_version=PROMPT_VERSION, attempt=attempt, status=status,
            latency_ms=latency, error_category=category, error_message=(message or None) and message[:300],
            finish_reason=finish,
            input_tokens=getattr(response, "input_tokens", None), output_tokens=getattr(response, "output_tokens", None),
            thinking_tokens=getattr(response, "thinking_tokens", None), total_tokens=getattr(response, "total_tokens", None),
        )
        try:
            self.record_call(record)
        except Exception:  # observability must never break extraction
            log.exception("failed to record model call", extra={"trace_id": trace_id})
