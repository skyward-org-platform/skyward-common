"""Populate meta.data_access.account_identifier, then place the rows
that had no domain.

Two jobs, in order, because the second depends on the first:

1. Derive account_identifier from the dataset name where the name
   embeds it. gads_backfill_2720744565 is customer 2720744565;
   analytics_255778584 is GA4 property 255778584. Nothing is invented:
   a name that embeds no id leaves the column null.

2. Fan out the five client_datasets rows that had a NULL domain_id.
   The new table forbids that null -- it is the bug the redesign exists
   to remove, because a row that should have been domain-scoped
   silently contaminates a sibling territory. Each becomes one row per
   site of the owning client, same dataset, which is what the
   account-scoped key makes possible.

   Prototype sites are skipped: a .replit.app build has no analytics
   account and no ad spend.

    uv run python scripts/backfill_account_identifiers.py --dry-run
    uv run python scripts/backfill_account_identifiers.py
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

EXPECTED_PROJECT_REF = "ycvkkukiulygmmkcpsnt"

# Dataset naming conventions that embed the account id.
DERIVE = (
    (re.compile(r"^gads_backfill_(\d+)$"), "google_ads"),
    (re.compile(r"^analytics_(\d+)$"), "ga4"),
)

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


def account_id_for(dataset: str) -> str | None:
    for pattern, _tool in DERIVE:
        m = pattern.match(dataset or "")
        if m:
            return m.group(1)
    return None


def tool_for(dataset: str) -> str | None:
    for prefix, tool in TOOL_BY_PREFIX:
        if dataset.startswith(prefix):
            return tool
    return None


def derive_existing(cur) -> tuple[int, int]:
    cur.execute("select data_access_id, dataset_id from meta.data_access "
                "where account_identifier is null and dataset_id is not null")
    rows = cur.fetchall()
    filled = 0
    for access_id, dataset in rows:
        account = account_id_for(dataset)
        if account is None:
            continue
        cur.execute(
            "update meta.data_access set account_identifier = %s, "
            "updated_at = now() where data_access_id = %s",
            (account, access_id),
        )
        filled += cur.rowcount
    return len(rows), filled


def place_unscoped(cur) -> tuple[int, list]:
    cur.execute(
        "select client_id, dataset_id, is_active, notes "
        "from meta.client_datasets where domain_id is null "
        "order by client_id, dataset_id"
    )
    written, detail = 0, []
    for client_id, dataset, is_active, notes in cur.fetchall():
        tool = tool_for(dataset)
        account = account_id_for(dataset)
        cur.execute(
            "select domain_id from meta.site where client_id = %s "
            "and engagement_status <> 'prototype' order by domain_id",
            (client_id,),
        )
        sites = [r[0] for r in cur.fetchall()]
        made = 0
        for domain_id in sites:
            cur.execute(
                """
                insert into meta.data_access
                    (domain_id, tool, access_status, storage_platform,
                     storage_project, dataset_id, account_identifier,
                     is_active, notes, source)
                values (%s, %s, 'granted', 'bigquery', 'data-hub-468216',
                        %s, %s, %s, %s, 'client_datasets_backfill')
                on conflict do nothing
                """,
                (domain_id, tool, dataset, account, is_active, notes),
            )
            made += cur.rowcount
        written += made
        detail.append((dataset, tool, account, len(sites), made))
    return written, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import psycopg

    with psycopg.connect(database_url(), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) from meta.data_access")
            print(f"  meta.data_access before: {cur.fetchone()[0]}")

            seen, filled = derive_existing(cur)
            print(f"\naccount_identifier: {seen} rows had none, "
                  f"{filled} derived from the dataset name")

            written, detail = place_unscoped(cur)
            print(f"\nunscoped rows fanned out: {written} rows written")
            for dataset, tool, account, sites, made in detail:
                print(f"    {dataset:<34} {tool:<12} "
                      f"acct={account or '-':<12} {made}/{sites} sites")

            cur.execute("select count(*) from meta.data_access")
            print(f"\n  meta.data_access after: {cur.fetchone()[0]}")
            cur.execute("select count(*) from meta.data_access "
                        "where account_identifier is not null")
            print(f"  with an account_identifier: {cur.fetchone()[0]}")

        if args.dry_run:
            conn.rollback()
            print("\nDRY RUN — rolled back, nothing persisted.")
        else:
            conn.commit()
            print("\nCommitted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
