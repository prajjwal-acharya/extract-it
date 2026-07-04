import os
import threading
import time
from typing import Callable

from watchdog.events import FileSystemEventHandler, FileCreatedEvent
from watchdog.observers import Observer


class _NewFileHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[str], None], settle_secs: float = 1.0) -> None:
        super().__init__()
        self._callback = callback
        self._settle_secs = settle_secs

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            path = event.src_path if isinstance(event.src_path, str) else event.src_path.decode()
            if self._settle_secs > 0:
                t = threading.Thread(target=self._settle_and_call, args=(path,), daemon=True)
                t.start()
            else:
                self._callback(path)

    def _settle_and_call(self, path: str) -> None:
        """Poll until file size is stable for settle_secs, then invoke callback."""
        prev_size = -1
        while True:
            try:
                cur_size = os.path.getsize(path)
            except OSError:
                return  # file disappeared before we could read it
            if cur_size == prev_size:
                break
            prev_size = cur_size
            time.sleep(self._settle_secs)
        self._callback(path)


class LocalWatchTrigger:
    """Trigger implementation that watches a local directory for new files."""

    def __init__(self, watch_dir: str, settle_secs: float = 1.0) -> None:
        os.makedirs(watch_dir, exist_ok=True)
        self._watch_dir = watch_dir
        self._settle_secs = settle_secs
        self._callback: Callable[[str], None] | None = None
        self._observer = Observer()

    def on_new_object(self, callback: Callable[[str], None]) -> None:
        self._callback = callback

    def start(self) -> None:
        if self._callback is None:
            raise RuntimeError("Register a callback with on_new_object() before calling start()")
        handler = _NewFileHandler(self._callback, settle_secs=self._settle_secs)
        self._observer.schedule(handler, self._watch_dir, recursive=False)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
