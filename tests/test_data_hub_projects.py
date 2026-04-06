"""Tests for Project management in DataHub."""
import pandas as pd


def test_add_project_auto_generates_id(hub, fake_bq):
    """add_project should auto-generate project_id and default status to 'active'."""
    fake_bq.client.set_next_result(pd.DataFrame({"max_id": [None]}))
    result = hub.add_project(client_id=1, project_type="seo_pipeline", project_name="SEO Audit")
    assert result == 1
    insert_sql = fake_bq.client.queries[-1]["sql"]
    assert "INSERT INTO" in insert_sql
    assert "Meta.projects" in insert_sql
    assert "status" in insert_sql


def test_update_project_status(hub, fake_bq):
    """update_project should update the status field."""
    hub.update_project(project_id=1, status="complete")
    sql = fake_bq.client.queries[-1]["sql"]
    assert "UPDATE" in sql
    assert "Meta.projects" in sql
    assert "status" in sql


def test_deactivate_project(hub, fake_bq):
    """deactivate_project sets status to 'deactivated'."""
    hub.deactivate_project(project_id=1)
    sql = fake_bq.client.queries[-1]["sql"]
    assert "UPDATE" in sql
    assert "status" in sql


def test_complete_project(hub, fake_bq):
    """complete_project sets status to 'complete'."""
    hub.complete_project(project_id=1)
    sql = fake_bq.client.queries[-1]["sql"]
    assert "UPDATE" in sql
    assert "status" in sql


def test_add_project_domains(hub, fake_bq):
    """add_project_domains inserts rows into project_domains."""
    count = hub.add_project_domains(project_id=1, domain_ids=[10, 20], role="client")
    assert count == 2
    loaded = fake_bq.client.loaded_tables[-1]
    assert "project_domains" in loaded["table_ref"]
    df = loaded["df"]
    assert list(df["domain_id"]) == [10, 20]
    assert list(df["role"]) == ["client", "client"]


def test_remove_project_domains(hub, fake_bq):
    """remove_project_domains deletes rows from project_domains."""
    hub.remove_project_domains(project_id=1, domain_ids=[10, 20])
    sql = fake_bq.client.queries[-1]["sql"]
    assert "DELETE" in sql
    assert "project_domains" in sql
    assert "@project_id" in sql
    assert "@domain_ids" in sql


def test_list_projects_filters_by_status(hub, fake_bq):
    """list_projects with status filter includes WHERE status clause."""
    fake_bq.client.set_next_result(pd.DataFrame({
        "project_id": [1], "client_id": [1], "project_type": ["seo_pipeline"],
        "project_name": ["SEO"], "notes": [None], "status": ["active"], "created_at": [pd.Timestamp.now()],
    }))
    hub.list_projects(status="active")
    sql = fake_bq.client.queries[-1]["sql"]
    assert "status" in sql
    assert "@status" in sql
