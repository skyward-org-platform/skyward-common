"""Copy meta.client_domains -> meta.site and meta.client_datasets ->
meta.data_access. Both old tables are left untouched.

Neither old table is dropped and nothing is repointed: the pairs run
side by side until every consumer has moved, which is its own ticket.
The drop is that ticket's last step.

ORDER MATTERS. data_access.domain_id references meta.site, so the sites
must exist before any access row can be written.

    uv run python scripts/backfill_site_and_data_access.py --dry-run
    uv run python scripts/backfill_site_and_data_access.py

WHAT IS NOT COPIED
------------------
client_domains rows with is_competitor = true. Competitors moved to
meta.site_competitors in July and that table is authoritative; the flag
left behind on client_domains is the superseded copy.

client_datasets rows with a NULL domain_id. The new table forbids it --
that NULL is the bug the redesign exists to fix, because a row that
should have been domain-scoped silently contaminates a sibling
territory. Those rows are reported for a human to place.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

EXPECTED_PROJECT_REF = "ycvkkukiulygmmkcpsnt"

# engagement_status, decided with Adam 2026-09-08.
#
# The rule is whether the owning client has a ClickUp Delivery folder,
# matched on the abbreviation that already drives ClickUp naming. Three
# exceptions are active without one, and four former clients have none
# because the folder is gone rather than never created -- neither case
# is inferable from the data, so both are listed explicitly.
DELIVERY = {"DS", "GCS", "KG", "KL", "RRD", "ME", "PLP", "S1", "UC"}
FORCE_CLIENT = {46, 48, 53}          # kwev, transportdigitalmedia, goskyward
FORCE_CANCELLED = {39, 40, 41, 50}   # the three Sears properties, Three Trees
CANCELLED_CLIENTS = {"TNA"}          # engagement ended; pipeline data stays
SKIP = {21}                          # transportnetworkaustralia.com.au2


def engagement_status(domain_id: int, domain: str,
                      abbrev: str | None) -> str | None:
    """None means the row is not written at all."""
    if domain_id in SKIP:
        return None
    if domain_id in FORCE_CLIENT:
        return "client"
    if domain_id in FORCE_CANCELLED:
        return "canceled"
    if domain.endswith(".replit.app"):
        # What the domain IS beats whose it is: a prototype for a
        # canceled client is still a prototype.
        return "prototype"
    ab = (abbrev or "").split("-")[0].strip().upper()
    if ab in CANCELLED_CLIENTS:
        return "canceled"
    return "client" if ab in DELIVERY else "prospect"


# client_datasets has no tool column; the dataset name is the only clue.
TOOL_BY_PREFIX = (
    ("jepto_gsc", "gsc"),
    ("jepto_gmb", "gbp"),
    ("jepto_facebook", "facebook"),
    ("analytics", "ga4"),
    ("agent_ga4", "ga4"),
    ("gads", "google_ads"),
)


def database_url() -> str:
    import os

    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        env = pathlib.Path("/mnt/d/Skyward/skyward-seo-pipeline/.env")
        m = re.search(r"^SUPABASE_DB_URL=(.+)$", env.read_text(), re.M)
        url = m.group(1).strip().strip('"').strip("'")
    if EXPECTED_PROJECT_REF not in url:
        raise RuntimeError("refusing: not the skyward-ops database")
    return url


def tool_for(dataset: str) -> str | None:
    for prefix, tool in TOOL_BY_PREFIX:
        if dataset.startswith(prefix):
            return tool
    return None


def backfill_sites(cur) -> tuple[int, int]:
    """One meta.site row per client-owned domain."""
    cur.execute(
        """
        select cd.domain_id, cd.client_id, cd.notes, d.is_active, d.domain,
               cl.abbreviation
        from meta.client_domains cd
        join meta.domains d using (domain_id)
        left join meta.clients cl on cl.client_id = cd.client_id
        where not cd.is_competitor
        order by cd.domain_id
        """
    )
    rows = cur.fetchall()
    written, by_status = 0, {}
    for domain_id, client_id, notes, is_active, domain, abbrev in rows:
        status = engagement_status(domain_id, domain, abbrev)
        by_status[status] = by_status.get(status, 0) + 1
        if status is None:
            continue
        cur.execute(
            """
            insert into meta.site (domain_id, client_id, engagement_status,
                                   lifecycle_status, notes, source)
            values (%s, %s, %s, %s, %s, 'client_domains_backfill')
            on conflict (domain_id) do nothing
            """,
            (domain_id, client_id, status,
             "active" if is_active else "paused", notes),
        )
        written += cur.rowcount
    return len(rows), written, by_status


def backfill_access(cur) -> tuple[int, int, list]:
    """One meta.data_access row per domain per tool."""
    cur.execute(
        """
        select cd.domain_id, cd.dataset_id, cd.is_active, cd.notes,
               cd.client_id
        from meta.client_datasets cd
        order by cd.domain_id nulls last, cd.dataset_id
        """
    )
    rows = cur.fetchall()
    skipped, written = [], 0
    for domain_id, dataset_id, is_active, notes, client_id in rows:
        tool = tool_for(dataset_id)
        if domain_id is None:
            skipped.append((client_id, dataset_id, "no domain_id"))
            continue
        if tool is None:
            skipped.append((client_id, dataset_id, "tool not inferable"))
            continue
        cur.execute("select 1 from meta.site where domain_id = %s",
                    (domain_id,))
        if cur.fetchone() is None:
            skipped.append((client_id, dataset_id,
                            f"domain {domain_id} has no meta.site row"))
            continue
        cur.execute(
            """
            insert into meta.data_access
                (domain_id, tool, access_status, storage_platform,
                 storage_project, dataset_id, is_active, notes, source)
            values (%s, %s, 'granted', 'bigquery', 'data-hub-468216',
                    %s, %s, %s, 'client_datasets_backfill')
            on conflict (domain_id, tool) do nothing
            """,
            (domain_id, tool, dataset_id, is_active, notes),
        )
        written += cur.rowcount
    return len(rows), written, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="write inside a transaction, then roll back")
    args = ap.parse_args()

    import psycopg

    with psycopg.connect(database_url(), autocommit=False) as conn:
        with conn.cursor() as cur:
            for table in ("meta.site", "meta.data_access"):
                cur.execute(f"select count(*) from {table}")
                print(f"  {table} before: {cur.fetchone()[0]}")

            seen, made, by_status = backfill_sites(cur)
            print(f"\nmeta.site: {seen} client-owned domains, "
                  f"{made} rows written")
            for status, count in sorted(
                    by_status.items(), key=lambda kv: (kv[0] is None, kv[0])):
                label = status or "skipped (not written)"
                print(f"    {label:<24} {count}")

            seen, made, skipped = backfill_access(cur)
            print(f"meta.data_access: {seen} source rows, "
                  f"{made} rows written, {len(skipped)} held back")
            for client_id, dataset, why in skipped:
                print(f"    client {client_id}  {dataset:<34} {why}")

            for table in ("meta.client_domains", "meta.client_datasets"):
                cur.execute(f"select count(*) from {table}")
                print(f"\n  {table} after: {cur.fetchone()[0]} "
                      f"(untouched)")

        if args.dry_run:
            conn.rollback()
            print("\nDRY RUN — rolled back, nothing persisted.")
        else:
            conn.commit()
            print("\nCommitted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
