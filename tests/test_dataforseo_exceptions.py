import pytest

from skyward.data.dataforseo import IncompleteTaskError


def test_incomplete_task_error_stores_task_ids():
    err = IncompleteTaskError(
        "3 of 5 tasks did not complete within 7200 seconds",
        task_ids=["t1", "t2", "t3"],
    )
    assert err.task_ids == ["t1", "t2", "t3"]
    assert "3 of 5" in str(err)


def test_incomplete_task_error_empty_task_ids_list_ok():
    err = IncompleteTaskError("zero stragglers (sanity)", task_ids=[])
    assert err.task_ids == []


def test_incomplete_task_error_is_runtime_error_subclass():
    assert issubclass(IncompleteTaskError, RuntimeError)
