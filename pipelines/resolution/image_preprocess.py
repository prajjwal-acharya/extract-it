"""IMAGE_PREPROCESS strategy — deterministic image/PDF preprocessing.

All operations are fully deterministic (no LLM, no learning). They are applied
in the order given by DirectiveEngine.to_preprocessing_ops(). The preprocessed
bytes replace raw_bytes for the next extraction pass only — they are cleared
from state after consumption.

Available operations:
  contrast_enhance  — increase image contrast using PIL ImageEnhance
  sharpen           — increase sharpness using PIL ImageEnhance
  denoise           — median filter for noise reduction via PIL ImageFilter
  render_hires      — re-render PDF pages at 300 DPI using PyMuPDF

PDF inputs are rendered to PNG by PyMuPDF before PIL operations, then returned
as PNG bytes with "image/png" mime type so the LLM receives a clean raster.
Image inputs (JPEG, PNG, TIFF) are processed directly by PIL.
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PIL operation implementations
# ---------------------------------------------------------------------------


def _apply_contrast(img):  # type: ignore[no-untyped-def]
    from PIL import ImageEnhance

    return ImageEnhance.Contrast(img).enhance(2.0)


def _apply_sharpen(img):  # type: ignore[no-untyped-def]
    from PIL import ImageEnhance

    return ImageEnhance.Sharpness(img).enhance(2.5)


def _apply_denoise(img):  # type: ignore[no-untyped-def]
    from PIL import ImageFilter

    return img.filter(ImageFilter.MedianFilter(size=3))


_PIL_OPS: dict[str, object] = {
    "contrast_enhance": _apply_contrast,
    "sharpen": _apply_sharpen,
    "denoise": _apply_denoise,
}


# ---------------------------------------------------------------------------
# PDF high-resolution rendering
# ---------------------------------------------------------------------------


def _render_pdf_hires(raw_bytes: bytes, dpi: int = 300) -> bytes:
    """Render each PDF page at `dpi` and combine into a multi-page PNG.

    Returns PNG bytes of the first page (most identity documents are single page).
    Falls back to the original bytes on any error.
    """
    try:
        import fitz  # type: ignore[import-untyped]

        doc = fitz.open(stream=raw_bytes, filetype="pdf")
        if doc.page_count == 0:
            return raw_bytes
        page = doc[0]  # identity documents are almost always single-page
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        return pix.tobytes("png")
    except Exception as e:
        log.warning("render_hires failed: %s", e)
        return raw_bytes


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

_PDF_MIME = "application/pdf"
_PNG_MIME = "image/png"


class ImagePreprocessStrategy:
    """Applies deterministic preprocessing operations to raw document bytes.

    The strategy converts PDF to PNG before applying PIL operations because
    PIL cannot process PDF bytes directly. Image inputs are processed in-place.

    All operations are applied in the order given by `operations`. Unknown
    operation names are skipped with a warning so the pipeline never breaks
    when a new operation is added before all deployments are updated.
    """

    def preprocess(
        self,
        raw_bytes: bytes,
        mime_type: str,
        operations: list[str],
    ) -> tuple[bytes, str, list[str]]:
        """Apply operations and return (processed_bytes, output_mime_type, ops_applied).

        output_mime_type is "image/png" when a PDF was rasterised, otherwise
        the input mime_type is preserved.
        """
        if not operations:
            return raw_bytes, mime_type, []

        applied: list[str] = []
        current_bytes = raw_bytes
        current_mime = mime_type

        # Handle PDF: render_hires must run first to convert to PNG for PIL
        if "render_hires" in operations and current_mime == _PDF_MIME:
            current_bytes = _render_pdf_hires(current_bytes)
            current_mime = _PNG_MIME
            applied.append("render_hires")
        elif current_mime == _PDF_MIME and any(op in _PIL_OPS for op in operations):
            # Implicit render if PIL ops are requested on a PDF
            current_bytes = _render_pdf_hires(current_bytes)
            current_mime = _PNG_MIME
            applied.append("render_hires_implicit")

        # Apply PIL operations
        pil_ops = [op for op in operations if op in _PIL_OPS and op != "render_hires"]
        if pil_ops and current_mime in (_PNG_MIME, "image/jpeg", "image/tiff"):
            try:
                from PIL import Image

                img = Image.open(io.BytesIO(current_bytes)).convert("RGB")
                for op in pil_ops:
                    fn = _PIL_OPS.get(op)
                    if fn is None:
                        log.warning("image_preprocess: unknown op %r — skipped", op)
                        continue
                    img = fn(img)  # type: ignore[operator]
                    applied.append(op)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                current_bytes = buf.getvalue()
                current_mime = _PNG_MIME
            except Exception as e:
                log.warning("image_preprocess PIL operations failed: %s", e)

        skipped = [op for op in operations if op not in applied and op != "render_hires"]
        if skipped:
            log.debug(
                "image_preprocess: skipped ops %s (no PIL/fitz or PDF was not rasterised)", skipped
            )

        return current_bytes, current_mime, applied
