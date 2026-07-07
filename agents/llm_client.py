import io
import ssl
import time

import httpx
import numpy as np
from google import genai
from google.genai import types
from PIL import Image

from config.settings import settings

# Images larger than this are downscaled before being sent inline to Gemini.
# Primary protection against Docker MTU fragmentation on large payloads;
# secondary protection against Gemini inline-data size limits.
_MAX_IMAGE_BYTES = 3 * 1024 * 1024  # 3 MB
_MAX_IMAGE_DIM = 2048  # px on the long edge


def _compress_image(data: bytes, mime_type: str) -> tuple[bytes, str]:
    """Downscale and re-encode an image if it exceeds _MAX_IMAGE_BYTES.

    Returns (possibly-compressed bytes, actual mime_type).
    PDFs are returned unchanged — Gemini handles them natively.
    """
    if mime_type == "application/pdf" or len(data) <= _MAX_IMAGE_BYTES:
        return data, mime_type

    img = Image.open(io.BytesIO(data))
    # Downscale if either dimension exceeds the cap
    w, h = img.size
    if max(w, h) > _MAX_IMAGE_DIM:
        scale = _MAX_IMAGE_DIM / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # Re-encode as JPEG (universal, smaller than PNG)
    buf = io.BytesIO()
    rgb = img.convert("RGB")
    quality = 85
    rgb.save(buf, format="JPEG", quality=quality, optimize=True)
    compressed = buf.getvalue()

    # If still too large, drop quality iteratively
    while len(compressed) > _MAX_IMAGE_BYTES and quality > 40:
        quality -= 15
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=quality, optimize=True)
        compressed = buf.getvalue()

    return compressed, "image/jpeg"

_RETRYABLE = (
    ssl.SSLError,
    TimeoutError,
    ConnectionError,
    OSError,
)
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5  # seconds


def _with_retry(fn):
    """Retry fn on transient network/SSL errors with exponential backoff."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn()
        except _RETRYABLE:
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(_BACKOFF_BASE ** attempt)
            continue
        except Exception as exc:
            # also retry on google-genai server errors (5xx)
            msg = str(exc).lower()
            if any(t in msg for t in ("503", "500", "internal", "unavailable", "bad record mac", "server disconnected", "disconnected without sending", "empty response from model")):
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(_BACKOFF_BASE ** attempt)
                continue
            raise


def _client() -> genai.Client:
    # HTTPTransport(retries=3) makes httpx transparently retry on stale
    # keep-alive connections (root cause of SSLV3_ALERT_BAD_RECORD_MAC).
    # keepalive_expiry=4s drops pooled connections quickly — pipeline stages
    # take 10–60s each, so most pooled connections would be server-side dead
    # anyway; short expiry avoids the stale-pool TLS alert.
    transport = httpx.HTTPTransport(retries=3)
    http_client = httpx.Client(
        transport=transport,
        timeout=120.0,
        limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=4.0),
    )
    return genai.Client(
        api_key=settings.GOOGLE_API_KEY,
        http_options=types.HttpOptions(httpx_client=http_client),
    )


def generate(
    prompt: str,
    image_bytes: bytes | None = None,
    mime_type: str = "application/pdf",
    response_schema: type | None = None,
    model: str | None = None,
) -> str:
    """Send a prompt (and optional image/doc bytes) to the configured Gemini model.

    model overrides settings.GEMINI_MODEL when provided — used by MODEL_ESCALATION
    to route the escalated retry through a higher-tier model.
    """
    contents: list = [prompt]
    if image_bytes is not None:
        image_bytes, mime_type = _compress_image(image_bytes, mime_type)
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    config = None
    if response_schema is not None:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
        )

    response = _with_retry(lambda: _client().models.generate_content(
        model=model or settings.GEMINI_MODEL,
        contents=contents,
        config=config,
    ))
    text = response.text
    if not text:
        raise RuntimeError("empty response from model")
    return text


def generate_with_tools(
    prompt: str,
    declarations: list,
    fn_registry: dict,
    max_tool_calls: int = 3,
) -> tuple[str, int, list[dict]]:
    """Run a Gemini tool-calling loop; returns (final_text, calls_made, tool_results).

    declarations: list of types.FunctionDeclaration objects
    fn_registry:  {name: callable} for dispatching function calls
    max_tool_calls: hard cap — once reached the loop exits without another model call
    tool_results: [{"name": str, "result": dict}, ...] in call order
    """
    tool = types.Tool(function_declarations=declarations)
    contents: list = [prompt]
    calls_made = 0
    final_text = ""
    tool_results: list[dict] = []

    for _ in range(max_tool_calls + 1):
        _contents = contents  # capture for lambda
        response = _with_retry(lambda: _client().models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=_contents,
            config=types.GenerateContentConfig(tools=[tool]),
        ))
        candidate = response.candidates[0] if response.candidates else None
        if candidate is None:
            break

        content = candidate.content
        if content is None:
            break
        fn_parts = [p for p in (content.parts or []) if getattr(p, "function_call", None)]
        if not fn_parts or calls_made >= max_tool_calls:
            final_text = response.text or ""
            break

        contents.append(content)
        fn_response_parts: list = []
        for part in fn_parts:
            fc = part.function_call
            if fc is None:
                continue
            name: str = str(fc.name)
            args: dict = dict(fc.args) if fc.args is not None else {}
            if name in fn_registry and calls_made < max_tool_calls:
                result = fn_registry[name](**args)
                calls_made += 1
                tool_results.append({"name": name, "result": result})
            else:
                result = {"error": f"unknown function {name!r}"}
            fn_response_parts.append(types.Part.from_function_response(name=name, response=result))
        contents.append(types.Content(parts=fn_response_parts))

    return final_text, calls_made, tool_results


def embed(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Return an L2-normalized embedding for text at EMBEDDING_DIMENSIONS dims.

    gemini-embedding-001 defaults to 3072 dims and is NOT normalized below
    that default — must normalize manually for correct cosine similarity.
    """
    response = _with_retry(lambda: _client().models.embed_content(
        model=settings.GEMINI_EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            output_dimensionality=settings.EMBEDDING_DIMENSIONS,
            task_type=task_type,
        ),
    ))
    embeddings = response.embeddings or []
    vec = np.array(embeddings[0].values)
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist() if norm > 0 else vec.tolist()
