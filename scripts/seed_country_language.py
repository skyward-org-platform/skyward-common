"""Seed meta.country and meta.language from DataForSEO reference data.

Global reference data, not per-client: run once, re-run whenever
DataForSEO adds a country or language. Idempotent -- rows are upserted
on their primary key, so re-running updates names and codes in place
and never duplicates.

Both endpoints are free reference lookups. Neither posts a task, so
neither costs balance.

    uv run python scripts/seed_country_language.py --show
    uv run python scripts/seed_country_language.py --dry-run
    uv run python scripts/seed_country_language.py

WHY meta.language.code CAN'T BE PLAIN ISO 639-1
-----------------------------------------------
DataForSEO's language list is BCP-47-shaped: mostly bare ISO 639-1
('en', 'fr'), but 23 of 129 carry a region or script subtag
('pt-BR' and 'pt-PT', 'zh-CN' and 'zh-TW', 'sr-Latn' and 'sr-ME').
Collapsing those to their base language would collide on the primary
key -- 'pt' cannot be two rows. So the grain of the table is the
language tag, not the ISO 639-1 language.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys


EXPECTED_PROJECT_REF = "ycvkkukiulygmmkcpsnt"


def database_url() -> str:
    import os

    url = os.environ.get("SUPABASE_DB_URL")
    if url:
        return url
    env = pathlib.Path("/mnt/d/Skyward/skyward-seo-pipeline/.env")
    match = re.search(r"^SUPABASE_DB_URL=(.+)$", env.read_text(), re.M)
    if not match:
        raise RuntimeError("SUPABASE_DB_URL not set, and not in the pipeline .env")
    return match.group(1).strip().strip('"').strip("'")


def assert_expected_database(url: str) -> None:
    if EXPECTED_PROJECT_REF not in url:
        raise RuntimeError(
            f"refusing to run: SUPABASE_DB_URL does not name {EXPECTED_PROJECT_REF!r}"
        )


def fetch_countries(client) -> list[tuple[str, str, int]]:
    """(iso2, name, dfs_location_code) for every DataForSEO country."""
    rows = []
    for loc in client.get_serp_locations():
        if loc.get("location_type") != "Country":
            continue
        iso = (loc.get("country_iso_code") or "").strip().upper()
        if len(iso) != 2 or not iso.isalpha():
            continue
        rows.append((iso, loc["location_name"], loc["location_code"]))
    return rows


def fetch_languages(client) -> list[tuple[str, str]]:
    """(code, name) for every DataForSEO SERP language."""
    resp = client._get(f"{client.BASE_URL}/serp/google/languages")
    try:
        result = resp["tasks"][0]["result"]
    except (TypeError, KeyError, IndexError) as exc:
        raise RuntimeError("unexpected shape from /serp/google/languages") from exc
    return [(row["language_code"], row["language_name"]) for row in result]


def show(conn) -> None:
    with conn.cursor() as cur:
        for table in ("country", "language"):
            cur.execute(f"select count(*) from meta.{table}")  # noqa: S608
            print(f"meta.{table}: {cur.fetchone()[0]} rows")
        cur.execute(
            "select iso2, name, dfs_location_code from meta.country "
            "where iso2 in ('US','CA','AU','GB') order by iso2"
        )
        for row in cur.fetchall():
            print("  ", row)
        cur.execute(
            "select code, name from meta.language "
            "where code in ('en','fr') order by code"
        )
        for row in cur.fetchall():
            print("  ", row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true",
                        help="print current contents and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="write inside a transaction, then roll back")
    args = parser.parse_args(argv)

    import psycopg

    url = database_url()
    assert_expected_database(url)

    if args.show:
        with psycopg.connect(url) as conn:
            show(conn)
        return 0

    from skyward.config import load_config
    from skyward.data.dataforseo import DataForSEOClient

    cfg = load_config()
    client = DataForSEOClient(cfg.dataforseo_username, cfg.dataforseo_password)

    countries = fetch_countries(client)
    languages = fetch_languages(client)
    print(f"fetched {len(countries)} countries, {len(languages)} languages")

    if not countries or not languages:
        print("refusing to write an empty seed")
        return 1

    mode = "DRY RUN (rolls back)" if args.dry_run else "WRITING"
    print(f"{mode}\n")

    with psycopg.connect(url, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """insert into meta.country (iso2, name, dfs_location_code)
                   values (%s, %s, %s)
                   on conflict (iso2) do update
                     set name = excluded.name,
                         dfs_location_code = excluded.dfs_location_code""",
                countries,
            )
            cur.executemany(
                """insert into meta.language (code, name, dfs_language_code)
                   values (%s, %s, %s)
                   on conflict (code) do update
                     set name = excluded.name,
                         dfs_language_code = excluded.dfs_language_code""",
                [(code, name, code) for code, name in languages],
            )
            show(conn)

        if args.dry_run:
            conn.rollback()
            print("\nDRY RUN OK — rolled back, nothing persisted.")
        else:
            conn.commit()
            print("\nCommitted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
