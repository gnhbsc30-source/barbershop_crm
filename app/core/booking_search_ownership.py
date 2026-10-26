"""Bounded single-process application ownership/admission; no booking rules."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from app.services.alternative_selection import AlternativeSelectionError


class SearchAdmissionError(ValueError):
    def __init__(self, global_limit=False):
        self.status = 503 if global_limit else 429
        self.code = "SEARCH_CAPACITY_REACHED" if global_limit else "SEARCH_ADMISSION_LIMITED"
        super().__init__(self.code)


@dataclass(frozen=True)
class SearchOwner:
    subject: str
    business_id: int
    customer_id: int
    session_binding: str = field(repr=False)


@dataclass(frozen=True)
class OwnedSearch:
    owner: SearchOwner
    expires_at: datetime


class BookingSearchOwnership:
    MAX_PER_SESSION = 5
    MAX_GLOBAL = 128
    MAX_COMPUTATIONS_PER_SESSION = 1
    MAX_COMPUTATIONS_GLOBAL = 2

    def __init__(self, context_store, now_provider=None):
        self._store = context_store
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._records: dict[str, OwnedSearch] = {}
        self._pending: dict[str, tuple[SearchOwner, bool, str | None]] = {}
        self._lock = RLock()

    def _cleanup(self):
        for search_id, record in list(self._records.items()):
            if self._now() >= record.expires_at:
                self._store.expire_search_context(search_id)
                del self._records[search_id]

    @contextmanager
    def computation(self, owner: SearchOwner, reserve_context: bool):
        with self._lock:
            self._cleanup()
            same = lambda other: other.session_binding == owner.session_binding
            if sum(same(p[0]) for p in self._pending.values()) >= self.MAX_COMPUTATIONS_PER_SESSION:
                raise SearchAdmissionError()
            if len(self._pending) >= self.MAX_COMPUTATIONS_GLOBAL:
                raise SearchAdmissionError(True)
            if reserve_context:
                own_count = sum(same(r.owner) for r in self._records.values()) + sum(same(p[0]) and p[1] for p in self._pending.values())
                if own_count >= self.MAX_PER_SESSION:
                    raise SearchAdmissionError()
                if len(self._records) + sum(p[1] for p in self._pending.values()) >= self.MAX_GLOBAL:
                    raise SearchAdmissionError(True)
            ticket = uuid4().hex
            self._pending[ticket] = (owner, reserve_context, None)
        try:
            yield ticket
        except BaseException:
            with self._lock:
                published = self._pending[ticket][2]
                if published:
                    self._records.pop(published, None)
                    self._store.expire_search_context(published)
            raise
        finally:
            with self._lock:
                self._pending.pop(ticket, None)
                self._cleanup()

    def publish(self, ticket: str, search_id: str):
        with self._lock:
            owner, reserved, published = self._pending[ticket]
            if not reserved or published is not None or search_id in self._records:
                raise ValueError("INVALID_SEARCH_PUBLICATION")
            context = self._store.get_search_context(search_id)
            if context.business_id != owner.business_id:
                raise ValueError("INVALID_SEARCH_PUBLICATION")
            self._records[search_id] = OwnedSearch(owner, context.expires_at)
            self._pending[ticket] = (owner, False, search_id)
            return context.expires_at

    def authorize(self, search_id: str, owner: SearchOwner):
        with self._lock:
            self._cleanup()
            record = self._records.get(search_id)
            if record is None or record.owner != owner:
                raise AlternativeSelectionError("ALTERNATIVE_SEARCH_EXPIRED")
            context = self._store.get_search_context(search_id)
            if context.business_id != owner.business_id:
                raise AlternativeSelectionError("ALTERNATIVE_SEARCH_EXPIRED")

    def consumed(self, search_id: str, owner: SearchOwner):
        with self._lock:
            record = self._records.get(search_id)
            if record and record.owner == owner:
                del self._records[search_id]
