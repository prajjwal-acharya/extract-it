from functools import lru_cache

import numpy as np
from google import genai
from google.genai import types

from config.settings import settings


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    return genai.Client(api_key=settings.GOOGLE_API_KEY)


def generate(
    prompt: str,
    image_bytes: bytes | None = None,
    mime_type: str = "application/pdf",
    response_schema: type | None = None,
) -> str:
    """Send a prompt (and optional image/doc bytes) to the configured Gemini model."""
    contents: list = [prompt]
    if image_bytes is not None:
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    config = None
    if response_schema is not None:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
        )

    response = _client().models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=config,
    )
    return response.text or ""


def embed(text: str) -> list[float]:
    """Return an L2-normalized embedding for text at EMBEDDING_DIMENSIONS dims.

    gemini-embedding-001 defaults to 3072 dims and is NOT normalized below
    that default — must normalize manually for correct cosine similarity.
    """
    response = _client().models.embed_content(
        model=settings.GEMINI_EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=settings.EMBEDDING_DIMENSIONS),
    )
    embeddings = response.embeddings or []
    vec = np.array(embeddings[0].values)
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist() if norm > 0 else vec.tolist()
