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

Which rows: those whose site is set and which have no metrics yet (or all of
them with --refresh). The spec wants the values "at went_live". A row that went
live before this table existed gets its first read instead, and
metrics_read_at records that date.

USAGE
    SUPABASE_DB_URL=... DATAFORSEO_API_LOGIN=... DATAFORSEO_API_PASSWORD=... \\
        uv run python scripts/enrich_authority_placement.py            # estimate only
    ... uv run python scripts/enrich_authority_placement.py --approve  # spend
Then run scripts/sync_authority_placement.py to show the values in the sheet.
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


def pick_sites(conn, refresh: bool) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "select distinct site from authority.placement where site is not null"
            + ("" if refresh else " and (spam_score is null or site_us_traffic is null)")
            + " order by site"
        )
        return [r[0] for r in cur.fetchall()]


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--approve", action="store_true", help="make the paid calls")
    ap.add_argument("--refresh", action="store_true", help="re-read sites that already have metrics")
    args = ap.parse_args(argv)

    import psycopg
    from skyward.config import load_config
    from skyward.data.dataforseo import DataForSEOClient

    cfg = load_config()
    client = DataForSEOClient(cfg.dataforseo_username, cfg.dataforseo_password)

    with psycopg.connect(database_url(False), autocommit=False) as conn:
        sites = pick_sites(conn, args.refresh)
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
            for s in sites:
                cur.execute(
                    "update authority.placement set spam_score = %s, site_us_traffic = %s, "
                    "metrics_read_at = %s, updated_at = now() where site = %s",
                    (spam.get(s), traffic.get(s), today, s))
            cur.execute("select count(*) filter (where quality), count(*) from authority.placement")
            q, total = cur.fetchone()
        conn.commit()

    after = client.get_balance()["balance"]
    missing = [s for s in sites if spam.get(s) is None or traffic.get(s) is None]
    print(f"done. DataForSEO billed ${billed:.4f}; balance ${before:,.2f} -> ${after:,.2f}.")
    print(f"quality rows now: {q} of {total}.")
    if missing:
        print(f"no value returned for: {', '.join(missing)} (left null, so not counted)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
