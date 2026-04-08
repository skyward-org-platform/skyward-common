"""Verify interactive methods have been removed from BigQueryClient."""
import inspect
from skyward.data.bigquery import BigQueryClient


def test_no_interactive_select_methods():
    """BigQueryClient should not have interactive input() methods."""
    methods = [name for name, _ in inspect.getmembers(BigQueryClient, predicate=inspect.isfunction)]
    assert "select_dataset" not in methods
    assert "select_table" not in methods
    assert "select_upload_id" not in methods
