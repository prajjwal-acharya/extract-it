from typing import Callable


class LocalWatchTrigger:
    """Trigger implementation that watches a local directory for new files."""

    def __init__(self, watch_dir: str) -> None:
        """Initialise watchdog observer for *watch_dir*, creating it if absent."""
        raise NotImplementedError

    def on_new_object(self, callback: Callable[[str], None]) -> None:
        """Register *callback* to be invoked with the path of each new file."""
        raise NotImplementedError

    def start(self) -> None:
        """Start the watchdog observer thread."""
        raise NotImplementedError

    def stop(self) -> None:
        """Stop and join the watchdog observer thread."""
        raise NotImplementedError
