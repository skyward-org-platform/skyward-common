from tests.conftest_pg import requires_pg
from skyward.data.meta import MetaClient


@requires_pg
def test_add_list_project(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    pid = meta.add_project(cid, "seo_pipeline", project_name="Nat")
    assert isinstance(pid, int)
    df = meta.list_projects(client_id=cid, project_type="seo_pipeline")
    assert df.iloc[0]["project_name"] == "Nat"
    assert df.iloc[0]["status"] == "active"


@requires_pg
def test_project_domains_link(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    pid = meta.add_project(cid, "seo_pipeline")
    did = meta.add_domain("acme.com")
    assert meta.add_project_domains(pid, [did], role="client") == 1
    assert list(meta.list_project_domains(pid)["domain"]) == ["acme.com"]
    meta.remove_project_domains(pid, [did])
    assert meta.list_project_domains(pid).empty


@requires_pg
def test_update_complete_deactivate_project(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    pid = meta.add_project(cid, "wqa")
    meta.complete_project(pid)
    assert meta.list_projects(client_id=cid).iloc[0]["status"] == "complete"
    meta.deactivate_project(pid)
    assert meta.list_projects(client_id=cid).iloc[0]["status"] == "deactivated"
