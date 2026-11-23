"""Sync the Authority placement log (Google Sheet) into authority.placement.

The sheet is the input people log into: one row per link Skyward tries to
get. This script upserts it into Supabase by placement_id, so it is safe to
re-run as often as you like: unchanged rows are left alone (no change-log
noise), changed rows are updated, new rows are inserted.

What the sheet owns and what the table owns
-------------------------------------------
People edit every spec column EXCEPT these six, which the table owns and
this script writes back into the sheet after each run so people can see them:

    spam_score, site_us_traffic, metrics_read_at   (scripts/enrich_authority_placement.py)
    quality, counted                               (generated columns)
    updated_at                                     (set on every real change)

quality = live AND followed AND spam < 40 AND US traffic >= 1000.
counted = quality, but not pre_existing (live before Skyward started) and not
paid. Only counted links count toward the target.

A row with a blank placement_id is a new row: the script gives it a uuid and
writes the uuid back into the sheet, so the row keeps the same key forever.
Rows deleted from the sheet are NOT deleted from the table; the run lists
them so someone can decide.

Any invalid row stops the whole run before anything is written, with a list
of every problem (row number and column), so the table never holds half a
sheet.

USAGE
-----
    # once: give your Google login read/write access to Sheets
    gcloud auth application-default login \\
        --scopes=https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/cloud-platform

    SUPABASE_DB_URL=... uv run python scripts/sync_authority_placement.py --dry-run
    SUPABASE_DB_URL=... uv run python scripts/sync_authority_placement.py

    # without Google API access (Workspace blocks gcloud's Sheets scope):
    # File > Download > CSV on the `placement` tab, then
    SUPABASE_DB_URL=... uv run python scripts/sync_authority_placement.py --csv ~/Downloads/<file>.csv
    # Same upsert and checks. Nothing is written back to the sheet, so new
    # rows must already have a placement_id; the run refuses otherwise.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

SPREADSHEET_ID = "14zS3lHmNO8RMynHQBkhdH15K87R7g7YDM-ZWS2bqjds"
TAB = "placement"
EXPECTED_PROJECT_REF = "ycvkkukiulygmmkcpsnt"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"

# Columns people edit, in the order they are written to the table.
SHEET_OWNED = [
    "placement_id", "domain_id", "site", "url_live", "target_page", "page_group",
    "anchor", "anchor_kind", "type", "tactic", "tier", "vendor", "cost_usd",
    "status", "contacted_at", "replied_at", "went_live", "check_30d",
    "check_90d", "dofollow", "pre_existing", "sponsored", "work_item_id", "notes",
]
# Columns the table owns; written back into the sheet, never read from it.
TABLE_OWNED = ["spam_score", "site_us_traffic", "metrics_read_at", "quality", "counted", "updated_at"]

ENUMS = {
    "page_group": {"home", "pricing", "location", "service", "blog", "other"},
    "anchor_kind": {"brand", "general", "partial", "exact"},
    "type": {"owned", "earned", "paid"},
    "tier": {"1", "2a", "2b", "2c"},
    "status": {"prospect", "contacted", "replied", "agreed", "live", "lost", "declined"},
}
DATES = {"contacted_at", "replied_at", "went_live"}
BOOLS = {"check_30d", "check_90d", "dofollow", "sponsored"}
TRUE, FALSE = {"true", "yes", "y", "1"}, {"false", "no", "n", "0"}


class SyncError(RuntimeError):
    pass


@dataclass
class Parsed:
    rows: list[dict]          # clean values keyed by SHEET_OWNED
    sheet_rows: list[int]     # 1-based sheet row number for each parsed row
    new_ids: dict[int, str]   # sheet row -> uuid given to a blank placement_id
    errors: list[str]


def _cell(raw: list, i: int) -> str:
    return str(raw[i]).strip() if i < len(raw) and raw[i] is not None else ""


def parse_sheet(values: list[list]) -> Parsed:
    """Turn the sheet's values (header first) into clean rows, or errors.

    Pure, so the rules are testable without Google or a database.
    """
    if not values:
        raise SyncError("the sheet is empty; expected a header row")
    header = [str(h).strip() for h in values[0]]
    missing = [c for c in SHEET_OWNED + TABLE_OWNED if c not in header]
    if missing:
        raise SyncError(f"the sheet is missing columns: {', '.join(missing)}")
    col = {name: header.index(name) for name in SHEET_OWNED}

    rows, sheet_rows, new_ids, errors, seen = [], [], {}, [], {}
    for n, raw in enumerate(values[1:], start=2):
        cells = {c: _cell(raw, i) for c, i in col.items()}
        if not any(v for c, v in cells.items() if c != "placement_id"):
            continue  # blank line
        out: dict = {}
        bad = lambda c, why: errors.append(f"row {n}, {c}: {why} (got {cells[c]!r})")  # noqa: E731

        pid = cells["placement_id"]
        if not pid:
            pid = str(uuid.uuid4())
            new_ids[n] = pid
        try:
            pid = str(uuid.UUID(pid))
        except ValueError:
            bad("placement_id", "not a uuid; leave it blank and the sync fills it")
        if pid in seen:
            bad("placement_id", f"same id as row {seen[pid]}")
        seen[pid] = n
        out["placement_id"] = pid

        try:
            out["domain_id"] = int(cells["domain_id"])
        except ValueError:
            bad("domain_id", "must be the client's whole-number domain_id")

        site = cells["site"].lower()
        if site and (re.match(r"^https?://", site) or "/" in site or " " in site):
            bad("site", "must be the bare host, like visitma.com")
        out["site"] = site or None

        for c in ("url_live", "target_page", "anchor", "tactic", "vendor", "work_item_id", "notes"):
            out[c] = cells[c] or None

        for c, allowed in ENUMS.items():
            v = cells[c].lower()
            if c == "status" and not v:
                bad(c, "is required")
            elif v and v not in allowed:
                bad(c, f"must be one of {', '.join(sorted(allowed))}")
            out[c] = v or None

        v = cells["cost_usd"].replace("$", "").replace(",", "")
        try:
            out["cost_usd"] = Decimal(v) if v else None
            if out["cost_usd"] is not None and out["cost_usd"] < 0:
                bad("cost_usd", "can't be negative")
        except InvalidOperation:
            bad("cost_usd", "must be a number")

        for c in DATES:
            v = cells[c]
            try:
                out[c] = dt.date.fromisoformat(v) if v else None
            except ValueError:
                bad(c, "must be a date written YYYY-MM-DD")

        for c in BOOLS:
            v = cells[c].lower()
            if v and v not in TRUE | FALSE:
                bad(c, "must be TRUE or FALSE, or blank if not known")
            out[c] = None if not v else v in TRUE

        v = cells["pre_existing"].lower()
        if v and v not in TRUE | FALSE:
            bad("pre_existing", "must be TRUE or FALSE (blank means FALSE)")
        out["pre_existing"] = v in TRUE

        if out.get("type") == "paid" and out.get("status") == "live" and out["sponsored"] is not True:
            bad("sponsored", 'a live paid link must be marked rel="sponsored"; check it and set TRUE')

        if out.get("status") == "live" and not out["site"]:
            bad("site", "a live link needs its site")

        rows.append(out)
        sheet_rows.append(n)
    return Parsed(rows, sheet_rows, new_ids, errors)


def database_url(local_test: bool) -> str:
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        raise SyncError("SUPABASE_DB_URL is not set")
    if local_test:
        if not re.search(r"@(localhost|127\.0\.0\.1)[:/]", url):
            raise SyncError("--local-test only runs against localhost")
    elif EXPECTED_PROJECT_REF not in url:
        raise SyncError(f"refusing to run: SUPABASE_DB_URL does not reference {EXPECTED_PROJECT_REF}")
    return url


UPSERT = f"""
insert into authority.placement ({", ".join(SHEET_OWNED)})
values ({", ".join(["%s"] * len(SHEET_OWNED))})
on conflict (placement_id) do update set
    {", ".join(f"{c} = excluded.{c}" for c in SHEET_OWNED[1:])},
    updated_at = now()
