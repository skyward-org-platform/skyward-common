"""Apply this repo's migrations to Supabase, without touching shared history.

COPIED FROM skyward-knowledge-base, DELIBERATELY
------------------------------------------------
This is a stopgap so the P0 schema migration could land now. The real
fix is a shared, importable migration runner in skyward-common that a
downstream repo adopts in a few lines -- tracked separately. Retire
this copy when that exists.

WHY THIS EXISTS INSTEAD OF `supabase db push`
---------------------------------------------
Supabase records applied migrations in ONE table per PROJECT
(`supabase_migrations.schema_migrations`), not per repo. Three repos
write to skyward-ops: this one owns `meta`, `brand` and `site`,
seo-pipeline owns `sf_embeddings`, and skyward-knowledge-base owns
`knowledge_base*`.

Before pushing, the CLI requires the local migrations directory to
contain every migration in the remote history. Ours cannot -- we do not
have the other repos' files -- so `db push` refuses, and `--include-all`
does not bypass it.

Worse, writing OUR migrations into that shared table would break THEM:
the next push from another repo would find a remote migration it has no
local file for and hit the identical error. So this is not merely our
inconvenience, it is a landmine we would be leaving for the others.

This script therefore keeps its own bookkeeping inside our own schema
and never writes to `supabase_migrations`.

USAGE
-----
    uv run python scripts/apply_migrations.py --dry-run   # execute, then roll back
    uv run python scripts/apply_migrations.py             # apply for real

The dry run executes every statement for real inside a transaction and
then rolls back, so SEMANTIC errors surface without persisting anything
-- a bad operator class, or a generated column whose expression is not
immutable. Grammar-only validation catches neither.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import sys

# skyward-ops. Anything else is refused rather than trusted.
EXPECTED_PROJECT_REF = "ycvkkukiulygmmkcpsnt"

# <version>_<name>.sql — the version fixes apply order.
FILENAME = re.compile(r"^(?P<version>\d{14})_(?P<name>.+)\.sql$")


class MigrationError(RuntimeError):
    pass


def discover_migrations(directory: pathlib.Path) -> list[tuple[str, str, pathlib.Path]]:
    """Return (version, name, path) for each migration, in apply order.

    Order is filename order, so a file without a sortable version has no
    defined position — rejected rather than guessed at.
    """
    found: list[tuple[str, str, pathlib.Path]] = []

    for path in sorted(directory.glob("*.sql")):
        match = FILENAME.match(path.name)
        if not match:
            raise MigrationError(
                f"{path.name} does not match <14-digit-version>_<name>.sql, "
                f"so its apply order is undefined"
            )
        found.append((match["version"], match["name"], path))

    return found


def plan_migration(
    version: str, name: str, *, recorded: str | None, checksum: str
) -> str:
    """Decide what to do with one migration: 'run' or 'skip'.

    Applied migrations are immutable. Editing one means the database no
    longer matches the file that supposedly produced it, and re-running is
    not an option because it already ran — so this raises rather than
    guessing which side is correct.
    """
    if recorded is None:
        return "run"
    if recorded == checksum:
        return "skip"
    raise MigrationError(
        f"{version}_{name} was already applied but the file has since changed "
        f"({recorded} -> {checksum}). Write a new migration rather than editing "
        f"an applied one."
    )


def database_url() -> str:
    """The target connection string, from named sources only.

    Never searched for. A search could silently pick up a different
    database, which is precisely how the June 2026 incident happened.
    """
    url = os.environ.get("SUPABASE_DB_URL")
    if url:
        return url

    env = pathlib.Path("/mnt/d/Skyward/skyward-seo-pipeline/.env")
    match = re.search(r"^SUPABASE_DB_URL=(.+)$", env.read_text(), re.M)
    if not match:
        raise MigrationError("SUPABASE_DB_URL not set, and not found in the pipeline .env")
    return match.group(1).strip().strip('"').strip("'")


def assert_expected_database(url: str) -> None:
    """Refuse to run anywhere unexpected.

    skyward-ops is shared with skyward-common and seo-pipeline, and has a
    documented data-loss incident (2026-06-10: a misconfigured connection
    string wiped live meta data). An explicit check costs nothing.
    """
    if EXPECTED_PROJECT_REF not in url:
        raise MigrationError(
            f"refusing to run: connection string does not reference "
            f"{EXPECTED_PROJECT_REF!r}. Set SUPABASE_DB_URL deliberately."
        )


def _ensure_tracking(conn, schema: str) -> None:
    """Create OUR bookkeeping table. Never supabase_migrations — writing
    there would break the other repos' pushes."""
    with conn.cursor() as cur:
        cur.execute(f"create schema if not exists {schema}")
        cur.execute(
            f"""create table if not exists {schema}.applied_migrations (
                   version    text primary key,
                   name       text        not null,
                   checksum   text        not null,
                   applied_at timestamptz not null default now()
               )"""
        )
    conn.commit()


