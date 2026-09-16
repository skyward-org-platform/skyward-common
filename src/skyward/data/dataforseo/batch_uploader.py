"""Periodic data saves for DataForSEO runs (v1.6.1).

A run's rows collect in an open "window" that already has its upload_id. When the window
reaches the threshold it is saved under that upload_id and a new window opens. Windows are
private to one run; nothing is shared between runs and nothing is linked by time.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

import pandas as pd

from skyward.functions import generate_upload_id

logger = logging.getLogger(__name__)

SINGLE_SAVE_BELOW_ROWS = 10_000
MID_TIER_MAX_ROWS = 100_000
MID_TIER_BATCH_ROWS = 10_000
LARGE_TIER_BATCH_ROWS = 50_000


def choose_upload_batch_rows(planned_max_rows: int, override: int | None = None) -> int | None:
    if override is not None:
        if override < 1:
            raise ValueError("upload_batch_rows must be >= 1")
        return int(override)
    if planned_max_rows < SINGLE_SAVE_BELOW_ROWS:
        return None
    if planned_max_rows <= MID_TIER_MAX_ROWS:
        return MID_TIER_BATCH_ROWS
    return LARGE_TIER_BATCH_ROWS


class BatchUploader:
    def __init__(
        self,
        *,
        threshold: int | None,
        write: Callable[[pd.DataFrame, str], None],
        before_save: Callable[[str], None] | None = None,
        after_save: Callable[[str], None] | None = None,
    ) -> None:
        self._threshold = threshold
        self._write = write
        self._before_save = before_save
        self._after_save = after_save
        self._lock = threading.Lock()
        self._frames: list[pd.DataFrame] = []
        self._rows = 0
        self._upload_id = generate_upload_id()
        self._closed = False
        self.saved_upload_ids: list[str] = []

    @property
    def current_upload_id(self) -> str:
        with self._lock:
            return self._upload_id

    def add(self, df: pd.DataFrame | None, on_joined: Callable[[str], None] | None = None) -> str:
        ready: tuple[list[pd.DataFrame], str] | None = None
        refused = False
        refused_rows = 0
        with self._lock:
            upload_id = self._upload_id
            if self._closed:
                # A late writer. RunContext.add_rows must release its own lock before
                # calling us -- the lock order forbids holding it across this call -- so a
                # writer that passed its "am I too late" check can still arrive here after
                # close() has run. Before this guard those rows were appended to a window
                # that close() had just minted and that nothing would ever flush; with
                # threshold None, which is every run under SINGLE_SAVE_BELOW_ROWS rows,
                # it could never flush at all. The rows were already in the caller's
                # frames, so the returned DataFrame looked complete while nothing reached
                # BigQuery. Refuse loudly instead.
                refused = True
                refused_rows = 0 if df is None else len(df)
            else:
                if on_joined is not None:
                    on_joined(upload_id)
                if df is not None and not df.empty:
                    self._frames.append(df)
                    self._rows += len(df)
                if self._threshold is not None and self._rows >= self._threshold:
                    ready = (self._frames, upload_id)
                    self._frames, self._rows = [], 0
                    self._upload_id = generate_upload_id()
        if refused:
            if on_joined is not None:
                # Deliberately OUTSIDE the lock, unlike the open path. This callback
                # writes cost rows, and a closed cost writer flushes them inline -- a
                # BigQuery insert with retries and sleeps, up to tens of seconds. Holding
                # the uploader lock across that would stall every other add() in exactly
                # the situation this path exists for. Passing None rather than an id is
                # the honest answer: the cost is real, the window is not, so those cost
                # rows must not point at an upload_id that will never land.
                on_joined(None)
            if refused_rows:
                # Never raise: RunContext.add_rows is called from the SERP legacy worker,
                # whose `except Exception` would turn a raise into a bogus per-keyword
                # failure row.
                logger.error(
                    "Uploader already closed: refused %d row(s) for window %s; they were "
                    "NOT saved to BigQuery.", refused_rows, upload_id)
            return upload_id
        if ready is not None:
            self._save(*ready)
        return upload_id

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            frames, upload_id = self._frames, self._upload_id
            self._frames, self._rows = [], 0
        if frames:
            self._save(frames, upload_id)

    def _save(self, frames: list[pd.DataFrame], upload_id: str) -> None:
        if self._before_save is not None:
            self._before_save(upload_id)
        self._write(pd.concat(frames, ignore_index=True), upload_id)
        with self._lock:
            self.saved_upload_ids.append(upload_id)
        if self._after_save is not None:
            self._after_save(upload_id)
