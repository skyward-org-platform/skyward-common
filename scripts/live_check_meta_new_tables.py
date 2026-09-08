"""Exercise every meta.site / meta.data_access method against live data.

Reads run over EVERY row, not a spot check: the first version of this
work verified two domains and called it "verified live", which oversold
it. Writes run inside a transaction that is rolled back, so nothing
persists.

    uv run python scripts/live_check_meta_new_tables.py
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, "src")
from skyward.data.meta import MetaClient          # noqa: E402
from skyward.data.supabase import SupabaseClient  # noqa: E402

EXPECTED = "ycvkkukiulygmmkcpsnt"


def url() -> str:
    env = pathlib.Path("/mnt/d/Skyward/skyward-seo-pipeline/.env").read_text()
    u = re.search(r"^SUPABASE_DB_URL=(.+)$", env, re.M).group(1).strip().strip('"')
    if EXPECTED not in u:
        raise RuntimeError("refusing: not skyward-ops")
    return u


def main() -> int:
    import psycopg

    failures = []

    def check(label, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" +
              (f"   {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    meta = MetaClient(SupabaseClient(url()))

    # ---- reads, over every row -------------------------------------
    print("READS (every row, live)")
    sites = meta.list_sites()
    check("list_sites returns every site", len(sites) == 50, f"{len(sites)}")

    by_status = {}
    for status in ("client", "prospect", "canceled", "prototype"):
        by_status[status] = len(meta.list_sites(engagement_status=status))
    check("list_sites filters sum to the whole",
          sum(by_status.values()) == len(sites), str(by_status))

    resolved = 0
    for domain_id in sites["domain_id"]:
        row = meta.get_site(int(domain_id))
        if row and row["domain_id"] == domain_id and row.get("domain"):
            resolved += 1
    check("get_site resolves every site", resolved == len(sites),
          f"{resolved}/{len(sites)}")

    check("get_site is None for an unknown domain",
          meta.get_site(999999) is None)

    total_access, multi = 0, []
    for domain_id in sites["domain_id"]:
        df = meta.get_data_access(domain_id=int(domain_id), active_only=False)
        total_access += len(df)
        for tool, group in df.groupby("tool"):
            if len(group) > 1:
                multi.append((int(domain_id), tool, len(group)))
    check("get_data_access covers every access row", total_access == 98,
          f"{total_access}")
    check("more than one account per tool is returned, not collapsed",
          bool(multi), f"{len(multi)} site/tool pairs, e.g. {multi[:2]}")

    ads = meta.get_data_access(domain_id=1, tool="google_ads")
    check("buscharter still shows BOTH Ads accounts", len(ads) == 2,
          ", ".join(sorted(ads["account_identifier"].fillna("-"))))

    using = meta.get_sites_using_dataset("gads_backfill_3945915239")
    check("get_sites_using_dataset finds every site sharing a dataset",
          len(using) == 5, f"{len(using)} sites")

    # ---- writes, rolled back ---------------------------------------
    print("\nWRITES (real statements, rolled back)")
    conn = psycopg.connect(url(), autocommit=False)
    tx = MetaClient(_Tx(conn))
    cur = conn.cursor()

    def count(sql, params=None):
        cur.execute(sql, params or {})
        return cur.fetchone()[0]

    before_sites = count("select count(*) from meta.site")
    tx.upsert_site(domain_id=22, client_id=1, engagement_status="client",
                   source="live-check", industry="charter bus")
    check("upsert_site updates an existing site rather than duplicating",
          count("select count(*) from meta.site") == before_sites)
    check("upsert_site wrote the field",
          count("select count(*) from meta.site where domain_id=22 "
                "and industry='charter bus'") == 1)

    tx.update_site(22, title_brand="BusBank")
    check("update_site leaves other columns alone",
          count("select count(*) from meta.site where domain_id=22 "
                "and industry='charter bus' and title_brand='BusBank'") == 1)

    tx.update_site_priority_batch(
        [{"domain_id": 22, "priority": "VERY HIGH"},
         {"domain_id": 23, "priority": "LOW"}])
    check("update_site_priority_batch writes both in one statement",
          count("select count(*) from meta.site where "
                "(domain_id=22 and priority='VERY HIGH') or "
                "(domain_id=23 and priority='LOW')") == 2)

    before_access = count("select count(*) from meta.data_access")
    tx.add_data_access(domain_id=22, tool="ahrefs", source="live-check",
                       account_identifier="proj-1")
    check("add_data_access inserts a new account",
          count("select count(*) from meta.data_access") == before_access + 1)
    tx.add_data_access(domain_id=22, tool="ahrefs", source="live-check",
                       account_identifier="proj-1")
    check("add_data_access is idempotent on the same account",
          count("select count(*) from meta.data_access") == before_access + 1)
    tx.add_data_access(domain_id=22, tool="ahrefs", source="live-check",
                       account_identifier="proj-2")
    check("a SECOND account for the same tool is allowed",
          count("select count(*) from meta.data_access") == before_access + 2)

    tx.update_data_access(domain_id=22, tool="ahrefs",
                          account_identifier="proj-1",
                          access_status="revoked")
    check("update_data_access hits ONE row, not both accounts",
          count("select count(*) from meta.data_access where domain_id=22 "
                "and tool='ahrefs' and access_status='revoked'") == 1)

    tx.deactivate_data_access(domain_id=22, tool="ahrefs",
                              account_identifier="proj-2")
    check("deactivate_data_access flags rather than deletes",
          count("select count(*) from meta.data_access where domain_id=22 "
                "and tool='ahrefs' and is_active=false") == 1
          and count("select count(*) from meta.data_access") ==
          before_access + 2)

    tx.delete_data_access(domain_id=22, tool="ahrefs",
                          account_identifier="proj-1")
    tx.delete_data_access(domain_id=22, tool="ahrefs",
                          account_identifier="proj-2")
    check("delete_data_access removes exactly what it named",
          count("select count(*) from meta.data_access") == before_access)

    # a tool with no account identifier at all
    tx.add_data_access(domain_id=22, tool="screaming_frog",
                       source="live-check")
    tx.add_data_access(domain_id=22, tool="screaming_frog",
                       source="live-check")
    check("a NULL account identifier still deduplicates",
          count("select count(*) from meta.data_access where domain_id=22 "
                "and tool='screaming_frog'") == 1)

    conn.rollback()
    conn.close()

    after = MetaClient(SupabaseClient(url()))
    check("\n  nothing persisted: site count unchanged",
          len(after.list_sites()) == 50)

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


class _Tx:
    """Adapts a live psycopg connection to the sb interface, so the same
    MetaClient code runs inside a transaction we can roll back."""

    def __init__(self, conn):
        self.conn = conn

    def query(self, sql, params=None):
        import pandas as pd
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            cols = [d.name for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)

    def execute(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            return []


if __name__ == "__main__":
    sys.exit(main())
