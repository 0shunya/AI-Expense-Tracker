"""Live Gemini check, excluded from the default run. Uses real quota:  pytest -m live"""

import os

import pytest

from app.llm.gateway import ModelGateway, build_provider
from app.services.files import prepare_pages
from app.services.receipt_data import from_extraction
from tests.factories import image_bytes

pytestmark = pytest.mark.live


def test_gemini_returns_schema_valid_extraction(settings_override):
    key = os.environ.get("LIVE_GEMINI_API_KEY")
    if not key:
        pytest.skip("set LIVE_GEMINI_API_KEY to run the live Gemini test")
    settings_override(gemini_api_key=key)
    data = image_bytes("PNG", text="CORNER CAFE\nLatte 4.50\nTOTAL USD 4.50", size=(500, 400))
    calls = []
    result = ModelGateway(build_provider(), calls.append, max_attempts=3).extract_receipt(prepare_pages(data, "image/png"))
    assert result.extraction.is_receipt in (True, False)
    assert calls[-1].status == "success" and calls[-1].input_tokens
    from_extraction(result.extraction)  # normalisation never raises on schema-valid output
