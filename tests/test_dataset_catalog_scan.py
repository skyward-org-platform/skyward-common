"""`scan_datasets` keeps meta.dataset_catalog in step with BigQuery.

The catalogue is the foreign key behind `meta.data_access.dataset_id`, which
is where every later phase looks to find a client's GA4 or Search Console
data. Raised as pipeline.request
`dataset-catalogue-cannot-be-updated-from-the-lane` after insofast's three
real datasets could not be linked: the write is refused by the constraint
because the catalogue does not know them.

These use a recording fake rather than the `hub` fixture on purpose. That
fixture needs a live test database and SKIPS without one, so a test written
against it would have looked green while never running.
"""
from types import SimpleNamespace

import pandas as pd

from skyward.data.hub import DataHub
from skyward.data.meta import MetaClient


class FakeSb:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def query(self, sql, params=None):
        self.calls.append({"sql": " ".join(sql.split()), "params": params or {}})
        return self.results.pop(0) if self.results else pd.DataFrame()

    def execute(self, sql, params=None):
        self.calls.append({"sql": " ".join(sql.split()), "params": params or {}})
        return []

    @property
    def sql(self):
        return " | ".join(c["sql"] for c in self.calls)


def _hub(datasets):
    """A DataHub whose BigQuery holds exactly these dataset names."""
    bq = SimpleNamespace(
        project_id="data-hub-468216",
        # Only called when a GA4 dataset was matched; it resolves hostnames
        # from the data, which is the one thing a prefix cannot tell you.
        get_ga4_dataset_hostnames=lambda: {},
        client=SimpleNamespace(
            project="data-hub-468216",
            list_datasets=lambda: [SimpleNamespace(dataset_id=d) for d in datasets],
            query=lambda *a, **k: SimpleNamespace(result=lambda: iter([])),
        ),
    )
    sb = FakeSb()
    return DataHub(sb, bq), sb


# ── the Google Ads prefix ────────────────────────────────────────────

def test_a_google_ads_backfill_dataset_is_catalogued():
    """`gads_backfill` was ALREADY a dataset_type in the catalogue -- twelve
    rows carry it -- so Ads datasets had been catalogued before by some route
    other than the default prefixes. What was missing was the prefix, so a
    scan left every client's Ads backfill out, insofast's
    gads_backfill_4913425827 among them."""
    hub, sb = _hub(["gads_backfill_4913425827"])

    out = hub.scan_datasets()

    assert "gads_backfill" in out, (
        f"Ads dataset was not categorised; got {list(out)}")
    assert out["gads_backfill"][0]["dataset"] == "gads_backfill_4913425827"


def test_the_prefix_vocabulary_names_google_ads():
    assert "gads_backfill" in MetaClient.DEFAULT_DATASET_PREFIXES


def test_the_existing_prefixes_are_untouched():
    """Adding one type must not disturb the four that worked."""
    prefixes = MetaClient.DEFAULT_DATASET_PREFIXES
    assert prefixes["ga4"] == ["analytics_"]
    assert prefixes["gsc"] == ["jepto_gsc_", "searchconsole_"]
    assert prefixes["gmb"] == ["jepto_gmb_"]
    assert prefixes["facebook"] == ["jepto_facebook_"]


# ── pruning is a separate intention ──────────────────────────────────

def test_a_scan_does_not_delete_by_default():
    """DISCOVERY AND PRUNING ARE DIFFERENT INTENTIONS, and only one of them
    can break a foreign key. meta.data_access.dataset_id references
    dataset_catalog.dataset with no ON DELETE clause, so removing a row
    something points at RAISES -- which means a scan run to pick up a new
    dataset could fail part way on an unrelated stale row, having already
    upserted some of its work."""
    hub, sb = _hub(["analytics_123"])

    hub.scan_datasets()

    assert "delete from meta.dataset_catalog" not in sb.sql.lower(), (
        "a plain scan must not delete anything")


def test_prune_deletes_rows_whose_dataset_is_gone():
    hub, sb = _hub(["analytics_123"])

    hub.scan_datasets(prune=True)

    assert "delete from meta.dataset_catalog" in sb.sql.lower()


def test_prune_is_scoped_to_the_prefixes_it_scanned():
    """Without `full`, a prune may only remove rows it could have seen.
    Deleting outside the scanned prefixes would remove datasets this scan
    never looked for -- the twelve gads_backfill rows, before the prefix
    existed, were exactly that case."""
    hub, sb = _hub(["analytics_123"])

    hub.scan_datasets(prune=True, prefixes={"ga4": ["analytics_"]})

    deletes = [c for c in sb.calls
               if "delete from meta.dataset_catalog" in c["sql"].lower()]
    assert len(deletes) == 1
    # %% not %: the LIKE pattern is escaped because the statement carries
    # psycopg parameters alongside it.
    assert "like 'analytics_%%'" in deletes[0]["sql"].lower()
    assert "jepto_gsc_" not in deletes[0]["sql"].lower(), (
        "a prune must not reach prefixes this scan never looked for")


