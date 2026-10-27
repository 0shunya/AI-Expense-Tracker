import io

import pytest
from PIL import Image

from app.services.files import UploadRejected, inspect_upload, prepare_pages, sniff_content_type
from tests.factories import image_bytes, pdf_bytes

LIMIT = 10 * 1024 * 1024


@pytest.mark.parametrize("fmt, mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_supported_images(fmt, mime):
    info = inspect_upload(image_bytes(fmt), max_bytes=LIMIT, max_pdf_pages=5)
    assert info.content_type == mime and info.page_count == 1


def test_pdf_page_count_and_split():
    data = pdf_bytes(3)
    info = inspect_upload(data, max_bytes=LIMIT, max_pdf_pages=5)
    assert info.content_type == "application/pdf" and info.page_count == 3
    pages = prepare_pages(data, info.content_type)
    assert len(pages) == 3 and all(p.mime_type == "image/jpeg" for p in pages)


def test_type_is_detected_from_content_not_extension():
    assert sniff_content_type(b"GIF89a....") is None
    with pytest.raises(UploadRejected) as info:
        inspect_upload(b"MZ\x90\x00 executable pretending to be receipt.jpg", max_bytes=LIMIT, max_pdf_pages=5)
    assert info.value.code == "unsupported_type" and info.value.status_code == 415


def test_empty_and_oversized():
    with pytest.raises(UploadRejected, match="empty"):
        inspect_upload(b"", max_bytes=LIMIT, max_pdf_pages=5)
    with pytest.raises(UploadRejected) as info:
        inspect_upload(image_bytes(), max_bytes=100, max_pdf_pages=5)
    assert info.value.code == "file_too_large" and info.value.status_code == 413


def test_truncated_image_is_rejected():
    data = image_bytes("JPEG", size=(800, 1000))
    with pytest.raises(UploadRejected) as info:
        inspect_upload(data[: len(data) // 3], max_bytes=LIMIT, max_pdf_pages=5)
    assert info.value.code == "corrupt_image"


def test_corrupt_pdf_and_too_many_pages():
    with pytest.raises(UploadRejected) as info:
        inspect_upload(b"%PDF-1.7\n garbage garbage", max_bytes=LIMIT, max_pdf_pages=5)
    assert info.value.code == "corrupt_pdf"
    with pytest.raises(UploadRejected) as info:
        inspect_upload(pdf_bytes(3), max_bytes=LIMIT, max_pdf_pages=2)
    assert info.value.code == "too_many_pages"


def test_tiny_image_rejected():
    with pytest.raises(UploadRejected) as info:
        inspect_upload(image_bytes(size=(50, 50)), max_bytes=LIMIT, max_pdf_pages=5)
    assert info.value.code == "image_too_small"


def test_preprocessing_caps_resolution_and_applies_exif_rotation():
    image = Image.new("RGB", (4000, 1000), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° clockwise on display
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    page = prepare_pages(buffer.getvalue(), "image/jpeg")[0]
    with Image.open(io.BytesIO(page.data)) as out:
        assert max(out.size) == 2048
        assert out.height > out.width  # rotated upright
        assert not out.getexif()       # metadata stripped
