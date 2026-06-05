"""Live smoke test for the Supabase-backed MetaClient/DataHub against skyward-ops.

SAFE BY DESIGN: runs every check inside a single transaction with
``autocommit = False`` and ROLLS BACK at the end, so no test data is ever
committed and the real meta.* data is untouched. Proves cleanup by asserting the
real row counts are unchanged afterward.

    export $(grep '^SUPABASE_DB_URL=' secrets/skyward-ops-supabase.env | xargs)
    python scripts/live_smoke_meta.py
"""
from __future__ import annotations

import os
import sys

from skyward.config import load_config
from skyward.data.bigquery import BigQueryClient
from skyward.data.supabase import SupabaseClient
from skyward.data.hub import DataHub

TS_COLS = [
    ("clients", "created_at"), ("domains", "created_at"),
    ("projects", "created_at"), ("client_datasets", "created_at"),
    ("dataset_catalog", "updated_at"),
    ("table_catalog", "status_changed_at"), ("table_catalog", "last_indexed_at"),
]
COUNT_TABLES = ["clients", "domains", "client_domains", "projects",
                "project_domains", "client_datasets", "dataset_catalog", "table_catalog"]


def main():
    cfg = load_config()
    if not cfg.supabase_db_url:
        sys.exit("SUPABASE_DB_URL not set")
    bq = BigQueryClient(project_id=cfg.datahub_project_id, credentials_info=cfg.datahub_credentials)
    sb = SupabaseClient(cfg.supabase_db_url)
    sb._conn.autocommit = False  # one transaction, rolled back at the end

    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

    counts = lambda: {t: int(sb.query(f"select count(*) c from meta.{t}").iloc[0]["c"]) for t in COUNT_TABLES}
    before = counts()

    # Data-integrity: no out-of-range timestamp sentinels anywhere.
    print("=== data integrity ===")
    for t, c in TS_COLS:
        bad = int(sb.query(f"select count(*) c from meta.{t} where {c} > timestamptz '9999-12-31'").iloc[0]["c"])
        check(f"{t}.{c} no corrupt timestamps", bad == 0)

    try:
        hub = DataHub(sb, bq)
        print("=== clients ===")
        cid = hub.add_client("ZZ_LiveTest", abbreviation="ZZT", notes="live")
        check("add_client/get_client", hub.get_client(cid)["client_name"] == "ZZ_LiveTest")
        check("list_clients search", "ZZ_LiveTest" in list(hub.list_clients(search="zz_livetest")["client_name"]))
        check("list_clients counts cols", {"domain_count", "competitor_count", "project_count"} <= set(hub.list_clients(include_counts=True).columns))
        hub.update_client(cid, notes="updated")
        check("update_client", hub.get_client(cid)["notes"] == "updated")

        print("=== domains ===")
        res = hub.add_domains(["zzlivetest.com", "https://www.zzlivetest.com/path"], client_id=cid)
        did = res[0]["domain_id"]
        check("get_domain", hub.get_domain("https://zzlivetest.com")["domain"] == "zzlivetest.com")
        check("search_domains", not hub.search_domains("zzlivetest").empty)
        check("get_client_domains", "zzlivetest.com" in list(hub.get_client_domains(cid)["domain"]))
        hub.update_domain(did, domain_name="ZZ Live")
        check("update_domain", hub.get_domain("zzlivetest.com")["domain_name"] == "ZZ Live")
        hub.update_client_domains_priority_batch(cid, [{"domain_id": did, "priority": "high"}])
        check("priority_batch HIGH", hub.get_client_domains(cid).iloc[0]["priority"] == "HIGH")

        print("=== projects ===")
        pid = hub.add_project(cid, "live_test", project_name="ZZ proj")
        check("add_project/list_projects", hub.list_projects(client_id=cid).iloc[0]["status"] == "active")
        check("add_project_domains", hub.add_project_domains(pid, [did]) == 1)
        check("list_project_domains", list(hub.list_project_domains(pid)["domain"]) == ["zzlivetest.com"])
        hub.complete_project(pid)
        check("complete_project", hub.list_projects(client_id=cid).iloc[0]["status"] == "complete")
        hub.remove_project_domains(pid, [did])
        check("remove_project_domains", hub.list_project_domains(pid).empty)

        print("=== datasets ===")
        check("add_client_dataset", hub.add_client_dataset(cid, "zz_live_ds", "ga4", hostname="zzlivetest.com", domain_id=did)["status"] == "added")
        check("get_dataset_catalog", "zz_live_ds" in list(hub.get_dataset_catalog(dataset_type="ga4")["dataset"]))
        check("check_dataset_assignment", hub.check_dataset_assignment("zz_live_ds")["client_id"] == cid)
        check("get_client_datasets join", hub.get_client_datasets(client_id=cid).iloc[0]["hostname"] == "zzlivetest.com")
        hub.update_client_dataset(cid, "zz_live_ds", is_active=False)
        check("update_client_dataset", hub.get_client_datasets(client_id=cid, active_only=False).iloc[0]["is_active"] is False)
        hub.delete_client_dataset(cid, "zz_live_ds")
        check("delete_client_dataset", hub.get_client_datasets(client_id=cid, active_only=False).empty)
        check("scan_and_match_datasets", isinstance(hub.scan_and_match_datasets(), dict))

        print("=== additional create/modify coverage ===")
        # add_domain (singular wrapper) + update_domains_batch
        d2 = hub.add_domain("zzlive2.com", client_id=cid, is_competitor=True, priority="LOW")
        check("add_domain singular", isinstance(d2, int) and "zzlive2.com" in list(hub.get_client_domains(cid)["domain"]))
        check("add_domain is_competitor link", hub.get_client_domains(cid, is_competitor=True).iloc[0]["domain"] == "zzlive2.com")
        hub.update_domains_batch([{"domain_id": d2, "domain_name": "ZZ Two", "is_active": False, "notes": "b"}])
        check("update_domains_batch", hub.get_domain("zzlive2.com")["domain_name"] == "ZZ Two")

        # deactivate_project
        p2 = hub.add_project(cid, "live_test2", project_name="ZZ proj2")
        hub.deactivate_project(p2)
        check("deactivate_project", hub.list_projects(client_id=cid, status="deactivated").iloc[0]["project_id"] == p2)

        # deactivate_client_dataset (soft delete)
        hub.add_client_dataset(cid, "zz_ds2", "gsc", hostname="zzlive2.com")
        hub.deactivate_client_dataset("zz_ds2")
        check("deactivate_client_dataset", bool((hub.get_client_datasets(client_id=cid, active_only=False)["dataset_id"] == "zz_ds2").any())
              and not bool((hub.get_client_datasets(client_id=cid, active_only=True)["dataset_id"] == "zz_ds2").any()))

        # approve_scanned_datasets (bulk approve path)
        n_appr = hub.approve_scanned_datasets([
            {"dataset_id": "zz_ds3", "dataset_type": "ga4", "hostname": "zzlive2.com", "client_id": cid, "domain_id": d2}
        ])
        check("approve_scanned_datasets", n_appr == 1 and "zz_ds3" in list(hub.get_dataset_catalog(dataset_type="ga4")["dataset"]))

        # cached getters
        check("get_ga4_datasets_cached cols", set(["client_id", "dataset_id", "hostname"]) <= set(hub.get_ga4_datasets_cached(client_id=cid).columns))
        check("get_gsc_datasets_cached cols", set(["client_id", "dataset_id", "hostname"]) <= set(hub.get_gsc_datasets_cached(client_id=cid).columns))

        print("=== DataHub catalog + hybrid ===")
        lt = hub.list_tables(dataset="DataForSEO")
        check("list_tables reads catalog", "table_name" in lt.columns and len(lt) > 0)
        rc = hub.reindex_catalog("Logs")
        check("reindex_catalog summary", {"dataset", "new_tables", "total_active"} <= set(rc.keys()))
        gcd = hub.get_client_data(str(cid), "dataforseo_labs-google-ranked_keywords", use_domain_lookup=True, limit=5)
        check("get_client_data domain-lookup (hybrid)", gcd is not None)

        print("=== teardown ops ===")
        hub.remove_client_domain(cid, did)
        check("remove_client_domain", did not in list(hub.get_client_domains(cid)["domain_id"]))
        hub.deactivate_client(cid, cascade=True)
        check("deactivate_client cascade", hub.get_client(cid)["is_active"] is False)
    finally:
        sb._conn.rollback()  # cleanup: undo ALL test writes

    after = counts()
    print("\n=== rollback cleanup check ===")
    check("real counts unchanged after rollback", before == after)
    sb.close()

    passed = sum(1 for _, ok in results if ok)
    print(f"\n=== LIVE SUITE: {passed}/{len(results)} passed ===")
    for n, ok in results:
        if not ok:
            print(f"   FAILED: {n}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
