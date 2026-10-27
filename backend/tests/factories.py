"""Test helpers: tiny receipt images/PDFs and model replies in the exact schema shape."""

from __future__ import annotations

import copy
import io
import json

from PIL import Image, ImageDraw

from app.llm.providers import ProviderResponse, ScriptedProvider


def image_bytes(fmt: str = "PNG", text: str = "RECEIPT", size=(400, 600), color="white") -> bytes:
    image = Image.new("RGB", size, color)
    ImageDraw.Draw(image).text((20, 20), text, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def pdf_bytes(pages: int = 2) -> bytes:
    images = [Image.new("RGB", (400, 600), "white") for _ in range(pages)]
    for index, image in enumerate(images):
        ImageDraw.Draw(image).text((20, 20), f"page {index + 1}", fill="black")
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def tv(value, source=None):
    return {"value": value, "source_text": source if source is not None else value}


BASE_REPLY = {
    "is_receipt": True,
    "readability": "clear",
    "merchant_name": tv("Blue Oak Cafe"),
    "merchant_address": tv("12 Harbour St"),
    "transaction_date": tv("2026-03-14", "14/03/2026"),
    "transaction_time": tv("10:42"),
    "currency": tv("USD"),
    "subtotal": tv("12.25"),
    "tax": tv("0.98"),
    "tip": tv(None),
    "discount": tv(None),
    "service_charge": tv(None),
    "total": tv("13.23"),
    "tax_included_in_prices": None,
    "payment_method": tv("VISA"),
    "transaction_reference": tv("A19-55821"),
    "gstin": tv(None),
    "line_items": [
        {"description": "Flat white", "quantity": "2", "unit_price": "4.50", "line_total": "9.00",
         "discount": None, "tax": None, "source_text": "Flat white x2 4.50 9.00"},
        {"description": "Croissant", "quantity": "1", "unit_price": "3.25", "line_total": "3.25",
         "discount": None, "tax": None, "source_text": "Croissant 3.25"},
    ],
    "category_suggestion": "dining",
    "notes": [],
}


def reply(**overrides) -> dict:
    data = copy.deepcopy(BASE_REPLY)
    for key, value in overrides.items():
        data[key] = value
    return data


def response(data: dict | None = None, **usage) -> ProviderResponse:
    return ProviderResponse(text=json.dumps(data if data is not None else reply()), finish_reason="STOP",
                            input_tokens=usage.get("input_tokens", 1200), output_tokens=usage.get("output_tokens", 400),
                            total_tokens=usage.get("total_tokens", 1600))


def scripted(*items) -> ScriptedProvider:
    return ScriptedProvider(script=list(items))


def gateway_factory(provider: ScriptedProvider):
    from app.llm.gateway import ModelGateway
    from app.services.observability import record_call_to_db

    return lambda: ModelGateway(provider, record_call_to_db, max_attempts=3, sleep=lambda _s: None)
