"""Upload validation (by content, not by extension) and page preprocessing for the vision model."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 60_000_000  # decompression-bomb guard
MAX_LONG_SIDE = 2048                 # cap the long side before sending to the model
MIN_SIDE = 120


class UploadRejected(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class UploadInfo:
    content_type: str
    page_count: int


@dataclass
class Page:
    data: bytes
    mime_type: str


def sniff_content_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:1024].lstrip().startswith(b"%PDF-"):
        return "application/pdf"
    return None


def inspect_upload(data: bytes, *, max_bytes: int, max_pdf_pages: int) -> UploadInfo:
    if not data:
        raise UploadRejected("empty_file", "The file is empty.")
    if len(data) > max_bytes:
        raise UploadRejected("file_too_large", f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.", 413)
    content_type = sniff_content_type(data)
    if content_type is None:
        raise UploadRejected("unsupported_type", "Only JPEG, PNG, WebP images and PDF documents are supported.", 415)
    if content_type == "application/pdf":
        return UploadInfo(content_type, _inspect_pdf(data, max_pdf_pages))
    _inspect_image(data)
    return UploadInfo(content_type, 1)


def _inspect_image(data: bytes) -> None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image.load()
    except Image.DecompressionBombError as exc:
        raise UploadRejected("image_too_large", "The image has too many pixels to process safely.", 413) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise UploadRejected("corrupt_image", "The image is corrupted or truncated and could not be decoded.") from exc
    if min(width, height) < MIN_SIDE:
        raise UploadRejected("image_too_small", f"The image is too small to read ({width}×{height} px).")


def _inspect_pdf(data: bytes, max_pages: int) -> int:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        text = str(exc).lower()
        if "password" in text:
            raise UploadRejected("encrypted_pdf", "The PDF is password-protected; upload an unlocked copy.") from exc
        raise UploadRejected("corrupt_pdf", "The PDF is damaged and could not be opened.") from exc
    try:
        pages = len(pdf)
        if pages == 0:
            raise UploadRejected("empty_pdf", "The PDF has no pages.")
        if pages > max_pages:
            raise UploadRejected("too_many_pages", f"The PDF has {pages} pages; the limit is {max_pages}.", 413)
        return pages
    finally:
        pdf.close()


def _encode(image: Image.Image) -> Page:
    image = image.convert("RGB")
    image.thumbnail((MAX_LONG_SIDE, MAX_LONG_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)  # re-encoding also strips EXIF metadata such as GPS
    return Page(buffer.getvalue(), "image/jpeg")


def prepare_pages(data: bytes, content_type: str) -> list[Page]:
    """Fix rotation, cap resolution, and split PDFs into one image per page."""
    if content_type == "application/pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        try:
            pages: list[Page] = []
            for page in pdf:
                width, height = page.get_size()
                scale = min(3.0, MAX_LONG_SIDE / max(width, height))
                pages.append(_encode(page.render(scale=scale).to_pil()))
                page.close()
            return pages
        finally:
            pdf.close()
    with Image.open(io.BytesIO(data)) as image:
        return [_encode(ImageOps.exif_transpose(image))]
