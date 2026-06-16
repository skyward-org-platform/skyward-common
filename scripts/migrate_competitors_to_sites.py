"""Additive backfill: client-level competitors -> meta.site_competitors.

Dry-run by default; pass --apply to write. Does NOT delete any rows or alter
any table. For each client-level competitor (client_domains.is_competitor=true),
inserts a site_competitors row from EACH of that client's brand sites
(client_domains.is_competitor=false) to the competitor domain.

  * Multi-brand-site clients: fans out to all brand sites (safe superset) and
    reports the fan-out for manual trimming.
  * Clients with no brand site: skipped and reported (nothing to attach to).
  * Self-rows (competitor == brand site) are skipped.

Re-runnable: inserts use ON CONFLICT DO NOTHING.

See docs/superpowers/specs/2026-06-15-site-competitors-design.md.
"""
import argparse
import sys

from skyward.config import load_config
from skyward.data.supabase import SupabaseClient


def plan_backfill(competitor_rows, brand_sites_by_client):
    """Pure planner.

    Args:
        competitor_rows: list of (client_id, competitor_domain_id, priority)
        brand_sites_by_client: {client_id: [brand_domain_id, ...]}

    Returns:
        (inserts, multi_site_review, zero_brand_review)
          inserts: list of (domain_id, competitor_domain_id, priority)
          multi_site_review: list of (client_id, competitor_domain_id, [brand_ids])
          zero_brand_review: list of (client_id, competitor_domain_id)
    """
    inserts = []
    multi_site_review = []
    zero_brand_review = []
    for client_id, competitor_domain_id, priority in competitor_rows:
        brand_ids = brand_sites_by_client.get(client_id, [])
        if not brand_ids:
            zero_brand_review.append((client_id, competitor_domain_id))
            continue
        targets = [b for b in brand_ids if b != competitor_domain_id]
        if not targets:
            continue
        for brand_id in targets:
            inserts.append((brand_id, competitor_domain_id, priority))
        if len(targets) > 1:
            multi_site_review.append((client_id, competitor_domain_id, targets))
    return inserts, multi_site_review, zero_brand_review


def _fetch_state(sb):
    df = sb.query(
        "SELECT client_id, domain_id, is_competitor, priority FROM meta.client_domains"
    )
    competitor_rows = []
    brand_sites_by_client = {}
    for _, r in df.iterrows():
        cid = int(r["client_id"])
        did = int(r["domain_id"])
        if r["is_competitor"]:
            competitor_rows.append((cid, did, r["priority"]))
        else:
            brand_sites_by_client.setdefault(cid, []).append(did)
    return competitor_rows, brand_sites_by_client


def _apply_inserts(sb, inserts):
    for domain_id, competitor_domain_id, priority in inserts:
        sb.execute(
            "INSERT INTO meta.site_competitors (domain_id, competitor_domain_id, priority) "
            "VALUES (%(d)s, %(c)s, %(p)s) "
            "ON CONFLICT (domain_id, competitor_domain_id) DO NOTHING",
            {"d": domain_id, "c": competitor_domain_id, "p": priority},
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backfill client competitors into site_competitors.")
    parser.add_argument("--apply", action="store_true", help="Write inserts (default: dry-run).")
    args = parser.parse_args(argv)

    cfg = load_config()
    sb = SupabaseClient(cfg.supabase_db_url)

    competitor_rows, brand_sites = _fetch_state(sb)
    inserts, multi_review, zero_review = plan_backfill(competitor_rows, brand_sites)

    print(f"Planned site_competitors inserts: {len(inserts)}")
    if multi_review:
        print("\n=== MULTI-SITE FAN-OUT (review/trim manually) ===")
        for client_id, comp, brand_ids in multi_review:
            print(f"  client {client_id}: competitor {comp} -> sites {brand_ids}")
    if zero_review:
        print("\n=== ZERO BRAND SITE (skipped, needs attention) ===")
        for client_id, comp in zero_review:
            print(f"  client {client_id}: competitor {comp} has no brand site to attach to")

    if not args.apply:
        print("\nDRY-RUN only. Re-run with --apply to write.")
        return

    _apply_inserts(sb, inserts)
    print(f"\nApplied {len(inserts)} inserts (ON CONFLICT DO NOTHING).")


if __name__ == "__main__":
    sys.exit(main())
