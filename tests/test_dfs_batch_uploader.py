import logging
import threading

import pandas as pd
import pytest

from skyward.data.dataforseo.batch_uploader import BatchUploader, choose_upload_batch_rows


def test_choose_upload_batch_rows_tiers_and_override():
    assert choose_upload_batch_rows(9_999) is None
    assert choose_upload_batch_rows(10_000) == 10_000
    assert choose_upload_batch_rows(100_000) == 10_000
    assert choose_upload_batch_rows(100_001) == 50_000
    assert choose_upload_batch_rows(10_000_000) == 50_000
    assert choose_upload_batch_rows(10_000_000, override=5) == 5
    with pytest.raises(ValueError):
        choose_upload_batch_rows(10, override=0)


def _df(n, marker="x"):
    return pd.DataFrame({"marker": [marker] * n})


def test_single_save_at_close_when_no_threshold():
    saved = []
    up = BatchUploader(threshold=None, write=lambda df, uid: saved.append((len(df), uid)))
    uid1 = up.add(_df(3))
    uid2 = up.add(_df(4))
    assert saved == [] and uid1 == uid2
    up.close()
    assert saved == [(7, uid1)]
    assert up.saved_upload_ids == [uid1]


def test_threshold_rolls_windows():
    saved = []
    up = BatchUploader(threshold=5, write=lambda df, uid: saved.append((len(df), uid)))
    first = up.add(_df(3))
    second = up.add(_df(3))       # 6 rows -> save window 1
    assert first == second
    assert saved == [(6, first)]
    third = up.add(_df(1))
    assert third != first
    up.close()
    assert saved[-1] == (1, third)


def test_empty_add_returns_current_window_and_close_skips_empty():
    saved = []
    up = BatchUploader(threshold=5, write=lambda df, uid: saved.append(uid))
    uid = up.add(None)
    assert uid == up.current_upload_id
    up.close()
    assert saved == []


def test_on_joined_gets_same_id_as_saved_window_and_order_is_before_write_after():
    events = []
    up = BatchUploader(
        threshold=2,
        write=lambda df, uid: events.append(("write", uid)),
        before_save=lambda uid: events.append(("before", uid)),
        after_save=lambda uid: events.append(("after", uid)),
    )
    joined = []
    up.add(_df(2), on_joined=joined.append)
    assert events == [("before", joined[0]), ("write", joined[0]), ("after", joined[0])]


def test_concurrent_adds_never_mix_windows():
    saved: dict[str, list[str]] = {}
    lock = threading.Lock()

    def write(df, uid):
        with lock:
            saved[uid] = list(df["marker"])

    up = BatchUploader(threshold=7, write=write)
    joined: dict[str, str] = {}

    def worker(i):
        for j in range(50):
            marker = f"{i}-{j}"
            uid = up.add(_df(1, marker), on_joined=lambda u, m=marker: joined.__setitem__(m, u))
            assert joined[marker] == uid

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    up.close()

    for uid, markers in saved.items():
        for m in markers:
            assert joined[m] == uid
    assert sum(len(m) for m in saved.values()) == 400


def test_add_after_close_is_refused_and_logged_not_silently_lost(caplog):
    """RunContext.add_rows releases its own lock before calling add() (the lock order
    forbids holding it across the call), so a writer can arrive here after close(). Those
    rows used to be appended to a window close() had just minted, which nothing would ever
    flush -- silently lost while still appearing in the caller's DataFrame.
    """
    saved = []
    up = BatchUploader(threshold=None, write=lambda df, uid: saved.append((len(df), uid)))
    up.add(_df(3))
    up.close()
    assert saved == [(3, up.current_upload_id)]

    with caplog.at_level(logging.ERROR):
        up.add(_df(5))
    assert "refused 5 row(s)" in caplog.text

    # Nothing further was written, and no phantom window is holding the rows.
    assert saved == [(3, up.current_upload_id)]
    up.close()
    assert saved == [(3, up.current_upload_id)]


def test_add_after_close_with_no_threshold_cannot_be_rescued_by_a_later_flush():
    """threshold=None is every run under 10k rows, so a post-close window has no flush
    condition at all: the loss was guaranteed there, not merely likely.
    """
    saved = []
    up = BatchUploader(threshold=None, write=lambda df, uid: saved.append(len(df)))
    up.close()
    for _ in range(50):
        up.add(_df(100))
    assert saved == []          # refused outright, not buffered into an unflushable window

    # And not merely parked somewhere a later flush could rescue: without the closed guard
    # those 5,000 rows sit in a window minted by close(), and a second close() would write
    # them under an upload_id the run already finished reporting on.
    up.close()
    assert saved == []


def test_on_joined_after_close_gets_none_not_a_phantom_upload_id():
    """Cost rows are tagged through on_joined. After close the data will never land, so
    tagging them with a live-looking upload_id would point cost rows at a window that has
    no data behind it. A null upload_id is the honest answer.
    """
    up = BatchUploader(threshold=None, write=lambda df, uid: None)
    up.close()
    joined = []
    up.add(_df(2), on_joined=joined.append)
    assert joined == [None]


def test_close_twice_does_not_save_twice():
    saved = []
    up = BatchUploader(threshold=None, write=lambda df, uid: saved.append(uid))
    up.add(_df(1))
    up.close()
    up.close()
    assert len(saved) == 1
