import os
from typing import Callable

from watchdog.events import FileSystemEventHandler, FileCreatedEvent
from watchdog.observers import Observer


class _NewFileHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self._callback = callback

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            path = event.src_path if isinstance(event.src_path, str) else event.src_path.decode()
            self._callback(path)


class LocalWatchTrigger:
    """Trigger implementation that watches a local directory for new files."""

    def __init__(self, watch_dir: str) -> None:
        os.makedirs(watch_dir, exist_ok=True)
        self._watch_dir = watch_dir
        self._callback: Callable[[str], None] | None = None
        self._observer = Observer()

    def on_new_object(self, callback: Callable[[str], None]) -> None:
        self._callback = callback

    def start(self) -> None:
        if self._callback is None:
            raise RuntimeError("Register a callback with on_new_object() before calling start()")
        handler = _NewFileHandler(self._callback)
        self._observer.schedule(handler, self._watch_dir, recursive=False)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