def test_full_prune_still_requires_prune():
    """`full` says how WIDE to look, not whether to delete. It used to imply
    deletion across the entire table."""
    hub, sb = _hub(["analytics_123"])

    hub.scan_datasets(full=True)

    assert "delete from meta.dataset_catalog" not in sb.sql.lower()


# ── our own datasets must not be catalogued as a client's ────────────


def test_our_own_dbt_layers_are_not_catalogued_as_client_ga4():
    """The bare `analytics_` prefix sweeps in our OWN modelled data.

    Found by the shape survey on 2026-10-02: analytics_staging,
    analytics_mart and analytics_intermediate are dbt layers holding stg_*,
    mart_* and int_* tables, and analytics_gcs_portfolio and
    analytics_tna_portfolio are our own rollups holding
    analytics_combined_data. None carries events_*, so none is a GA4
    export. All five sat in the catalogue typed `ga4`, where something
    could link one to a client as their analytics.
    """
    hub, sb = _hub([
        "analytics_387603466",      # a real GA4 export
        "analytics_staging",
        "analytics_mart",
        "analytics_intermediate",
        "analytics_gcs_portfolio",
        "analytics_tna_portfolio",
    ])

    out = hub.scan_datasets()

    catalogued = [d["dataset"] for d in out.get("ga4", [])]
    assert catalogued == ["analytics_387603466"], (
        f"only the real export may be catalogued, got {catalogued}")


def test_a_client_export_is_still_catalogued():
    """The exclusion must be exact names, not a loose pattern: a client
    whose property id happened to contain 'mart' must not be dropped."""
    hub, sb = _hub(["analytics_387603466"])

    out = hub.scan_datasets()

    assert [d["dataset"] for d in out["ga4"]] == ["analytics_387603466"]


# ── recording a surveyed shape ───────────────────────────────────────
#
# The shape survey lives in the seo-pipeline repo but the write does not
# get to live there: meta.* is modified through these helpers so the
# allowed-column list, validation and the updated_at stamp all still
# apply. A raw `update meta.dataset_catalog ... set shape` in the lane
# is caught by that repo's own guard test, which is how this helper came
# to be asked for.


def test_a_surveyed_shape_is_recorded():
    sb = FakeSb()
    meta = MetaClient(sb)

    meta.record_dataset_shapes([
        {"dataset": "analytics_387603466", "shape": "ga4_full",
         "shape_checked_at": "2026-10-05T12:00:00+00:00",
         "shape_detail": {"missing": [], "extra": ["pseudonymous_users_"]}},
    ])

    assert len(sb.calls) == 1, f"expected one statement, got {len(sb.calls)}"
    sql = sb.calls[0]["sql"].lower()
    assert "update meta.dataset_catalog" in sql
    assert "updated_at = now()" in sql
    params = sb.calls[0]["params"]
    assert params["dataset_0"] == "analytics_387603466"
    assert params["shape_0"] == "ga4_full"
    # JSON text, not a dict: the column is jsonb and psycopg will not
    # adapt a bare dict.
    assert isinstance(params["shape_detail_0"], str)
    assert '"pseudonymous_users_"' in params["shape_detail_0"]


def test_many_shapes_are_one_statement():
    """105 catalogued datasets must not become 105 UPDATEs."""
    sb = FakeSb()
    meta = MetaClient(sb)

    meta.record_dataset_shapes([
        {"dataset": f"ds_{i}", "shape": "base_only",
         "shape_checked_at": "2026-10-05T12:00:00+00:00",
         "shape_detail": {}}
        for i in range(40)
    ])

    assert len(sb.calls) == 1, (
        f"one batched statement expected, got {len(sb.calls)}")
    params = sb.calls[0]["params"]
    assert params["dataset_39"] == "ds_39"


def test_recording_nothing_touches_the_database():
    """An --apply run that matched no shapes must not emit a statement."""
    sb = FakeSb()
    meta = MetaClient(sb)

    meta.record_dataset_shapes([])

    assert sb.calls == []


def test_a_row_without_a_dataset_is_refused():
    """The dataset name is the key. A blank one would update every row."""
    sb = FakeSb()
    meta = MetaClient(sb)

    import pytest
    with pytest.raises(ValueError) as exc:
        meta.record_dataset_shapes([
            {"dataset": "", "shape": "ga4_full",
             "shape_checked_at": "2026-10-05T12:00:00+00:00",
             "shape_detail": {}},
        ])

    assert "dataset" in str(exc.value).lower()
    assert sb.calls == [], "nothing may be written when a row is refused"
