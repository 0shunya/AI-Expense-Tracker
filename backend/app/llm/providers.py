"""Provider adapters. Only this module imports a vendor SDK; the rest of the app talks to ``Provider``."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.services.files import Page


class ProviderError(Exception):
    """A categorised provider failure. ``retryable`` decides whether the gateway may try again."""

    def __init__(self, category: str, message: str, *, retryable: bool, status_code: int | None = None,
                 retry_after: float | None = None, finish_reason: str | None = None):
        super().__init__(message)
        self.category = category
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after
        self.finish_reason = finish_reason


@dataclass
class ProviderRequest:
    system_instruction: str
    user_text: str
    pages: list[Page]
    response_schema: dict


@dataclass
class ProviderResponse:
    text: str
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    total_tokens: int | None = None


class Provider(Protocol):
    name: str
    model: str

    def generate(self, request: ProviderRequest) -> ProviderResponse: ...


_BLOCK_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION", "IMAGE_SAFETY",
                  "IMAGE_PROHIBITED_CONTENT", "OTHER"}


def _retry_after(details: object) -> float | None:
    """Gemini puts a RetryInfo.retryDelay like '23s' in 429 error details."""
    match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?([\d.]+)s", json.dumps(details, default=str))
    return float(match.group(1)) if match else None


def classify_status(code: int | None, message: str, details: object = None) -> ProviderError:
    text = (message or "")[:200]
    if code == 429:
        return ProviderError("rate_limited", f"Rate limited or quota exhausted: {text}", retryable=True,
                             status_code=code, retry_after=_retry_after(details))
    if code in (401, 403):
        return ProviderError("auth_error", "The Gemini API key was rejected (check GEMINI_API_KEY).",
                             retryable=False, status_code=code)
    if code == 404:
        return ProviderError("model_not_found", f"Model not found or not available to this key: {text}",
                             retryable=False, status_code=code)
    if code in (400, 413, 422):
        return ProviderError("bad_request", f"Request rejected by the provider: {text}", retryable=False, status_code=code)
    if code is not None and code >= 500:
        return ProviderError("server_error", f"Provider server error {code}.", retryable=True, status_code=code)
    return ProviderError("provider_error", f"Unexpected provider error: {text}", retryable=False, status_code=code)


class GeminiProvider:
    name = "google"

    def __init__(self, api_key: str, model: str, *, timeout_seconds: float, thinking_level: str = "low"):
        from google import genai
        from google.genai import types

        self._types = types
        self.model = model
        self.thinking_level = thinking_level
        self._client = genai.Client(
            api_key=api_key,
            # The gateway owns retries so every attempt is logged; disable the SDK's own retry loop.
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000),
                                           retry_options=types.HttpRetryOptions(attempts=1)),
        )

    def _thinking_config(self):
        types = self._types
        if self.model.startswith("gemini-2.5"):
            budgets = {"off": 0, "minimal": 0, "low": 1024, "medium": 4096, "high": 8192}
            budget = budgets[self.thinking_level]
            if budget == 0 and "pro" in self.model:
                budget = 128  # 2.5 Pro cannot disable thinking
            return types.ThinkingConfig(thinking_budget=budget)
        level = "minimal" if self.thinking_level == "off" else self.thinking_level
        return types.ThinkingConfig(thinking_level=level)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        from google.genai import errors

        types = self._types
        parts = [types.Part.from_text(text=request.user_text)]
        for index, page in enumerate(request.pages, start=1):
            if len(request.pages) > 1:
                parts.append(types.Part.from_text(text=f"Page {index}:"))
            parts.append(types.Part.from_bytes(data=page.data, mime_type=page.mime_type))
        config = types.GenerateContentConfig(
            system_instruction=request.system_instruction,
            response_mime_type="application/json",
            response_json_schema=request.response_schema,
            thinking_config=self._thinking_config(),
            max_output_tokens=32768,
        )
        try:
            response = self._client.models.generate_content(model=self.model, contents=parts, config=config)
        except errors.APIError as exc:
            raise classify_status(exc.code, exc.message or str(exc), getattr(exc, "details", None)) from exc
        except Exception as exc:  # network errors, timeouts
            name = type(exc).__name__.lower()
            if "timeout" in name or "timeout" in str(exc).lower():
                raise ProviderError("timeout", "The model call timed out.", retryable=True) from exc
            if any(k in name for k in ("connect", "network", "remoteprotocol", "readerror")):
                raise ProviderError("network_error", f"Network error contacting Gemini ({type(exc).__name__}).",
                                    retryable=True) from exc
            raise ProviderError("provider_error", f"Unexpected error: {type(exc).__name__}", retryable=False) from exc

        usage = response.usage_metadata
        meta = dict(
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            thinking_tokens=getattr(usage, "thoughts_token_count", None),
            total_tokens=getattr(usage, "total_token_count", None),
        )
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise ProviderError("blocked", f"The request was blocked ({feedback.block_reason}).", retryable=False)
        if not response.candidates:
            raise ProviderError("empty_response", "The model returned no candidates.", retryable=True)
        finish = response.candidates[0].finish_reason
        finish_name = getattr(finish, "name", None) or (str(finish) if finish else None)
        if finish_name == "MAX_TOKENS":
            raise ProviderError("truncated", "The response hit the output token limit and was cut off.",
                                retryable=False, finish_reason=finish_name)
        if finish_name in _BLOCK_REASONS:
            raise ProviderError("blocked", f"The model stopped with finish reason {finish_name}.",
                                retryable=False, finish_reason=finish_name)
        text = response.text or ""
        return ProviderResponse(text=text, finish_reason=finish_name, **meta)


@dataclass
class ScriptedProvider:
    """Deterministic provider for tests and offline demos: returns queued responses or raises queued errors."""

    script: list[ProviderResponse | ProviderError | Callable[[ProviderRequest], ProviderResponse]] = field(default_factory=list)
    name: str = "scripted"
    model: str = "scripted-model"
    requests: list[ProviderRequest] = field(default_factory=list)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if not self.script:
            raise ProviderError("provider_error", "ScriptedProvider has no more responses.", retryable=False)
        item = self.script.pop(0)
        if isinstance(item, ProviderError):
            raise item
        if callable(item):
            return item(request)
        return item
