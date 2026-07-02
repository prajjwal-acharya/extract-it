from typing import Callable, TypeVar, Any

F = TypeVar("F", bound=Callable[..., Any])


def trace(name: str | None = None) -> Callable[[F], F]:
    """Return a decorator that wraps *fn* with a named LangSmith trace span."""
    raise NotImplementedError
