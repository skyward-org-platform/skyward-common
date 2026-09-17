"""No shipped code may read or write the deprecated Meta tables.

meta.client_domains and meta.client_datasets were renamed to
*_deprecated on 2026-09-15 (commit 2d8d101). Every SQL statement against
the old names now fails with UndefinedTable at runtime.

This is a static guard because the ordinary suite cannot catch it: every
Meta test runs against a fake client, so a query naming a table that no
longer exists passes exactly as happily as one naming a live table. That
is precisely how v1.6.1 shipped with a broken write path -- the release
renamed the tables and left ~20 statements in meta/client.py still
pointing at them.

Scripts under scripts/ are excluded on purpose: the backfill and
migration scripts legitimately read the old tables, which is their job.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

# Matches a deprecated table only where it is being queried -- after FROM,
# JOIN, INTO or UPDATE -- so prose in docstrings and error messages that
# names the old table (to explain the migration) does not trip this.
PATTERN = re.compile(
    r"\b(from|join|into|update)\s+meta\.client_(domains|datasets)\b",
    re.IGNORECASE,
)


def test_no_sql_against_deprecated_meta_tables():
    offenders = []
    for path in SRC.rglob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if PATTERN.search(line):
                rel = path.relative_to(SRC.parent.parent)
                offenders.append(f"{rel}:{i}: {line.strip()}")

    assert not offenders, (
        "Shipped code still queries the deprecated Meta tables. They were "
        "renamed to client_domains_deprecated / client_datasets_deprecated, "
        "so these statements fail at runtime with UndefinedTable. Use "
        "meta.site, meta.site_competitors and meta.data_access instead:\n  "
        + "\n  ".join(offenders)
    )
