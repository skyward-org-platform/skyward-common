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
