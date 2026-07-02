def test_object_store_protocol_is_satisfied_by_minio_store() -> None:
    """MinioStore implements every method declared in the ObjectStore Protocol."""
    raise NotImplementedError


def test_object_store_protocol_is_satisfied_by_gcs_store() -> None:
    """GCSStore implements every method declared in the ObjectStore Protocol."""
    raise NotImplementedError


def test_trigger_protocol_is_satisfied_by_local_watch() -> None:
    """LocalWatchTrigger implements every method declared in the Trigger Protocol."""
    raise NotImplementedError


def test_trigger_protocol_is_satisfied_by_pubsub() -> None:
    """PubSubTrigger implements every method declared in the Trigger Protocol."""
    raise NotImplementedError


def test_factory_returns_minio_store_in_local_env() -> None:
    """get_object_store() returns a MinioStore when ENV=LOCAL."""
    raise NotImplementedError


def test_factory_returns_gcs_store_in_gcp_env() -> None:
    """get_object_store() returns a GCSStore when ENV=GCP."""
    raise NotImplementedError


def test_factory_returns_local_watch_trigger_in_local_env() -> None:
    """get_trigger() returns a LocalWatchTrigger when ENV=LOCAL."""
    raise NotImplementedError


def test_factory_returns_pubsub_trigger_in_gcp_env() -> None:
    """get_trigger() returns a PubSubTrigger when ENV=GCP."""
    raise NotImplementedError
