"""Generic batch runner and cost estimator for DataForSEO Backlinks benchmarking pulls.

Coordinates client and rival benchmarking pulls across the five Backlinks
endpoints without duplicating DataForSEO API spend on rivals shared by multiple
clients:

  Domain endpoints (`subject` = `'client'` or `'rival'`, stamped with the parent
  client's `domain_id` and `domain`):
    - backlinks_summary
    - backlinks_history
    - backlinks_referring_domains
    - backlinks_anchors

  URL endpoint (client sitemap URLs only, `subject` = `'client'`):
    - backlinks_bulk_pages_summary
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from skyward.data.dataforseo.run import DEFAULT_BALANCE_BUFFER, check_balance

DEFAULT_DATE_FROM = "2026-01-01"

DOMAIN_ENDPOINTS: tuple[str, ...] = (
    "backlinks_summary",
    "backlinks_history",
    "backlinks_referring_domains",
    "backlinks_anchors",
)
URL_ENDPOINTS: tuple[str, ...] = ("backlinks_bulk_pages_summary",)
ALL_ENDPOINTS: tuple[str, ...] = (*DOMAIN_ENDPOINTS, *URL_ENDPOINTS)

_EP_KWARGS: dict[str, Callable[[str, int | None], dict[str, Any]]] = {
    "backlinks_summary": lambda _date_from, _limit: {},
    "backlinks_history": lambda date_from, _limit: {"date_from": date_from},
    "backlinks_referring_domains": (
        lambda _date_from, limit: {"limit": limit} if limit is not None else {}
    ),
    "backlinks_anchors": (
        lambda _date_from, limit: {"limit": limit} if limit is not None else {}
    ),
    "backlinks_bulk_pages_summary": lambda _date_from, _limit: {},
}

_STAMPED_UPLOAD_COLS = ("job_id", "upload_id", "ingest_timestamp")


@dataclass(frozen=True)
class DomainWorkUnit:
    """One domain-level pull (or clone upload of an earlier pull) under a client."""

    endpoint: str
    target: str
    client_domain_id: int
    client_domain: str
    subject: str
    clone_of_client_id: int | None = None


@dataclass(frozen=True)
class BulkUrlWorkUnit:
    """One client's target URL batch for backlinks/bulk_pages_summary/live."""

    client_domain_id: int
    client_domain: str
    urls: tuple[str, ...]


COMPETITOR_PRIORITY_RANK: dict[str, int] = {
    "VERY HIGH": 1,
    "HIGH": 2,
    "NORMAL": 3,
    "LOW": 4,
}


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load a target manifest JSON containing a non-empty 'clients' list."""
    with Path(manifest_path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not data.get("clients"):
        raise ValueError(f"Manifest {manifest_path} missing non-empty 'clients' list")
    return data


def load_manifest_from_supabase(
    meta_client: Any,
    *,
    domain_ids: tuple[int, ...] = (),
    client_ids: tuple[int, ...] = (),
    top_n_rivals: int | None = 3,
    min_priority: str | None = None,
    sitemap_urls_by_domain: dict[int | str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build a backlinks work plan manifest dynamically from Supabase `meta`.

    Queries `meta.site` to resolve client sites and `meta.site_competitors`
    via `meta_client.get_site_competitors(domain_id)` to resolve rivals.

    Args:
        meta_client: Instance of `skyward.data.meta.MetaClient` (or compatible duck-typed object).
        domain_ids: Optional specific client domain_ids to pull (defaults to all active client sites).
        client_ids: Optional specific client_ids to pull if domain_ids not specified.
        top_n_rivals: Maximum rivals per client (ordered by priority), or None for all rivals.
        min_priority: Optional filter for minimum priority ('VERY HIGH', 'HIGH', etc.).
        sitemap_urls_by_domain: Optional mapping of domain_id or domain name to sitemap URLs.

    Returns:
        Manifest dictionary matching the structure expected by `filter_clients`
        and `build_work_plan` ({"version": "supabase", "clients": [...]}).
    """
    sitemap_map = sitemap_urls_by_domain or {}
    raw_sites: list[dict[str, Any]] = []

    if domain_ids:
        for did in domain_ids:
            site = None
            if hasattr(meta_client, "get_site"):
                site = meta_client.get_site(int(did))
            if site is None and hasattr(meta_client, "get_domain_by_id"):
                site = meta_client.get_domain_by_id(int(did))
            if site is None:
                raise ValueError(f"Domain ID {did} not found in meta")
            raw_sites.append(site)
    elif client_ids:
        if hasattr(meta_client, "list_sites"):
            for cid in client_ids:
                df = meta_client.list_sites(client_id=int(cid))
                if not df.empty:
                    raw_sites.extend(df.to_dict(orient="records"))
        else:
            raise AttributeError("meta_client does not implement list_sites")
    else:
        if hasattr(meta_client, "list_sites"):
            df = meta_client.list_sites(engagement_status="client")
            if df.empty:
                df = meta_client.list_sites()
            raw_sites = df.to_dict(orient="records")
        else:
            raise AttributeError("meta_client does not implement list_sites")

    clients: list[dict[str, Any]] = []
    max_rank = (
        COMPETITOR_PRIORITY_RANK.get(min_priority.upper(), 99)
        if min_priority
        else 99
    )

    for site in raw_sites:
        did = int(site["domain_id"])
        domain = str(site["domain"])

        rivals: list[dict[str, Any]] = []
        if hasattr(meta_client, "get_site_competitors"):
            comp_df = meta_client.get_site_competitors(did)
            if comp_df is not None and not comp_df.empty:
                comp_df = comp_df.copy()
                comp_df["_rank"] = comp_df["priority"].apply(
                    lambda p: COMPETITOR_PRIORITY_RANK.get(str(p).upper(), 99)
                )
                if min_priority:
                    comp_df = comp_df[comp_df["_rank"] <= max_rank]
                comp_df = comp_df.sort_values(by=["_rank", "domain"])
                if top_n_rivals is not None and top_n_rivals > 0:
                    comp_df = comp_df.head(top_n_rivals)

                for _, row in comp_df.iterrows():
                    rivals.append({
                        "domain_id": int(row["competitor_domain_id"]),
                        "domain": str(row["domain"]),
                    })

        sitemap_urls = (
            sitemap_map.get(did)
            or sitemap_map.get(domain)
            or site.get("sitemap_urls")
            or []
        )

        clients.append({
            "domain_id": did,
            "domain": domain,
            "rivals": rivals,
            "sitemap_urls": list(sitemap_urls),
        })

    return {"version": "supabase", "clients": clients}


