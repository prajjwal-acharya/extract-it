from functools import wraps
from typing import Callable, TypeVar, Any
from langsmith import traceable

F = TypeVar("F", bound=Callable[..., Any])


def trace(name: str | None = None) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        return traceable(name=name or fn.__name__)(fn)  # type: ignore[return-value]
    return decorator
