from config.settings import Env, settings
from adapters.object_store.base import ObjectStore
from adapters.trigger.base import Trigger


def get_object_store() -> ObjectStore:
    if settings.ENV == Env.LOCAL:
        from adapters.object_store.minio_store import MinioStore

        return MinioStore()
    from adapters.object_store.gcs_store import GCSStore

    return GCSStore()


def get_trigger(watch_dir: str = "/tmp/watch") -> Trigger:
    if settings.ENV == Env.LOCAL:
        from adapters.trigger.local_watch import LocalWatchTrigger

        return LocalWatchTrigger(watch_dir)
    from adapters.trigger.pubsub_trigger import PubSubTrigger

    return PubSubTrigger()
