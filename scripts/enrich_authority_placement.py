"""Fill spam_score and site_us_traffic on authority.placement from DataForSEO.

quality (a generated column) then follows: live AND dofollow AND
spam_score < 40 AND site_us_traffic >= 1000.

Two bulk calls cover up to 1,000 linking sites each:
    backlinks/bulk_spam_score/live                  -> spam_score (0-100)
    dataforseo_labs/google/bulk_traffic_estimation  -> organic etv, United
        States (2840), English                      -> site_us_traffic

PAID. Without --approve the script only prints the sites it would send and a
cost estimate, and makes no paid call. The free balance read happens in both
modes, so the estimate can be compared against what is left.

Checkpoints (--checkpoint, default first). Every reading is kept, dated, in
authority.placement_reading (one row per link per checkpoint):
    first  rows with a site and no metrics yet (all of them with --refresh).
           Also written onto authority.placement, where quality reads it. The
           spec wants it "at went_live"; a link that predates the table gets
           its first read instead, and metrics_read_at records that date.
    30d    live rows whose went_live is at least 30 days ago and that have
    90d    no reading at that checkpoint yet. Kept in the history only.

USAGE
    SUPABASE_DB_URL=... DATAFORSEO_API_LOGIN=... DATAFORSEO_API_PASSWORD=... \\
        uv run python scripts/enrich_authority_placement.py            # estimate only
    ... uv run python scripts/enrich_authority_placement.py --approve  # spend
Then run scripts/sync_authority_placement.py to show the values in the sheet.

Without a database login (SQL Editor workflow):
    DATAFORSEO_API_LOGIN=... DATAFORSEO_API_PASSWORD=... uv run python \
        scripts/enrich_authority_placement.py --csv sheet.csv --emit-sql out.sql [--approve]
reads the sites from a CSV download of the sheet and writes the updates as SQL.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sync_authority_placement import SyncError, database_url  # noqa: E402

# Repo rate card (src/skyward/data/dataforseo/cost_rates.py, list prices
# verified 2026-09-15): backlinks family and Labs family, plus its 10% buffer.
RATES = {
    "backlinks/bulk_spam_score/live": (0.024, 0.000036),
    "dataforseo_labs/google/bulk_traffic_estimation/live": (0.012, 0.00012),
}
BUFFER = 0.10
BASE = "https://api.dataforseo.com/v3/"


def estimate(n_sites: int) -> tuple[float, float]:
    cost = sum(per_req + per_item * n_sites for per_req, per_item in RATES.values())
    return cost, cost * (1 + BUFFER)


DAYS = {"30d": 30, "90d": 90}


def pick_rows(conn, checkpoint: str, refresh: bool) -> list[tuple[str, str]]:
    """(placement_id, site) pairs due a reading at this checkpoint."""
    with conn.cursor() as cur:
        if checkpoint == "first":
            cur.execute(
                "select placement_id::text, site from authority.placement where site is not null"
                + ("" if refresh else " and (spam_score is null or site_us_traffic is null)")
                + " order by site")
        else:
            cur.execute(
                "select p.placement_id::text, p.site from authority.placement p "
                "where p.site is not null and p.status = 'live' and p.went_live is not null "
                "and p.went_live + %s <= current_date and not exists (select 1 from "
                "authority.placement_reading r where r.placement_id = p.placement_id "
                "and r.checkpoint = %s) order by p.site", (DAYS[checkpoint], checkpoint))
        return cur.fetchall()


def due_from_csv(values: list[list], parsed, checkpoint: str, refresh: bool,
                 today: dt.date) -> list[tuple[str, str]]:
    """The same choice made from a CSV download of the sheet.

    For 30d/90d the CSV can't say whether a reading exists, so the SQL skips
    links that already have one (on conflict do nothing).
    """
    header = [h.strip() for h in values[0]]
    read_col = header.index("metrics_read_at") if "metrics_read_at" in header else None
    out = []
    for r, n in zip(parsed.rows, parsed.sheet_rows):
        if not r["site"]:
            continue
        if checkpoint == "first":
            raw = values[n - 1]
            already = read_col is not None and read_col < len(raw) and raw[read_col].strip()
            if refresh or not already:
                out.append((r["placement_id"], r["site"]))
        elif (r["status"] == "live" and r["went_live"]
              and r["went_live"] + dt.timedelta(days=DAYS[checkpoint]) <= today):
            out.append((r["placement_id"], r["site"]))
    return out


def reading_sql(pid: str, checkpoint: str, today: str, spam, traffic, refresh: bool) -> str:
    from sync_authority_placement import sql_literal as q

    conflict = ("do update set read_at = excluded.read_at, spam_score = excluded.spam_score, "
                "site_us_traffic = excluded.site_us_traffic" if refresh else "do nothing")
    return (f"insert into authority.placement_reading (placement_id, checkpoint, read_at, spam_score, "
            f"site_us_traffic) values ({q(pid)}, {q(checkpoint)}, '{today}', {q(spam)}, {q(traffic)}) "
            f"on conflict (placement_id, checkpoint) {conflict};")


def _items(resp: dict, endpoint: str) -> tuple[list[dict], float]:
    task = (resp or {}).get("tasks", [{}])[0]
    if task.get("status_code") != 20000:
        raise SyncError(f"{endpoint}: {task.get('status_code')} {task.get('status_message')}")
    result = (task.get("result") or [{}])[0] or {}
    return result.get("items") or [], float(task.get("cost") or 0)


def fetch(client, sites: list[str]) -> tuple[dict, dict, float]:
    spam_items, c1 = _items(client.post(BASE + "backlinks/bulk_spam_score/live",
                                        [{"targets": sites}]), "spam score")
    traffic_items, c2 = _items(client.post(
        BASE + "dataforseo_labs/google/bulk_traffic_estimation/live",
        [{"targets": sites, "location_code": 2840, "language_code": "en",
          "item_types": ["organic"]}]), "traffic estimation")
    spam = {i["target"]: i.get("spam_score") for i in spam_items}
    traffic = {}
    for i in traffic_items:
        etv = ((i.get("metrics") or {}).get("organic") or {}).get("etv")
        traffic[i["target"]] = None if etv is None else int(round(etv))
    return spam, traffic, c1 + c2


def from_csv(client, args) -> int:
    """Sites from a CSV download; updates written as SQL instead of to the DB."""
    import csv
    import pathlib

    from sync_authority_placement import parse_sheet, sql_literal

    if not args.emit_sql:
        raise SyncError("--csv needs --emit-sql (there is no database connection in this mode)")
    with pathlib.Path(args.csv).expanduser().open(newline="", encoding="utf-8-sig") as f:
        values = list(csv.reader(f))
    parsed = parse_sheet(values)
    if parsed.errors:
        raise SyncError("the CSV has problems; run the sync on it first to see them")
    today = dt.date.today()
    due = due_from_csv(values, parsed, args.checkpoint, args.refresh, today)
    sites = sorted({site for _, site in due})
    if not sites:
        print(f"nothing due a {args.checkpoint} reading.")
        return 0
    cost, cap = estimate(len(sites))
    before = client.get_balance()["balance"]
    print(f"{len(sites)} site(s) to read: {', '.join(sites)}")
    print(f"estimated cost ${cost:.4f} (up to ${cap:.4f} with the 10% buffer), "
          f"2 calls. Balance now ${before:,.2f}.")
    if not args.approve:
        print("Estimate only. Nothing was spent. Re-run with --approve to spend.")
        return 0
    spam, traffic, billed = fetch(client, sites)
    day = today.isoformat()
    lines = [f"-- Generated by scripts/enrich_authority_placement.py ({args.checkpoint} reading). "
             "Safe to re-run.", "begin;"]
    for pid, s in due:
        if args.checkpoint == "first":
            lines.append(
                f"update authority.placement set spam_score = {sql_literal(spam.get(s))}, "
                f"site_us_traffic = {sql_literal(traffic.get(s))}, metrics_read_at = '{day}', "
                f"updated_at = now() where placement_id = {sql_literal(pid)}"
                + ("" if args.refresh else " and metrics_read_at is null") + ";")
        lines.append(reading_sql(pid, args.checkpoint, day, spam.get(s), traffic.get(s), args.refresh))
    lines += ["select count(*) as rows, count(*) filter (where quality) as quality_rows,",
              "       count(*) filter (where counted) as counted_rows,",
              "       (select count(*) from authority.placement_reading) as readings",
              "  from authority.placement;", "commit;"]
    out = pathlib.Path(args.emit_sql).expanduser()
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    after = client.get_balance()["balance"]
    missing = [s for s in sites if spam.get(s) is None or traffic.get(s) is None]
    print(f"done. DataForSEO billed ${billed:.4f}; balance ${before:,.2f} -> ${after:,.2f}.")
    print(f"wrote {len(sites)} updates to {out}. Paste it into the SQL Editor and Run.")
    if missing:
        print(f"no value returned for: {', '.join(missing)} (left null, so not counted)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--approve", action="store_true", help="make the paid calls")
    ap.add_argument("--refresh", action="store_true", help="re-read sites that already have metrics")
    ap.add_argument("--checkpoint", choices=["first", "30d", "90d"], default="first",
                    help="which reading this is (default first)")
    ap.add_argument("--csv", metavar="PATH", help="take the sites from a CSV download of the sheet (no DB login)")
    ap.add_argument("--emit-sql", metavar="OUT", help="with --csv: write the updates as SQL for the SQL Editor")
    args = ap.parse_args(argv)

    import psycopg
    from skyward.config import load_config
    from skyward.data.dataforseo import DataForSEOClient

    cfg = load_config()
    client = DataForSEOClient(cfg.dataforseo_username, cfg.dataforseo_password)

    if args.csv:
        return from_csv(client, args)

    with psycopg.connect(database_url(False), autocommit=False) as conn:
        due = pick_rows(conn, args.checkpoint, args.refresh)
        sites = sorted({site for _, site in due})
        cost, cap = estimate(len(sites))
        before = client.get_balance()["balance"]
        print(f"{len(sites)} site(s) to read: {', '.join(sites) or '(none)'}")
        print(f"estimated cost ${cost:.4f} (up to ${cap:.4f} with the 10% buffer), "
              f"2 calls. Balance now ${before:,.2f}.")
        if not sites:
            return 0
        if not args.approve:
            print("Estimate only. Nothing was spent. Re-run with --approve to spend.")
            return 0

        spam, traffic, billed = fetch(client, sites)
        today = dt.date.today()
        with conn.cursor() as cur:
            for pid, s in due:
                if args.checkpoint == "first":
                    cur.execute(
                        "update authority.placement set spam_score = %s, site_us_traffic = %s, "
                        "metrics_read_at = %s, updated_at = now() where placement_id = %s",
                        (spam.get(s), traffic.get(s), today, pid))
                cur.execute(
                    "insert into authority.placement_reading (placement_id, checkpoint, read_at, "
                    "spam_score, site_us_traffic) values (%s, %s, %s, %s, %s) on conflict "
                    "(placement_id, checkpoint) do update set read_at = excluded.read_at, "
                    "spam_score = excluded.spam_score, site_us_traffic = excluded.site_us_traffic",
                    (pid, args.checkpoint, today, spam.get(s), traffic.get(s)))
            cur.execute("select count(*) filter (where quality), count(*) filter (where counted), "
                        "count(*) from authority.placement")
            q, counted, total = cur.fetchone()
        conn.commit()

    after = client.get_balance()["balance"]
    missing = [s for s in sites if spam.get(s) is None or traffic.get(s) is None]
    print(f"done. DataForSEO billed ${billed:.4f}; balance ${before:,.2f} -> ${after:,.2f}.")
    print(f"quality rows now: {q} of {total}; counted toward the target: {counted}.")
    if missing:
        print(f"no value returned for: {', '.join(missing)} (left null, so not counted)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