def filter_clients(
    manifest: dict[str, Any], domain_ids: tuple[int, ...] = ()
) -> list[dict[str, Any]]:
    """Return manifest client entries matching `domain_ids`, or all clients if empty."""
    clients = list(manifest["clients"])
    if not domain_ids:
        return clients
    requested = set(domain_ids)
    selected = [c for c in clients if c["domain_id"] in requested]
    found = {c["domain_id"] for c in selected}
    missing = sorted(requested - found)
    if missing:
        raise ValueError(f"Unknown client domain_id(s) not in manifest: {missing}")
    return selected


def resolve_endpoints(endpoints: tuple[str, ...] = ()) -> list[str]:
    """Validate and order requested Backlinks endpoint names."""
    if not endpoints:
        return list(ALL_ENDPOINTS)
    unknown = sorted(set(endpoints) - set(ALL_ENDPOINTS))
    if unknown:
        raise ValueError(f"Unsupported endpoint(s) {unknown}; valid: {list(ALL_ENDPOINTS)}")
    requested = set(endpoints)
    return [ep for ep in ALL_ENDPOINTS if ep in requested]


def build_work_plan(
    clients: list[dict[str, Any]],
    endpoints: list[str],
) -> tuple[list[DomainWorkUnit], list[BulkUrlWorkUnit]]:
    """Build domain and bulk-URL work units, deduplicating shared rivals across clients."""
    domain_units: list[DomainWorkUnit] = []
    bulk_units: list[BulkUrlWorkUnit] = []
    seen_primary: dict[tuple[str, str], int] = {}

    selected_domain_eps = [ep for ep in DOMAIN_ENDPOINTS if ep in endpoints]
    for ep_name in selected_domain_eps:
        for client in clients:
            cid = int(client["domain_id"])
            cdomain = str(client["domain"])
            assignments = [(cdomain, "client")] + [
                (str(rival["domain"]), "rival") for rival in client.get("rivals", [])
            ]
            for target, subject in assignments:
                key = (ep_name, target)
                primary_cid = seen_primary.get(key)
                domain_units.append(
                    DomainWorkUnit(
                        endpoint=ep_name,
                        target=target,
                        client_domain_id=cid,
                        client_domain=cdomain,
                        subject=subject,
                        clone_of_client_id=primary_cid,
                    )
                )
                if primary_cid is None:
                    seen_primary[key] = cid

    if "backlinks_bulk_pages_summary" in endpoints:
        for client in clients:
            urls = tuple(client.get("sitemap_urls") or ())
            if urls:
                bulk_units.append(
                    BulkUrlWorkUnit(
                        client_domain_id=int(client["domain_id"]),
                        client_domain=str(client["domain"]),
                        urls=urls,
                    )
                )

    return domain_units, bulk_units


