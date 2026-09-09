"""Exercise every meta.site / meta.data_access method against live data.

Reads run over EVERY row, not a spot check: the first version of this
work verified two domains and called it "verified live", which oversold
it. Writes run inside a transaction that is rolled back, so nothing
persists.

    uv run python scripts/live_check_meta_new_tables.py
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

sys.path.insert(0, "src")
from skyward.data.meta import MetaClient          # noqa: E402
from skyward.data.supabase import SupabaseClient  # noqa: E402

EXPECTED = "ycvkkukiulygmmkcpsnt"


# Files to fall back on when SUPABASE_DB_URL is not exported. Absolute
# paths to sibling checkouts are a last resort, not the primary source --
# this script should run from a bare environment with the variable set.
_URL_FALLBACKS = (
    "secrets/skyward-ops-supabase.env",
    "../skyward-common/secrets/skyward-ops-supabase.env",
    ".env",
    "/mnt/d/Skyward/skyward-seo-pipeline/.env",
)


def url() -> str:
    u = os.environ.get("SUPABASE_DB_URL", "").strip().strip('"')
    if not u:
        for candidate in _URL_FALLBACKS:
            path = pathlib.Path(candidate)
            if not path.is_file():
                continue
            found = re.search(r"^SUPABASE_DB_URL=(.+)$", path.read_text(), re.M)
            if found:
                u = found.group(1).strip().strip('"')
                break
    if not u:
        raise RuntimeError(
            "SUPABASE_DB_URL is not set and none of the fallbacks had it: "
            + ", ".join(_URL_FALLBACKS)
        )
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
    # Never assert a literal count here. The estate grows, and a check that
    # hard-codes today's number fails for the one reason we do not care
    # about. Compare the method against the table instead.
    site_count = int(meta.sb.query(
        "select count(*) as n from meta.site").iloc[0]["n"])
    check("list_sites returns every site",
          len(sites) == site_count, f"{len(sites)} of {site_count}")

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

    unknown_id = int(meta.sb.query(
        "select coalesce(max(domain_id), 0) + 1000 as n from meta.domains"
    ).iloc[0]["n"])
    check("get_site is None for an unknown domain",
          meta.get_site(unknown_id) is None, f"tried {unknown_id}")

    total_access, multi = 0, []
    for domain_id in sites["domain_id"]:
        df = meta.get_data_access(domain_id=int(domain_id), active_only=False)
        total_access += len(df)
        for tool, group in df.groupby("tool"):
            if len(group) > 1:
                multi.append((int(domain_id), tool, len(group)))
    access_count = int(meta.sb.query(
        "select count(*) as n from meta.data_access").iloc[0]["n"])
    check("get_data_access covers every access row",
          total_access == access_count, f"{total_access} of {access_count}")
    check("more than one account per tool is returned, not collapsed",
          bool(multi), f"{len(multi)} site/tool pairs, e.g. {multi[:2]}")

    # Which site holds the most accounts for one tool is a fact about live
    # data -- buscharter has two Ads accounts today and could have three
    # tomorrow. Ask the database which pair is busiest, then prove the read
    # returns all of them.
    busiest = meta.sb.query(
        """
        SELECT domain_id, tool, COUNT(*) AS n
        FROM meta.data_access
        GROUP BY domain_id, tool
        ORDER BY n DESC, domain_id
        LIMIT 1
        """
    )
    if busiest.empty:
        check("a site/tool pair exists to test multi-account reads", False)
    else:
        b_domain, b_tool = int(busiest.iloc[0]["domain_id"]), busiest.iloc[0]["tool"]
        b_n = int(busiest.iloc[0]["n"])
        got = meta.get_data_access(domain_id=b_domain, tool=b_tool, active_only=False)
        check("every account on the busiest site/tool pair is returned",
              len(got) == b_n, f"domain {b_domain} / {b_tool}: {len(got)} of {b_n}")

    shared = meta.sb.query(
        """
        SELECT dataset_id, COUNT(DISTINCT domain_id) AS n
        FROM meta.data_access
        WHERE dataset_id IS NOT NULL
        GROUP BY dataset_id
        ORDER BY n DESC, dataset_id
        LIMIT 1
        """
    )
    if shared.empty:
        check("a dataset exists to test sharing", False)
    else:
        ds, ds_n = shared.iloc[0]["dataset_id"], int(shared.iloc[0]["n"])
        using = meta.get_sites_using_dataset(ds)
        check("get_sites_using_dataset finds every site sharing a dataset",
              len(using) == ds_n, f"{ds}: {len(using)} of {ds_n} sites")

    # ---- writes, rolled back ---------------------------------------
    print("\nWRITES (real statements, rolled back)")
    conn = psycopg.connect(url(), autocommit=False)
    tx = MetaClient(_Tx(conn))
    cur = conn.cursor()

    def count(sql, params=None):
        cur.execute(sql, params or {})
        return cur.fetchone()[0]

    # Pick real rows to write against instead of naming domain 22. Which
    # ids exist is not what this check is about, and pinning to them means
    # the check fails the day that site is renamed or removed.
    fixture = meta.sb.query(
        "SELECT domain_id, client_id FROM meta.site ORDER BY domain_id LIMIT 2")
    if len(fixture) < 2:
        raise RuntimeError("need at least two sites to run the write checks")
    site_a = int(fixture.iloc[0]["domain_id"])
    site_b = int(fixture.iloc[1]["domain_id"])
    client_a = int(fixture.iloc[0]["client_id"])

    # Two tools the chosen site does not already use, so the insert checks
    # start from a known-empty slot rather than colliding with real access.
    used = set(meta.sb.query(
        "SELECT tool FROM meta.data_access WHERE domain_id = %(d)s",
        {"d": site_a})["tool"])
    free = [t for t in MetaClient.TOOLS if t not in used]
    if len(free) < 2:
        raise RuntimeError(f"site {site_a} already uses every tool; no free slot")
    tool_x, tool_y = free[0], free[1]
    print(f"  (fixtures: sites {site_a}/{site_b}, client {client_a}, "
          f"tools {tool_x}/{tool_y})")

    before_sites = count("select count(*) from meta.site")
    tx.upsert_site(domain_id=site_a, client_id=client_a,
                   engagement_status="client",
                   source="live-check", industry="live-check-industry")
    check("upsert_site updates an existing site rather than duplicating",
          count("select count(*) from meta.site") == before_sites)
    check("upsert_site wrote the field",
          count("select count(*) from meta.site where domain_id=%(d)s "
                "and industry='live-check-industry'", {"d": site_a}) == 1)

    tx.update_site(site_a, title_brand="LiveCheckBrand")
    check("update_site leaves other columns alone",
          count("select count(*) from meta.site where domain_id=%(d)s "
                "and industry='live-check-industry' "
                "and title_brand='LiveCheckBrand'", {"d": site_a}) == 1)

    tx.update_site_priority_batch(
        [{"domain_id": site_a, "priority": "VERY HIGH"},
         {"domain_id": site_b, "priority": "LOW"}])
    check("update_site_priority_batch writes both in one statement",
          count("select count(*) from meta.site where "
                "(domain_id=%(a)s and priority='VERY HIGH') or "
                "(domain_id=%(b)s and priority='LOW')",
                {"a": site_a, "b": site_b}) == 2)

    before_access = count("select count(*) from meta.data_access")
    tx.add_data_access(domain_id=site_a, tool=tool_x, source="live-check",
                       account_identifier="proj-1")
    check("add_data_access inserts a new account",
          count("select count(*) from meta.data_access") == before_access + 1)
    tx.add_data_access(domain_id=site_a, tool=tool_x, source="live-check",
                       account_identifier="proj-1")
    check("add_data_access is idempotent on the same account",
          count("select count(*) from meta.data_access") == before_access + 1)
    tx.add_data_access(domain_id=site_a, tool=tool_x, source="live-check",
                       account_identifier="proj-2")
    check("a SECOND account for the same tool is allowed",
          count("select count(*) from meta.data_access") == before_access + 2)

    tx.update_data_access(domain_id=site_a, tool=tool_x,
                          account_identifier="proj-1",
                          access_status="revoked")
    check("update_data_access hits ONE row, not both accounts",
          count("select count(*) from meta.data_access where domain_id=%(d)s "
                "and tool=%(t)s and access_status='revoked'",
                {"d": site_a, "t": tool_x}) == 1)

    tx.deactivate_data_access(domain_id=site_a, tool=tool_x,
                              account_identifier="proj-2")
    check("deactivate_data_access flags rather than deletes",
          count("select count(*) from meta.data_access where domain_id=%(d)s "
                "and tool=%(t)s and is_active=false",
                {"d": site_a, "t": tool_x}) == 1
          and count("select count(*) from meta.data_access") ==
          before_access + 2)

    tx.delete_data_access(domain_id=site_a, tool=tool_x,
                          account_identifier="proj-1")
    tx.delete_data_access(domain_id=site_a, tool=tool_x,
                          account_identifier="proj-2")
    check("delete_data_access removes exactly what it named",
          count("select count(*) from meta.data_access") == before_access)

    # a tool with no account identifier at all
    tx.add_data_access(domain_id=site_a, tool=tool_y, source="live-check")
    tx.add_data_access(domain_id=site_a, tool=tool_y, source="live-check")
    check("a NULL account identifier still deduplicates",
          count("select count(*) from meta.data_access where domain_id=%(d)s "
                "and tool=%(t)s", {"d": site_a, "t": tool_y}) == 1)

    conn.rollback()
    conn.close()

    after = MetaClient(SupabaseClient(url()))
    check("\n  nothing persisted: site count unchanged",
          len(after.list_sites()) == site_count, f"still {site_count}")

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
