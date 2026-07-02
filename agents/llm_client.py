def generate(prompt: str, image_bytes: bytes | None = None) -> str:
    """Send a prompt (and optional image) to the configured Gemini model.

    Initialises a cached GenerativeModel on first call using settings.GEMINI_MODEL
    and returns the raw text response.
    """
    raise NotImplementedError
