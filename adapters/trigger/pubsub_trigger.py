"""GCP Pub/Sub trigger — stub until P9."""
from typing import Callable


class PubSubTrigger:
    def on_new_object(self, callback: Callable[[str], None]) -> None:
        raise NotImplementedError("PubSub trigger not active until P9")

    def start(self) -> None:
        raise NotImplementedError("PubSub trigger not active until P9")

    def stop(self) -> None:
        raise NotImplementedError("PubSub trigger not active until P9")
