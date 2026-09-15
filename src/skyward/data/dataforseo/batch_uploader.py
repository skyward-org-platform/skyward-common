"""Periodic data saves for DataForSEO runs (v1.6.1).

A run's rows collect in an open "window" that already has its upload_id. When the window
reaches the threshold it is saved under that upload_id and a new window opens. Windows are
private to one run; nothing is shared between runs and nothing is linked by time.
"""

from __future__ import annotations

import threading
from typing import Callable

import pandas as pd

from skyward.functions import generate_upload_id

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
        self.saved_upload_ids: list[str] = []

    @property
    def current_upload_id(self) -> str:
        with self._lock:
            return self._upload_id

    def add(self, df: pd.DataFrame | None, on_joined: Callable[[str], None] | None = None) -> str:
        ready: tuple[list[pd.DataFrame], str] | None = None
        with self._lock:
            upload_id = self._upload_id
            if on_joined is not None:
                on_joined(upload_id)
            if df is not None and not df.empty:
                self._frames.append(df)
                self._rows += len(df)
            if self._threshold is not None and self._rows >= self._threshold:
                ready = (self._frames, upload_id)
                self._frames, self._rows = [], 0
                self._upload_id = generate_upload_id()
        if ready is not None:
            self._save(*ready)
        return upload_id

    def close(self) -> None:
        with self._lock:
            frames, upload_id = self._frames, self._upload_id
            self._frames, self._rows = [], 0
            self._upload_id = generate_upload_id()
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