def _already_applied(conn, schema: str) -> dict[str, str]:
    """Versions already applied, or empty if the table does not exist yet.

    Existence is checked rather than caught, because an exception inside
    a transaction poisons the connection and the caller would then need
    to roll back before doing anything else.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select to_regclass(%s) is not null", (f"{schema}.applied_migrations",)
        )
        if not cur.fetchone()[0]:
            return {}

        cur.execute(f"select version, checksum from {schema}.applied_migrations")  # noqa: S608
        return dict(cur.fetchall())


def select_only(pending, *, only: str | None):
    """Narrow a pending list to one migration, by version prefix.

    Applying a single migration has to be possible without replaying
    the ones before it. `applied_migrations` can disagree with the live
    database -- 009 and 010 were applied by hand during a table-by-table
    walkthrough and never recorded -- and reconciling that is a
    deliberate decision, not something to do accidentally on the way to
    applying an unrelated migration.

    Matched on the version prefix rather than an index, because
    filenames carry timestamps and every new migration shifts indices.

    A selector matching nothing RAISES. Reporting success having
    applied nothing is how a typo becomes a migration everyone believes
    is live.
    """
    if only is None:
        return pending
    chosen = [row for row in pending if row[0] == only]
    if not chosen:
        raise ValueError(
            f"no migration with version {only!r}; nothing was applied")
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply skyward-common migrations.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="execute everything in a transaction, then roll back",
    )
    parser.add_argument("--schema", default="meta")
    parser.add_argument(
        "--mark-applied",
        action="store_true",
        help="record the selected migrations as applied WITHOUT running "
             "them. For migrations already live in the database that "
             "were applied outside this script. Requires --only, "
             "because marking the wrong thing leaves a database "
             "missing a change nobody will run again.",
    )
    parser.add_argument(
        "--only",
        help="apply ONLY the migration with this version prefix, e.g. "
             "20260818200000. For when earlier migrations are applied "
             "but unrecorded and replaying them would fail.",
    )
    args = parser.parse_args(argv)

    import psycopg  # imported here so --help works without the driver present

    url = database_url()
    assert_expected_database(url)

    root = pathlib.Path(__file__).resolve().parents[1]
    pending = discover_migrations(root / "db/supabase/migrations")
    if not pending:
        print("no migration files found")
        return 0

    mode = "DRY RUN (rolls back)" if args.dry_run else "APPLYING FOR REAL"
    print(f"{mode} -> schema {args.schema}\n")

    if args.mark_applied and not args.only:
        print("--mark-applied requires --only: marking every pending "
              "migration as applied is never the intent")
        return 1

    with psycopg.connect(url, autocommit=False) as conn:
        if not args.dry_run:
            _ensure_tracking(conn, args.schema)

        # Read what is already applied in BOTH modes. A dry run that
        # replays every migration from the beginning is useless: it fails
        # on the first CREATE TABLE and tells you nothing about the one
        # you actually wanted to check.
        recorded = _already_applied(conn, args.schema)

        ran = 0
        for version, name, path in select_only(pending, only=args.only):
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]

            if plan_migration(version, name,
                              recorded=recorded.get(version),
                              checksum=checksum) == "skip":
                print(f"  skip  {version}_{name} (already applied)")
                continue

            if args.mark_applied:
                # RECORDED, NOT RUN. The change is already in the
                # database; this only repairs the bookkeeping. Kept a
                # distinct action rather than a fallback when execution
                # errors, because marking something applied that is NOT
                # applied leaves a database permanently missing a change
                # nobody will ever run again -- worse than the stale
                # bookkeeping it fixes.
                print(f"  mark  {version}_{name}  (recorded, not run)")
                if not args.dry_run:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"insert into {args.schema}.applied_migrations "  # noqa: S608
                            f"(version, name, checksum) values (%s, %s, %s)",
                            (version, name, checksum),
                        )
                ran += 1
                continue

            print(f"  run   {version}_{name}  ({len(sql):,} chars)")
            with conn.cursor() as cur:
                cur.execute(sql)
                if not args.dry_run:
                    cur.execute(
                        f"insert into {args.schema}.applied_migrations "  # noqa: S608
                        f"(version, name, checksum) values (%s, %s, %s)",
                        (version, name, checksum),
                    )
            ran += 1

        if args.dry_run:
            conn.rollback()
            print(f"\nDRY RUN OK — {ran} migration(s) executed cleanly, then rolled "
                  f"back. Nothing persisted.")
        else:
            conn.commit()
            print(f"\nAPPLIED {ran} migration(s).")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

