from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import Callable, Protocol, Sequence
from uuid import uuid4


class AlternativeSelectionError(ValueError):
    """A deterministic rejection while resolving an alternative selection."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AlternativeSelectionOption:
    """The minimal trusted option data retained for one search."""

    option_id: str
    staff_id: int
    start_datetime: datetime


@dataclass(frozen=True)
class AlternativeSearchContext:
    """A temporary, server-side record of one Alternative Discovery search."""

    search_id: str
    business_id: int
    requested_service_ids: tuple[int, ...]
    created_at: datetime
    expires_at: datetime
    options: tuple[AlternativeSelectionOption, ...]


@dataclass(frozen=True)
class ResolvedAlternativeSelection:
    """The trusted minimal data needed for a future selected-option booking flow."""

    search_id: str
    business_id: int
    requested_service_ids: tuple[int, ...]
    option_id: str
    staff_id: int
    start_datetime: datetime


class AlternativeSelectionContextStore(Protocol):
    """Temporary storage contract for trusted Alternative Discovery selections."""

    def create_search_context(
        self,
        *,
        business_id: int,
        requested_service_ids: Sequence[int],
        options: Sequence[AlternativeSelectionOption],
    ) -> AlternativeSearchContext: ...

    def resolve_selection(
        self,
        search_id: str,
        option_id: str,
    ) -> ResolvedAlternativeSelection: ...

    def claim_selection(
        self,
        search_id: str,
        option_id: str,
    ) -> ResolvedAlternativeSelection: ...

    def release_selection_claim(self, search_id: str) -> None: ...

    def expire_search_context(self, search_id: str) -> None: ...


class InMemoryAlternativeSelectionContextStore:
    """MVP-only selection storage with no persistence across process restarts."""

    _TTL = timedelta(minutes=15)

    def __init__(self, now_provider: Callable[[], datetime] | None = None):
        self._now_provider = now_provider or datetime.now
        self._contexts: dict[str, AlternativeSearchContext] = {}
        self._claimed_search_ids: set[str] = set()
        self._lock = RLock()

    def create_search_context(
        self,
        *,
        business_id: int,
        requested_service_ids: Sequence[int],
        options: Sequence[AlternativeSelectionOption],
    ) -> AlternativeSearchContext:
        with self._lock:
            created_at = self._now_provider()
            search_id = self._new_search_id()
            context = AlternativeSearchContext(
                search_id=search_id,
                business_id=business_id,
                requested_service_ids=tuple(requested_service_ids),
                created_at=created_at,
                expires_at=created_at + self._TTL,
                options=tuple(
                    AlternativeSelectionOption(
                        option_id=option.option_id,
                        staff_id=option.staff_id,
                        start_datetime=option.start_datetime,
                    )
                    for option in options
                ),
            )
            self._contexts[search_id] = context
            return context

    def resolve_selection(
        self,
        search_id: str,
        option_id: str,
    ) -> ResolvedAlternativeSelection:
        with self._lock:
            context = self._get_valid_context(search_id)
            return self._resolve_context_option(context, option_id)

    def claim_selection(
        self,
        search_id: str,
        option_id: str,
    ) -> ResolvedAlternativeSelection:
        """Atomically resolve and claim one context for a booking attempt."""
        with self._lock:
            context = self._get_valid_context(search_id)
            selection = self._resolve_context_option(context, option_id)
            if search_id in self._claimed_search_ids:
                raise AlternativeSelectionError("ALTERNATIVE_SELECTION_IN_PROGRESS")
            self._claimed_search_ids.add(search_id)
            return selection

    def release_selection_claim(self, search_id: str) -> None:
        with self._lock:
            self._claimed_search_ids.discard(search_id)

    def expire_search_context(self, search_id: str) -> None:
        with self._lock:
            self._contexts.pop(search_id, None)
            self._claimed_search_ids.discard(search_id)

    def _get_valid_context(self, search_id: str) -> AlternativeSearchContext:
        context = self._contexts.get(search_id)
        if context is None or self._now_provider() >= context.expires_at:
            self._contexts.pop(search_id, None)
            self._claimed_search_ids.discard(search_id)
            raise AlternativeSelectionError("ALTERNATIVE_SEARCH_EXPIRED")
        return context

    @staticmethod
    def _resolve_context_option(
        context: AlternativeSearchContext,
        option_id: str,
    ) -> ResolvedAlternativeSelection:
        for option in context.options:
            if option.option_id == option_id:
                return ResolvedAlternativeSelection(
                    search_id=context.search_id,
                    business_id=context.business_id,
                    requested_service_ids=context.requested_service_ids,
                    option_id=option.option_id,
                    staff_id=option.staff_id,
                    start_datetime=option.start_datetime,
                )

        raise AlternativeSelectionError("ALTERNATIVE_OPTION_NOT_FOUND")

    def _new_search_id(self) -> str:
        search_id = uuid4().hex
        while search_id in self._contexts:
            search_id = uuid4().hex
        return search_id
