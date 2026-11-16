"""Process-local admission for daily availability reads."""

from contextlib import contextmanager
from threading import Lock


class SlotsBusy(Exception):
    pass


class SlotAdmission:
    def __init__(self):
        self._lock = Lock()
        self._active = set()

    @contextmanager
    def admit(self, binding):
        with self._lock:
            if binding in self._active or len(self._active) >= 2:
                raise SlotsBusy()
            self._active.add(binding)
        try:
            yield
        finally:
            with self._lock:
                self._active.remove(binding)
