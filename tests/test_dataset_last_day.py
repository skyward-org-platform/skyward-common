"""`record_dataset_last_days` stores the latest day each dataset holds.

Asked for in ClickUp 86bd62xvt: the operator dashboard reads Supabase only,
so without this it judged BusBank's Search Console from a stale copy and
called a healthy feed broken.

Same recording fake as test_dataset_catalog_scan, for the same reason: the
`hub` fixture skips without a live database.
"""
from datetime import date

import pytest

from skyward.data.meta import MetaClient
from tests.test_dataset_catalog_scan import FakeSb


CHECKED = "2026-11-24T03:30:00+00:00"


def test_last_days_are_one_update():
    sb = FakeSb()
    meta = MetaClient(sb)

    sent = meta.record_dataset_last_days([
        {"dataset": "jepto_gsc_busbank", "last_day": date(2026, 11, 20),
         "last_day_checked_at": CHECKED},
        {"dataset": "analytics_255778584", "last_day": date(2026, 11, 21),
         "last_day_checked_at": CHECKED},
    ])

    assert sent == 2
    assert len(sb.calls) == 1, f"expected one statement, got {len(sb.calls)}"
    sql = sb.calls[0]["sql"].lower()
    assert "update meta.dataset_catalog" in sql
    assert "last_day = v.last_day::date" in sql
    assert "updated_at = now()" in sql
    # Shape is a separate observation and must not be touched here.
    assert "shape" not in sql
    params = sb.calls[0]["params"]
    assert params["dataset_0"] == "jepto_gsc_busbank"
    assert params["last_day_0"] == date(2026, 11, 20)
    assert params["last_day_checked_at_1"] == CHECKED


def test_a_read_that_found_nothing_is_stored_as_null_day():
    """Looked and found nothing to date is a finding, not a skip."""
    sb = FakeSb()
    meta = MetaClient(sb)

    meta.record_dataset_last_days([
        {"dataset": "jepto_gsc_empty", "last_day": None,
         "last_day_checked_at": CHECKED},
    ])

    params = sb.calls[0]["params"]
    assert params["last_day_0"] is None
    assert params["last_day_checked_at_0"] == CHECKED


def test_a_row_without_a_check_time_is_refused():
    """A failed read must not overwrite a good date with a null."""
    sb = FakeSb()
    meta = MetaClient(sb)

    with pytest.raises(ValueError) as exc:
        meta.record_dataset_last_days([
            {"dataset": "jepto_gsc_busbank", "last_day": None},
        ])

    assert "last_day_checked_at" in str(exc.value)
    assert sb.calls == [], "nothing may be written when a row is refused"


def test_a_row_without_a_dataset_is_refused():
    sb = FakeSb()
    meta = MetaClient(sb)

    with pytest.raises(ValueError):
        meta.record_dataset_last_days([
            {"dataset": " ", "last_day": date(2026, 11, 20),
             "last_day_checked_at": CHECKED},
        ])

    assert sb.calls == []


def test_recording_nothing_touches_the_database():
    sb = FakeSb()
    meta = MetaClient(sb)

    assert meta.record_dataset_last_days([]) == 0
    assert sb.calls == []


def test_catalogue_read_returns_the_new_columns():
    sb = FakeSb()
    meta = MetaClient(sb)

    meta.get_dataset_catalog()

    sql = sb.calls[0]["sql"]
    assert "dc.last_day, dc.last_day_checked_at" in sql
