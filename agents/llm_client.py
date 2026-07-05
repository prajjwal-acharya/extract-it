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
    model: str | None = None,
) -> str:
    """Send a prompt (and optional image/doc bytes) to the configured Gemini model.

    model overrides settings.GEMINI_MODEL when provided — used by MODEL_ESCALATION
    to route the escalated retry through a higher-tier model.
    """
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
        model=model or settings.GEMINI_MODEL,
        contents=contents,
        config=config,
    )
    return response.text or ""


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
        response = _client().models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(tools=[tool]),
        )
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
    response = _client().models.embed_content(
        model=settings.GEMINI_EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            output_dimensionality=settings.EMBEDDING_DIMENSIONS,
            task_type=task_type,
        ),
    )
    embeddings = response.embeddings or []
    vec = np.array(embeddings[0].values)
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist() if norm > 0 else vec.tolist()
