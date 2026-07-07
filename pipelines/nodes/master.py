import io
import logging

from adapters.factory import get_object_store
from pipelines.state import GraphState
from shared.utils.filename import parse_doc_type_from_filename

log = logging.getLogger(__name__)

# Pixel std-dev threshold below which an image is considered blank/solid-color.
# A pure black or white image has std=0; real documents typically score >20.
# Kept conservative (3.0) to avoid false positives on multi-page spreads or
# dark-background scans where half the image may be blank.
_BLANK_STD_THRESHOLD = 3.0


def _is_low_quality_image(raw_bytes: bytes, filename: str) -> bool:
    """Return True if the image appears blank, solid-color, or unreadable.

    Uses PIL pixel statistics — no LLM call required. Non-image files (PDFs
    handled by the LLM directly) are never flagged.
    """
    from shared.utils.mime import mime_from_filename

    mime = mime_from_filename(filename)
    if not mime.startswith("image/"):
        return False

    try:
        from PIL import Image, ImageStat

        img = Image.open(io.BytesIO(raw_bytes)).convert("L")
        stat = ImageStat.Stat(img)
        std = stat.stddev[0]
        log.info("event=ImageQualityCheck filename=%r pixel_stddev=%.2f", filename, std)
        return std < _BLANK_STD_THRESHOLD
    except Exception as exc:
        log.warning("event=ImageQualityCheckFailed filename=%r error=%s", filename, exc)
        return False


def master_node(state: GraphState) -> dict:
    """Fetch raw bytes once and pre-populate doc_type from filename when unambiguous.

    All downstream nodes (classify, extract) read state["raw_bytes"] rather than
    re-fetching — one object-store call per document regardless of retry count.

    Also runs a deterministic image quality check; blank/solid-color images are
    flagged via low_quality_image=True so the graph bypasses LLM calls entirely.
    """
    raw_bytes = get_object_store().get(state["object_key"])
    update: dict = {"raw_bytes": raw_bytes}
    doc_type = parse_doc_type_from_filename(state["filename"])
    if doc_type:
        update["doc_type"] = doc_type

    low_quality = _is_low_quality_image(raw_bytes, state["filename"])
    update["low_quality_image"] = low_quality
    if low_quality:
        log.warning(
            "event=LowQualityImageDetected document_id=%s filename=%r — routing to HITL",
            state.get("document_id"),
            state.get("filename"),
        )

    return update
