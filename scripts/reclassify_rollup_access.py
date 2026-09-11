"""Reclassify multi-site rollup datasets from a real tool to tool='other'.

WHY
---
A dataset that covers several sites was registered on each of them under
the tool it came from. meta.data_access then holds TWO rows for that
site/tool -- the site's own account, and the shared rollup -- and a
consumer asking "what is this site's GA4 source" gets an ambiguity it
cannot resolve. WQA's discovery routes that to a human picker, and the
unattended driver raises, so scheduled runs die.

The rollup is still worth keeping: it is the only place the cross-site
view lives. Moving it to tool='other' keeps the link while stopping it
masquerading as the site's source for a real tool.

SAFETY
------
Dry run by default: prints the exact before/after and rolls back.
Only rewrites rows that are BOTH shared across multiple sites AND sat
alongside an unshared row for the same site+tool -- so a site whose only
source is a shared dataset is left alone, because demoting that would
take its data away.

    uv run python scripts/reclassify_rollup_access.py            # dry run
    uv run python scripts/reclassify_rollup_access.py --apply
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

import psycopg

EXPECTED_PROJECT_REF = "ycvkkukiulygmmkcpsnt"

# Rows that are shared across >1 site AND have an unshared sibling for the
# same site+tool. The sibling test is what stops us demoting a site's only
# source.
SELECT_TARGETS = """
WITH shared AS (
    SELECT dataset_id
    FROM meta.data_access
    WHERE dataset_id IS NOT NULL
    GROUP BY dataset_id
    HAVING COUNT(DISTINCT domain_id) > 1
),
has_unshared_sibling AS (
    SELECT da.domain_id, da.tool
    FROM meta.data_access da
    WHERE da.dataset_id IS NOT NULL
      AND da.dataset_id NOT IN (SELECT dataset_id FROM shared)
)
SELECT da.domain_id, d.domain, da.tool, da.dataset_id, da.account_identifier
FROM meta.data_access da
JOIN meta.domains d USING (domain_id)
WHERE da.dataset_id IN (SELECT dataset_id FROM shared)
  AND da.tool <> 'other'
  AND (da.domain_id, da.tool) IN (SELECT domain_id, tool FROM has_unshared_sibling)
ORDER BY da.tool, d.domain, da.dataset_id
"""

UPDATE_ONE = """
UPDATE meta.data_access
   SET tool = 'other',
       notes = COALESCE(notes || ' | ', '') ||
               'Reclassified from ' || %(old_tool)s || ' to other: this dataset '
               'covers multiple sites and this site has its own ' || %(old_tool)s ||
               ' source. Kept so the cross-site rollup is not lost.',
       updated_at = now()
 WHERE domain_id = %(domain_id)s
   AND tool = %(old_tool)s
   AND dataset_id = %(dataset_id)s
"""


def url() -> str:
    u = os.environ.get("SUPABASE_DB_URL", "").strip().strip('"')
    if not u:
        for candidate in ("secrets/skyward-ops-supabase.env",
                          "../skyward-common/secrets/skyward-ops-supabase.env",
                          ".env"):
            path = pathlib.Path(candidate)
            if path.is_file():
                found = re.search(r"^SUPABASE_DB_URL=(.+)$", path.read_text(), re.M)
                if found:
                    u = found.group(1).strip().strip('"')
                    break
    if not u:
        raise RuntimeError("SUPABASE_DB_URL is not set and no fallback file had it")
    if EXPECTED_PROJECT_REF not in u:
        raise RuntimeError("refusing: not skyward-ops")
    return u


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Commit. Without this the script rolls back.")
    args = parser.parse_args(argv)

    conn = psycopg.connect(url(), autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute(SELECT_TARGETS)
            targets = cur.fetchall()

            if not targets:
                print("Nothing to reclassify.")
                return 0

            print(f"{len(targets)} row(s) would move to tool='other':\n")
            print(f"  {'domain':34} {'from':11} dataset")
            for domain_id, domain, tool, dataset_id, _acct in targets:
                print(f"  {domain:34} {tool:11} {dataset_id}")

            for domain_id, _domain, tool, dataset_id, _acct in targets:
                cur.execute(UPDATE_ONE, {"domain_id": domain_id, "old_tool": tool,
                                         "dataset_id": dataset_id})

            # Prove the ambiguity is actually gone before committing.
            cur.execute("""
                SELECT d.domain, da.tool, COUNT(*)
                FROM meta.data_access da
                JOIN meta.domains d USING (domain_id)
                WHERE da.tool <> 'other'
                GROUP BY d.domain, da.tool
                HAVING COUNT(*) > 1
                ORDER BY 3 DESC, 1
            """)
            leftover = cur.fetchall()
            print(f"\nSites still holding 2+ rows for one real tool: {len(leftover)}")
            for domain, tool, n in leftover:
                print(f"  {domain:34} {tool:11} {n}")

        if args.apply:
            conn.commit()
            print("\nCOMMITTED.")
        else:
            conn.rollback()
            print("\n[dry run - rolled back, nothing written]")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
