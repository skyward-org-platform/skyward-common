from tests.conftest_pg import requires_pg
from skyward.data.meta import MetaClient


def test_format_id_zero_pads():
    assert MetaClient.format_id(1, 5) == "1"
    assert MetaClient.format_id(1, 47) == "01"
    assert MetaClient.format_id(1, 150) == "001"


@requires_pg
def test_get_max_id_empty(pg_client):
    meta = MetaClient(pg_client)
    assert meta.get_max_id("clients", "client_id") == 0


@requires_pg
def test_get_next_id_after_insert(pg_client):
    meta = MetaClient(pg_client)
    assert meta.get_next_id("clients", "client_id") == 1  # empty table
    pg_client.execute("insert into meta.clients (client_name) values ('A')")
    # get_next_id always re-queries MAX, so it reflects the new row
    assert meta.get_next_id("clients", "client_id") == 2
