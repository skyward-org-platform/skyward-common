import pandas as pd


def test_get_next_id_empty_table(hub, fake_bq):
    """First ID for an empty table should be 1."""
    fake_bq.client.set_next_result(pd.DataFrame({"max_id": [None]}))
    result = hub.get_next_id("clients", "client_id")
    assert result == 1


def test_get_next_id_with_existing(hub, fake_bq):
    """Next ID after 3 should be 4."""
    fake_bq.client.set_next_result(pd.DataFrame({"max_id": [3]}))
    result = hub.get_next_id("clients", "client_id")
    assert result == 4


def test_get_next_id_custom_dataset(hub, fake_bq):
    """get_next_id supports a custom dataset parameter."""
    fake_bq.client.set_next_result(pd.DataFrame({"max_id": [10]}))
    result = hub.get_next_id("some_table", "id_col", dataset="CustomDataset")
    assert result == 11
    sql = fake_bq.client.queries[-1]["sql"]
    assert "CustomDataset.some_table" in sql


def test_get_max_id_caches(hub, fake_bq):
    """get_max_id should cache the result and not re-query."""
    fake_bq.client.queue_result(pd.DataFrame({"max_id": [42]}))
    result1 = hub.get_max_id("clients", "client_id")
    assert result1 == 42
    # Second call should use cache (no new query)
    result2 = hub.get_max_id("clients", "client_id")
    assert result2 == 42
    assert len(fake_bq.client.queries) == 1


def test_get_max_id_empty_table(hub, fake_bq):
    """get_max_id on empty table returns 0."""
    fake_bq.client.queue_result(pd.DataFrame({"max_id": [None]}))
    result = hub.get_max_id("clients", "client_id")
    assert result == 0


def test_format_id_single_digit_max():
    from skyward.data.meta import MetaClient
    assert MetaClient.format_id(1, 5) == "1"


def test_format_id_two_digit_max():
    from skyward.data.meta import MetaClient
    assert MetaClient.format_id(1, 47) == "01"


def test_format_id_three_digit_max():
    from skyward.data.meta import MetaClient
    assert MetaClient.format_id(1, 150) == "001"


def test_format_id_four_digit_max():
    from skyward.data.meta import MetaClient
    assert MetaClient.format_id(1, 1000) == "0001"


def test_format_id_zero_max():
    from skyward.data.meta import MetaClient
    assert MetaClient.format_id(1, 0) == "1"
