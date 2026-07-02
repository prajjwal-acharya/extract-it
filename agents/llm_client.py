from functools import lru_cache

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
