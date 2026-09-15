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