def endpoint_kwargs(endpoint_name: str, *, date_from: str, limit: int | None) -> dict[str, Any]:
    """Return endpoint-specific options shared by plan estimation and live execution."""
    return _EP_KWARGS[endpoint_name](date_from, limit)


def estimate_work_plan(
    client: Any,
    endpoints: list[str],
    domain_units: list[DomainWorkUnit],
    bulk_units: list[BulkUrlWorkUnit],
    *,
    date_from: str = DEFAULT_DATE_FROM,
    limit: int | None = None,
) -> dict[str, Any]:
    """Compute per-endpoint and total RunPlan/CostEstimate figures for primary fetches."""
    per_endpoint: list[dict[str, Any]] = []
    total_requests = 0
    total_items = 0
    total_max_rows = 0
    total_list_price = 0.0
    total_avg = 0.0
    total_max = 0.0

    for ep_name in endpoints:
        ep = getattr(client, ep_name)
        kwargs = endpoint_kwargs(ep_name, date_from=date_from, limit=limit)

        if ep_name == "backlinks_bulk_pages_summary":
            estimates = [ep.estimate_cost(list(bu.urls), **kwargs) for bu in bulk_units]
            fetches = len(bulk_units)
            clones = 0
            unit_count = sum(len(bu.urls) for bu in bulk_units)
        else:
            units = [u for u in domain_units if u.endpoint == ep_name]
            primary_targets = [u.target for u in units if u.clone_of_client_id is None]
            estimates = [ep.estimate_cost(primary_targets, **kwargs)] if primary_targets else []
            fetches = len(primary_targets)
            clones = len(units) - fetches
            unit_count = len(units)

        req = sum(est.plan.planned_requests for est in estimates)
        items = sum(est.plan.planned_items for est in estimates)
        max_rows = sum(est.plan.planned_max_rows for est in estimates)
        lp = round(sum(est.list_price_usd for est in estimates), 6)
        avg = round(sum(est.avg_usd for est in estimates), 6)
        mx = round(sum(est.max_usd for est in estimates), 6)
        basis = estimates[0].basis if estimates else "none"

        per_endpoint.append({
            "endpoint": ep_name,
            "units": unit_count,
            "primary_fetches": fetches,
            "reused_clones": clones,
            "requests": req,
            "items": items,
            "max_rows": max_rows,
            "list_price_usd": lp,
            "avg_usd": avg,
            "max_usd": mx,
            "basis": basis,
        })
        total_requests += req
        total_items += items
        total_max_rows += max_rows
        total_list_price = round(total_list_price + lp, 6)
        total_avg = round(total_avg + avg, 6)
        total_max = round(total_max + mx, 6)

    return {
        "per_endpoint": per_endpoint,
        "total_requests": total_requests,
        "total_items": total_items,
        "total_max_rows": total_max_rows,
        "total_list_price_usd": total_list_price,
        "total_avg_usd": total_avg,
        "total_max_usd": total_max,
    }


def clone_rows_for_client(
    source_df: pd.DataFrame,
    *,
    client_domain_id: int,
    client_domain: str,
    subject: str,
) -> pd.DataFrame:
    """Re-stamp fetched rows for another client's ownership before secondary BQ upload."""
    cloned = source_df.drop(columns=list(_STAMPED_UPLOAD_COLS), errors="ignore").copy()
    cloned["domain_id"] = client_domain_id
    cloned["domain"] = client_domain
    cloned["subject"] = subject
    return cloned


def _resolve_meta_domain(client: Any, domain_id: int, fallback: str) -> str:
    meta = getattr(client, "meta_client", None)
    if meta is not None:
        found = meta.get_domain_by_id(int(domain_id))
        if found and found.get("domain"):
            return str(found["domain"])
    return fallback


