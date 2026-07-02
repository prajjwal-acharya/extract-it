import unittest.mock as mock

from adapters.object_store.base import ObjectStore
from adapters.object_store.minio_store import MinioStore
from adapters.trigger.base import Trigger
from adapters.trigger.local_watch import LocalWatchTrigger


def test_object_store_protocol_is_satisfied_by_minio_store() -> None:
    assert issubclass(MinioStore, ObjectStore)


def test_object_store_protocol_is_satisfied_by_gcs_store() -> None:
    raise NotImplementedError


def test_trigger_protocol_is_satisfied_by_local_watch() -> None:
    assert issubclass(LocalWatchTrigger, Trigger)


def test_trigger_protocol_is_satisfied_by_pubsub() -> None:
    raise NotImplementedError


def test_factory_returns_minio_store_in_local_env() -> None:
    with mock.patch("config.settings.settings.ENV", "LOCAL"):
        from adapters.factory import get_object_store
        with mock.patch.object(MinioStore, "__init__", return_value=None):
            store = get_object_store()
    assert isinstance(store, MinioStore)


def test_factory_returns_gcs_store_in_gcp_env() -> None:
    raise NotImplementedError


def test_factory_returns_local_watch_trigger_in_local_env() -> None:
    watch_dir = "/tmp/test_watch_trigger"
    with mock.patch("config.settings.settings.ENV", "LOCAL"):
        from adapters.factory import get_trigger
        with mock.patch.object(LocalWatchTrigger, "__init__", return_value=None):
            trigger = get_trigger(watch_dir)
    assert isinstance(trigger, LocalWatchTrigger)


def test_factory_returns_pubsub_trigger_in_gcp_env() -> None:
    raise NotImplementedError
