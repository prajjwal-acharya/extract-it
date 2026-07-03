import importlib
import sys
import unittest.mock as mock


def test_review_app_module_is_importable() -> None:
    """frontend/review_app.py can be imported without raising an ImportError."""
    st_mock = mock.MagicMock()
    st_mock.text_area.return_value = "{}"  # json.loads is called on this at module level

    # Remove any cached import so the mock takes effect cleanly
    sys.modules.pop("frontend.review_app", None)

    with mock.patch.dict("sys.modules", {"streamlit": st_mock}):
        mod = importlib.import_module("frontend.review_app")

    assert mod is not None