def execute_backlinks_batch(
    client: Any,
    clients: list[dict[str, Any]],
    endpoints: list[str],
    *,
    job_id: str | None = None,
    date_from: str = DEFAULT_DATE_FROM,
    limit: int | None = None,
    upload: bool = True,
    balance_buffer: float = DEFAULT_BALANCE_BUFFER,
    ignore_balance_check: bool = False,
) -> dict[str, Any]:
    """Execute scheduled Backlinks pulls, reusing primary fetches for shared rivals."""
    if upload and getattr(client, "bq_client", None) is None:
        raise RuntimeError("BigQueryClient is required when upload=True.")

    resolved_job_id = job_id or str(uuid.uuid4())
    domain_units, bulk_units = build_work_plan(clients, endpoints)
    summary = estimate_work_plan(
        client, endpoints, domain_units, bulk_units, date_from=date_from, limit=limit
    )

    check_balance(
        client,
        required_usd=summary["total_max_usd"],
        balance_buffer=balance_buffer,
        ignore=ignore_balance_check,
        job_id=resolved_job_id,
        endpoint="backlinks_batch",
    )

    fetched_cache: dict[tuple[str, str], pd.DataFrame] = {}
    rows_written: dict[str, int] = {ep: 0 for ep in endpoints}
    reused_targets: list[dict[str, Any]] = []

    for unit in domain_units:
        ep = getattr(client, unit.endpoint)
        key = (unit.endpoint, unit.target)

        if unit.clone_of_client_id is None:
            kwargs = endpoint_kwargs(unit.endpoint, date_from=date_from, limit=limit)
            df = ep.live(
                unit.target,
                domain_id=unit.client_domain_id,
                job_id=resolved_job_id,
                upload=upload,
                subject=unit.subject,
                ignore_balance_check=ignore_balance_check,
                **kwargs,
            )
            fetched_cache[key] = df
            rows_written[unit.endpoint] += len(df)
            continue

        source_df = fetched_cache.get(key)
        if source_df is None or source_df.empty:
            continue
        target_domain = _resolve_meta_domain(
            client, unit.client_domain_id, unit.client_domain
        )
        cloned = clone_rows_for_client(
            source_df,
            client_domain_id=unit.client_domain_id,
            client_domain=target_domain,
            subject=unit.subject,
        )
        if upload:
            ok = ep.upload(client.bq_client, cloned, job_id=resolved_job_id)
            if not ok:
                raise RuntimeError(
                    f"Clone upload failed for endpoint={unit.endpoint} target={unit.target} "
                    f"client_domain_id={unit.client_domain_id}"
                )
        rows_written[unit.endpoint] += len(cloned)
        reused_targets.append({
            "endpoint": unit.endpoint,
            "target": unit.target,
            "primary_client_domain_id": unit.clone_of_client_id,
            "cloned_client_domain_id": unit.client_domain_id,
            "rows": len(cloned),
        })

    if "backlinks_bulk_pages_summary" in endpoints and bulk_units:
        bulk_ep = client.backlinks_bulk_pages_summary
        for bu in bulk_units:
            df = asyncio.run(
                bulk_ep.live_all(
                    list(bu.urls),
                    domain_id=bu.client_domain_id,
                    job_id=resolved_job_id,
                    upload=upload,
                    subject="client",
                    ignore_balance_check=ignore_balance_check,
                    ignore_location_check=True,
                )
            )
            rows_written["backlinks_bulk_pages_summary"] += len(df)

    return {
        "job_id": resolved_job_id,
        "clients": len(clients),
        "domain_units": len(domain_units),
        "bulk_url_batches": len(bulk_units),
        "rows_written": rows_written,
        "reused_targets": reused_targets,
        "estimate": summary,
    }


def format_summary_lines(
    manifest: dict[str, Any],
    clients: list[dict[str, Any]],
    domain_units: list[DomainWorkUnit],
    bulk_units: list[BulkUrlWorkUnit],
    summary: dict[str, Any],
    *,
    date_from: str,
    limit: int | None,
) -> list[str]:
    """Render human-readable dry-run lines summarizing fetches, clones, and costs."""
    clones = sorted({u.target for u in domain_units if u.clone_of_client_id is not None})
    lines = [
        f"Manifest version: {manifest.get('version', 'unknown')}",
        f"Clients selected: {len(clients)} ({[c['domain'] for c in clients]})",
        f"Shared rivals fetched once & cloned: {clones or 'none'}",
        f"Sitemap batches: {len(bulk_units)} clients, {sum(len(b.urls) for b in bulk_units)} URLs",
        f"Options: date_from={date_from}, limit={limit if limit is not None else 'all (20000 DFS cap, early-stop on total_count)'}",
        "",
    ]
    for row in summary["per_endpoint"]:
        lines.append(
            f"  {row['endpoint']}: fetches={row['primary_fetches']} "
            f"(+{row['reused_clones']} reused), reqs={row['requests']}, "
            f"items={row['items']}, list=${row['list_price_usd']:.4f}, "
            f"avg=${row['avg_usd']:.4f}, max=${row['max_usd']:.4f} [{row['basis']}]"
        )
    lines.extend([
        "",
        f"TOTAL worst-case plan: {summary['total_requests']} requests, "
        f"{summary['total_items']} items, list=${summary['total_list_price_usd']:.4f}, "
        f"avg=${summary['total_avg_usd']:.4f}, max=${summary['total_max_usd']:.4f}",
    ])
    return lines