where ({", ".join(f"placement.{c}" for c in SHEET_OWNED[1:])})
      is distinct from
      ({", ".join(f"excluded.{c}" for c in SHEET_OWNED[1:])})
returning placement_id, (xmax = 0) as inserted
"""


def upsert(conn, rows: list[dict]) -> tuple[int, int]:
    inserted = updated = 0
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(UPSERT, [r[c] for c in SHEET_OWNED])
            hit = cur.fetchone()
            if hit is None:
                continue  # unchanged
            inserted += hit[1]
            updated += not hit[1]
    return inserted, updated


def table_view(conn, ids: list[str]) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(
            f"select placement_id::text, {', '.join(TABLE_OWNED)} "
            "from authority.placement where placement_id = any(%s::uuid[])",
            (ids,),
        )
        return {r[0]: dict(zip(TABLE_OWNED, r[1:])) for r in cur.fetchall()}


def orphans(conn, domain_ids: set[int], ids: list[str]) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "select placement_id::text, site, status from authority.placement "
            "where domain_id = any(%s) and not (placement_id = any(%s::uuid[]))",
            (list(domain_ids), ids),
        )
        return cur.fetchall()


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, dt.datetime):
        return v.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return str(v)


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_back(session, header: list[str], parsed: Parsed, view: dict[str, dict]) -> int:
    """Write new uuids and the table-owned columns back into the sheet."""
    data = []
    pid_col = _col_letter(header.index("placement_id"))
    for n, pid in parsed.new_ids.items():
        data.append({"range": f"{TAB}!{pid_col}{n}", "values": [[pid]]})
    for row, n in zip(parsed.rows, parsed.sheet_rows):
        got = view.get(row["placement_id"], {})
        for c in TABLE_OWNED:
            data.append({"range": f"{TAB}!{_col_letter(header.index(c))}{n}",
                         "values": [[_fmt(got.get(c))]]})
    if data:
        r = session.post(f"{SHEETS}/{SPREADSHEET_ID}/values:batchUpdate",
                         json={"valueInputOption": "RAW", "data": data})
        r.raise_for_status()
    return len(data)


def sql_literal(v) -> str:
    """A value as a Postgres literal, for --emit-sql. Only the types parse_sheet makes."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, Decimal)):
        return str(v)
    if isinstance(v, dt.date):
        return f"'{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def emit_sql(rows: list[dict]) -> str:
    """The same upsert as a script for the Supabase SQL Editor (no login needed here)."""
    head, tail = UPSERT.split("values (", 1)
    tail = tail.split(")", 1)[1].split("returning")[0]
    values = ",\n".join("  (" + ", ".join(sql_literal(r[c]) for c in SHEET_OWNED) + ")" for r in rows)
    return (
        "-- Generated by scripts/sync_authority_placement.py --emit-sql. Safe to re-run:\n"
        "-- unchanged rows are left alone; changed rows are updated; new rows inserted.\n"
        "begin;\n" + head.strip() + "\nvalues\n" + values + tail.rstrip() + ";\n"
        "select count(*) as rows, count(*) filter (where quality) as quality_rows,\n"
        "       count(*) filter (where counted) as counted_rows,\n"
        "       max(updated_at) as last_change\n"
        "  from authority.placement where domain_id in ("
        + ", ".join(sorted({str(r["domain_id"]) for r in rows})) + ");\n"
        "commit;\n"
    )


