def test_setup_langsmith_sets_env_vars_when_key_present() -> None:
    """setup_langsmith() sets LANGCHAIN_TRACING_V2 and LANGCHAIN_API_KEY when the key is configured."""
    raise NotImplementedError


def test_setup_langsmith_is_noop_without_api_key() -> None:
    """setup_langsmith() does not set env vars when LANGCHAIN_API_KEY is empty."""
    raise NotImplementedError


def test_trace_decorator_wraps_function() -> None:
    """trace() returns a decorator that preserves the wrapped function's __name__."""
    raise NotImplementedError
