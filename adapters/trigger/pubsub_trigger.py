from typing import Callable


class PubSubTrigger:
    """Trigger implementation backed by GCP Pub/Sub (used when ENV=GCP)."""

    def on_new_object(self, callback: Callable[[str], None]) -> None:
        """Register *callback* to be invoked with the GCS object path from each Pub/Sub message."""
        raise NotImplementedError

    def start(self) -> None:
        """Start streaming pull from the configured Pub/Sub subscription."""
        raise NotImplementedError

    def stop(self) -> None:
        """Cancel the streaming pull and release resources."""
        raise NotImplementedError
