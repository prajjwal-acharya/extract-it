import time
from pathlib import Path
from typing import Callable
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent


class _Handler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[str], None]) -> None:
        self._callback = callback

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._callback(str(event.src_path))


class LocalWatchTrigger:
    def __init__(self, watch_dir: str) -> None:
        self._watch_dir = Path(watch_dir)
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._observer = Observer()
        self._callback: Callable[[str], None] | None = None

    def on_new_object(self, callback: Callable[[str], None]) -> None:
        self._callback = callback

    def start(self) -> None:
        assert self._callback, "Call on_new_object() before start()"
        self._observer.schedule(_Handler(self._callback), str(self._watch_dir), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
