from tests.conftest_pg import requires_pg
from skyward.data.meta import MetaClient


def test_format_id_zero_pads():
    assert MetaClient.format_id(1, 5) == "1"
    assert MetaClient.format_id(1, 47) == "01"
    assert MetaClient.format_id(1, 150) == "001"
    assert MetaClient.format_id(1, 1000) == "0001"
    assert MetaClient.format_id(1, 0) == "1"


@requires_pg
def test_get_max_id_empty(pg_client):
    meta = MetaClient(pg_client)
    assert meta.get_max_id("clients", "client_id") == 0


@requires_pg
def test_get_next_id_after_insert(pg_client):
    meta = MetaClient(pg_client)
    # IDENTITY sequences are not rolled back across tests, so don't assume the
    # new row's id — capture it and assert get_next_id == MAX + 1.
    rows = pg_client.execute(
        "insert into meta.clients (client_name) values ('A') returning client_id"
    )
    new_id = int(rows[0][0])
    assert meta.get_next_id("clients", "client_id") == new_id + 1
