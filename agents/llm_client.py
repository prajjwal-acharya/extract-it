import google.generativeai as genai
from config.settings import settings

_model: genai.GenerativeModel | None = None


def get_model() -> genai.GenerativeModel:
    global _model
    if _model is None:
        _model = genai.GenerativeModel(settings.GEMINI_MODEL)
    return _model


def generate(prompt: str, image_bytes: bytes | None = None) -> str:
    model = get_model()
    parts: list = [prompt]
    if image_bytes:
        parts.append({"mime_type": "image/jpeg", "data": image_bytes})
    response = model.generate_content(parts)
    return response.text