def google_session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return AuthorizedSession(creds)


def read_sheet(session) -> list[list]:
    r = session.get(f"{SHEETS}/{SPREADSHEET_ID}/values/{TAB}",
                    params={"valueRenderOption": "FORMATTED_VALUE"})
    if r.status_code == 403:
        raise SyncError("Google refused the read. Run the gcloud login line in this file's "
                        "docstring (it adds the Sheets scope), then try again.")
    r.raise_for_status()
    return r.json().get("values", [])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="upsert inside a transaction, report, roll back; the sheet is not touched")
    ap.add_argument("--csv", metavar="PATH",
                    help="read a CSV download of the sheet instead of the Sheets API (no write-back)")
    ap.add_argument("--emit-sql", metavar="OUT",
                    help="with --csv: write the upsert as a SQL file for the SQL Editor; no database login")
    ap.add_argument("--local-test", action="store_true",
                    help="allow a localhost SUPABASE_DB_URL (testing only)")
    args = ap.parse_args(argv)

    if args.emit_sql and not args.csv:
        raise SyncError("--emit-sql needs --csv")
    url = None if args.emit_sql else database_url(args.local_test)
    if args.csv:
        import csv
        import pathlib

        session = None
        with pathlib.Path(args.csv).expanduser().open(newline="", encoding="utf-8-sig") as f:
            values = list(csv.reader(f))
    else:
        session = google_session()
        values = read_sheet(session)
    parsed = parse_sheet(values)
    if args.csv and parsed.new_ids:
        parsed.errors += [f"row {n}, placement_id: blank. With --csv nothing is written back, so give "
                          f"the row an id first (e.g. {pid})" for n, pid in parsed.new_ids.items()]
    if parsed.errors:
        print(f"Nothing written. {len(parsed.errors)} problem(s) in the sheet:")
        for e in parsed.errors:
            print("  -", e)
        return 1

    if args.emit_sql:
        import pathlib

        out = pathlib.Path(args.emit_sql).expanduser()
        out.write_text(emit_sql(parsed.rows), encoding="utf-8")
        print(f"wrote {len(parsed.rows)} rows as SQL to {out}. Paste it into the SQL Editor and Run.")
        return 0

    import psycopg

    ids = [r["placement_id"] for r in parsed.rows]
    with psycopg.connect(url, autocommit=False) as conn:
        inserted, updated = upsert(conn, parsed.rows)
        unchanged = len(parsed.rows) - inserted - updated
        gone = orphans(conn, {r["domain_id"] for r in parsed.rows}, ids)
        view = table_view(conn, ids)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

    mode = "DRY RUN, rolled back" if args.dry_run else "SYNCED"
    print(f"{mode}: {len(parsed.rows)} sheet rows -> {inserted} new, {updated} changed, "
          f"{unchanged} unchanged; {len(parsed.new_ids)} new id(s) given.")
    print(f"quality: {sum(1 for v in view.values() if v['quality'])}; "
          f"counted toward the target: {sum(1 for v in view.values() if v['counted'])}")
    if gone:
        print(f"{len(gone)} row(s) are in the table but no longer in the sheet (left as they are):")
        for g in gone:
            print("  -", *g)

    if args.csv:
        print("CSV mode: nothing written back to the sheet.")
    elif not args.dry_run:
        cells = write_back(session, values[0], parsed, view)
        print(f"wrote {cells} cell(s) back into the sheet")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
