"""End-to-end live QA against the 11 migrated DataForSEO tables.

Three groups, designed to be run in order (or via `all`):

  domain   — domain-level endpoints: ranked_keywords, domain_rank_overview,
             backlinks-backlinks (domain form), backlinks-summary (domain form).
             Output of ranked_keywords feeds the keyword group.

  url      — URL-level endpoints: bulk_pages_summary, plus backlinks-backlinks
             and backlinks-summary in URL form. URLs pulled from the
             ScreamingFrog internal_all table for the configured SF job_id.

  keyword  — keyword-level endpoints: keyword_overview, search_intent,
             search_volume (live + post), serp-google-organic (live + post),
             keyword_suggestions, related_keywords. Seeds are top keywords
             from ranked_keywords (either the DataFrame from the `domain`
             step or pulled from BigQuery if running `keyword` standalone).

All runs use:
  - batch_size=5 (forces multiple batches / concurrent calls per endpoint)
  - upload=True (writes to the production tables)
  - One shared job_id per CLI invocation (stamp every row for traceability)

Usage:
  uv run python scripts/live_qa_migrated_tables.py all --yes
  uv run python scripts/live_qa_migrated_tables.py domain --yes
  uv run python scripts/live_qa_migrated_tables.py url --yes
  uv run python scripts/live_qa_migrated_tables.py keyword --yes
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import click
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


_QA_DOMAIN = "buscharter.com.au"
_SF_JOB_ID = "49731500-e40c-4f76-b73c-52896d357e41"
_BATCH_SIZE = 10
_NUM_KEYWORD_SEEDS = 5           # seeds for keyword_suggestions + related_keywords (one-per-call)
_MAX_KEYWORDS = 50               # cap the keyword pool fed into batch endpoints
_LIMIT_PER_DOMAIN = 10000        # max keywords ranked_keywords will pull for the domain
_RK_PAGE_SIZE = 10               # keywords per paginated API call (low to exercise pagination)
_SERP_DEPTH = 2                  # SERP items per keyword (Group 3 cost control)
_SUGGEST_LIMIT = 5               # keyword_suggestions rows per seed
_RELATED_DEPTH = 2               # related_keywords graph depth
_RELATED_LIMIT = 5               # related_keywords rows per seed per depth
_LOCATION_CODE = 2840            # United States
_LANGUAGE_CODE = "en"
_KEYWORDS_PICKLE = Path("/tmp/live_qa_keywords.pkl")


# ---------- helpers ----------

def _load_client_and_ids(project: str | None):
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    from skyward.data.dataforseo import ClientConfig, DataForSEOClient
    from skyward.data.meta import MetaClient

    cfg = load_config()
    resolved_project = project or cfg.datahub_project_id
    bq = BigQueryClient(project_id=resolved_project)
    meta = MetaClient(bq)
    dfs_cfg = ClientConfig(debug=True)
    client = DataForSEOClient(
        username=cfg.dataforseo_username,
        password=cfg.dataforseo_password,
        config=dfs_cfg,
        bq_client=bq,
    )

    row = meta.get_domain(_QA_DOMAIN)
    if not row:
        raise click.ClickException(f"{_QA_DOMAIN!r} not found in Meta.domains")
    domain_id = row["domain_id"]

    return client, bq, resolved_project, domain_id


def _pull_sf_urls(bq, project: str) -> list[str]:
    sql = f"""
    SELECT Address
    FROM `{project}.ScreamingFrog.internal_all`
    WHERE job_id = '{_SF_JOB_ID}'
      AND LOWER(CAST(Content_Type AS STRING)) LIKE '%html%'
      AND CAST(Status_Code AS STRING) = '200'
    ORDER BY Address
    """
    rows = list(bq.client.query(sql).result())
    return [r.Address for r in rows if r.Address]


def _all_keywords_from_df(df: pd.DataFrame) -> list[str]:
    if df is None or df.empty:
        return []
    if "search_volume" in df.columns:
        df = df.sort_values("search_volume", ascending=False, na_position="last")
    return df["keyword"].dropna().astype(str).drop_duplicates().tolist()


def _all_keywords_from_bq(bq, project: str, domain: str, job_id: str | None = None) -> list[str]:
    """Fall back for standalone `keyword` step — pull all keywords for the domain from BQ."""
    job_filter = f"AND job_id = '{job_id}'" if job_id else ""
    sql = f"""
    SELECT DISTINCT keyword, ANY_VALUE(search_volume) AS search_volume
    FROM `{project}.DataForSEO.dataforseo_labs-google-ranked_keywords`
    WHERE domain = '{domain}' {job_filter}
    GROUP BY keyword
    ORDER BY search_volume DESC NULLS LAST
    """
    rows = list(bq.client.query(sql).result())
    return [r.keyword for r in rows if r.keyword]


def _section(title: str):
    click.echo(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------- groups ----------

async def _run_domain_group(client, domain_id: int, job_id: str) -> pd.DataFrame | None:
    _section(f"GROUP 1: DOMAIN-LEVEL ({_QA_DOMAIN})")
    targets = [_QA_DOMAIN]

    click.echo("\n[1/4] ranked_keywords.live_all …")
    rk_df = await client.dataforseo_labs_google_ranked_keywords.live_all(
        targets=targets,
        domain_id=domain_id,
        job_id=job_id,
        location_code=_LOCATION_CODE,
        language_code=_LANGUAGE_CODE,
        limit_per_domain=_LIMIT_PER_DOMAIN,
        page_size=_RK_PAGE_SIZE,
        upload=True,
    )
    click.echo(f"  → {len(rk_df)} rows")

    click.echo("\n[2/4] domain_rank_overview.live (single call) …")
    dro_df = client.dataforseo_labs_google_domain_rank_overview.live(
        target=_QA_DOMAIN,
        domain_id=domain_id,
        job_id=job_id,
        location_code=_LOCATION_CODE,
        language_code=_LANGUAGE_CODE,
        upload=True,
    )
    click.echo(f"  → {len(dro_df)} rows")

    click.echo("\n[3/4] backlinks-backlinks.live_all (domain form, batch_size=5) …")
    bb_df = await client.backlinks_backlinks.live_all(
        targets=targets,
        domain_id=domain_id,
        job_id=job_id,
        batch_size=_BATCH_SIZE,
        upload=True,
    )
    click.echo(f"  → {len(bb_df)} rows")

    click.echo("\n[4/4] backlinks-summary.live_all (domain form, batch_size=5) …")
    bs_df = await client.backlinks_summary.live_all(
        targets=targets,
        domain_id=domain_id,
        job_id=job_id,
        batch_size=_BATCH_SIZE,
        upload=True,
    )
    click.echo(f"  → {len(bs_df)} rows")

    return rk_df


async def _run_url_bulk_pages_summary(client, bq, project: str, domain_id: int, job_id: str) -> None:
    _section(f"GROUP 2a: URL-LEVEL — bulk_pages_summary ({_QA_DOMAIN})")
    urls = _pull_sf_urls(bq, project)
    click.echo(f"Pulled {len(urls)} HTML-200 URLs from SF")

    click.echo("\nbacklinks-bulk_pages_summary.live_all (batch_size=50) …")
    bps_df = await client.backlinks_bulk_pages_summary.live_all(
        targets=urls,
        domain_id=domain_id,
        job_id=job_id,
        batch_size=50,
        upload=True,
    )
    click.echo(f"  → {len(bps_df)} rows ({len(urls)} URLs / 50 per batch = {-(-len(urls)//50)} batches)")


async def _run_url_backlinks_summary(client, bq, project: str, domain_id: int, job_id: str) -> None:
    _section(f"GROUP 2b: URL-LEVEL — backlinks-summary ({_QA_DOMAIN})")
    urls = _pull_sf_urls(bq, project)
    click.echo(f"Pulled {len(urls)} HTML-200 URLs from SF — running ALL URLs (batch_size=100 concurrency)")

    bs_url_df = await client.backlinks_summary.live_all(
        targets=urls,
        domain_id=domain_id,
        job_id=job_id,
        batch_size=100,
        upload=True,
    )
    click.echo(f"  → {len(bs_url_df)} rows")


async def _run_url_backlinks_backlinks(client, bq, project: str, domain_id: int, job_id: str) -> None:
    _section(f"GROUP 2c: URL-LEVEL — backlinks-backlinks ({_QA_DOMAIN})")
    urls = _pull_sf_urls(bq, project)
    click.echo(f"Pulled {len(urls)} HTML-200 URLs from SF — running ALL URLs (batch_size=100 concurrency, limit=10 per URL)")

    bb_url_df = await client.backlinks_backlinks.live_all(
        targets=urls,
        domain_id=domain_id,
        job_id=job_id,
        batch_size=100,
        limit=10,
        upload=True,
    )
    click.echo(f"  → {len(bb_url_df)} rows")


_KEYWORD_ENDPOINT_KEYS = [
    "overview", "intent", "volume-live", "volume-post",
    "serp-live", "serp-post", "suggestions", "related",
]


async def _kw_overview(client, domain_id, job_id, keywords, seeds):
    click.echo(f"\n[overview] keyword_overview.live_all (batch_size={_BATCH_SIZE}) …")
    df = await client.dataforseo_labs_google_keyword_overview.live_all(
        keywords=keywords, domain_id=domain_id, job_id=job_id,
        location_code=_LOCATION_CODE, language_code=_LANGUAGE_CODE,
        batch_size=_BATCH_SIZE, upload=True,
    )
    click.echo(f"  → {len(df)} rows")


async def _kw_intent(client, domain_id, job_id, keywords, seeds):
    click.echo(f"\n[intent] search_intent.live_all (batch_size={_BATCH_SIZE}) …")
    df = await client.dataforseo_labs_google_search_intent.live_all(
        keywords=keywords, domain_id=domain_id, job_id=job_id,
        language_code=_LANGUAGE_CODE, batch_size=_BATCH_SIZE, upload=True,
    )
    click.echo(f"  → {len(df)} rows")


async def _kw_volume_live(client, domain_id, job_id, keywords, seeds):
    click.echo(f"\n[volume-live] search_volume.live_all (batch_size={_BATCH_SIZE}) …")
    df = await client.keywords_data_google_ads_search_volume.live_all(
        targets=keywords, domain_id=domain_id, job_id=job_id,
        location_code=_LOCATION_CODE, language_code=_LANGUAGE_CODE,
        batch_size=_BATCH_SIZE, upload=True,
    )
    click.echo(f"  → {len(df)} rows")


async def _kw_volume_post(client, domain_id, job_id, keywords, seeds):
    click.echo(f"\n[volume-post] search_volume.post_all (STANDARD, batch_size={_BATCH_SIZE}) …")
    df = await client.keywords_data_google_ads_search_volume.post_all(
        targets=keywords, domain_id=domain_id, job_id=job_id,
        location_code=_LOCATION_CODE, language_code=_LANGUAGE_CODE,
        batch_size=_BATCH_SIZE, upload=True,
    )
    click.echo(f"  → {len(df)} rows")


async def _kw_serp_live(client, domain_id, job_id, keywords, seeds):
    click.echo(f"\n[serp-live] serp-google-organic.live_all (batch_size={_BATCH_SIZE}, depth={_SERP_DEPTH}) …")
    df = await client.serp_google_organic.live_all(
        targets=keywords, domain_id=domain_id, job_id=job_id,
        location_code=_LOCATION_CODE, language_code=_LANGUAGE_CODE,
        batch_size=_BATCH_SIZE, depth=_SERP_DEPTH, upload=True,
    )
    click.echo(f"  → {len(df)} rows")


async def _kw_serp_post(client, domain_id, job_id, keywords, seeds):
    click.echo(f"\n[serp-post] serp-google-organic.post_all (STANDARD, batch_size={_BATCH_SIZE}, depth={_SERP_DEPTH}) …")
    result = await client.serp_google_organic.post_all(
        targets=keywords, domain_id=domain_id, job_id=job_id,
        location_code=_LOCATION_CODE, language_code=_LANGUAGE_CODE,
        batch_size=_BATCH_SIZE, depth=_SERP_DEPTH, upload=True,
    )
    # post_all returns (results_df, failed_df)
    if isinstance(result, tuple):
        results_df, failed_df = result
        click.echo(f"  → {len(results_df)} rows; failed: {len(failed_df)}")
    else:
        click.echo(f"  → {len(result)} rows")


async def _kw_suggestions(client, domain_id, job_id, keywords, seeds):
    click.echo(f"\n[suggestions] keyword_suggestions.live_all ({len(seeds)} seeds, limit={_SUGGEST_LIMIT}/seed) …")
    df = await client.dataforseo_labs_google_keyword_suggestions.live_all(
        targets=seeds, domain_id=domain_id, job_id=job_id,
        location_code=_LOCATION_CODE, language_code=_LANGUAGE_CODE,
        batch_size=_NUM_KEYWORD_SEEDS, limit=_SUGGEST_LIMIT, upload=True,
    )
    click.echo(f"  → {len(df)} rows")


async def _kw_related(client, domain_id, job_id, keywords, seeds):
    click.echo(f"\n[related] related_keywords.live_all ({len(seeds)} seeds, depth={_RELATED_DEPTH}, limit={_RELATED_LIMIT}/seed) …")
    df = await client.dataforseo_labs_google_related_keywords.live_all(
        targets=seeds, domain_id=domain_id, job_id=job_id,
        location_code=_LOCATION_CODE, language_code=_LANGUAGE_CODE,
        batch_size=_NUM_KEYWORD_SEEDS, depth=_RELATED_DEPTH, limit=_RELATED_LIMIT, upload=True,
    )
    click.echo(f"  → {len(df)} rows")


_KW_DISPATCH = {
    "overview": _kw_overview,
    "intent": _kw_intent,
    "volume-live": _kw_volume_live,
    "volume-post": _kw_volume_post,
    "serp-live": _kw_serp_live,
    "serp-post": _kw_serp_post,
    "suggestions": _kw_suggestions,
    "related": _kw_related,
}


async def _run_keyword_group(client, bq, project: str, domain_id: int, job_id: str, rk_df: pd.DataFrame | None, only: list[str] | None = None) -> None:
    _section(f"GROUP 3: KEYWORD-LEVEL ({_QA_DOMAIN})")

    if rk_df is not None and not rk_df.empty:
        keywords = _all_keywords_from_df(rk_df)
        click.echo(f"Keywords from in-memory ranked_keywords DataFrame: {len(keywords)}")
    else:
        keywords = _all_keywords_from_bq(bq, project, _QA_DOMAIN, job_id)
        if not keywords:
            keywords = _all_keywords_from_bq(bq, project, _QA_DOMAIN)
        click.echo(f"Keywords from BQ ranked_keywords: {len(keywords)}")

    if not keywords:
        raise click.ClickException("No seed keywords available — run the `domain` group first.")

    if len(keywords) > _MAX_KEYWORDS:
        click.echo(f"Capping keyword pool {len(keywords)} → {_MAX_KEYWORDS} (top by search_volume)")
        keywords = keywords[:_MAX_KEYWORDS]

    seeds = keywords[:_NUM_KEYWORD_SEEDS]
    click.echo(f"Batch endpoints: {len(keywords)} keywords, batch_size={_BATCH_SIZE} → {-(-len(keywords)//_BATCH_SIZE)} batches each")
    click.echo(f"Seeds (one-per-call endpoints): {seeds}")

    to_run = only or _KEYWORD_ENDPOINT_KEYS
    click.echo(f"Running endpoints: {to_run}")

    for key in to_run:
        fn = _KW_DISPATCH.get(key)
        if fn is None:
            raise click.ClickException(f"Unknown endpoint key: {key!r}. Valid: {_KEYWORD_ENDPOINT_KEYS}")
        await fn(client, domain_id, job_id, keywords, seeds)


# ---------- CLI ----------

@click.group()
@click.option("--project", default=None, help="Override GCP project id.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt before live uploads.")
@click.option("--job-id", default=None, help="Reuse an existing QA job_id (default: new UUID).")
@click.pass_context
def cli(ctx, project, yes, job_id):
    """End-to-end live QA against migrated DataForSEO tables."""
    ctx.ensure_object(dict)
    ctx.obj["project"] = project
    ctx.obj["yes"] = yes
    ctx.obj["job_id"] = job_id or str(uuid.uuid4())


def _confirm(ctx, step: str) -> None:
    click.echo(f"\nQA job_id: {ctx.obj['job_id']}")
    click.echo(f"Target domain: {_QA_DOMAIN}")
    click.echo(f"Step: {step}")
    click.echo(f"Mode: LIVE upload=True → writes to production DataForSEO tables")
    if not ctx.obj["yes"]:
        click.confirm("Proceed?", abort=True)


@cli.command()
@click.pass_context
def domain(ctx):
    """Group 1: domain-level endpoints."""
    _confirm(ctx, "GROUP 1 — domain")
    client, bq, project, domain_id = _load_client_and_ids(ctx.obj["project"])
    rk_df = asyncio.run(_run_domain_group(client, domain_id, ctx.obj["job_id"]))
    if rk_df is not None and not rk_df.empty:
        rk_df.to_pickle(_KEYWORDS_PICKLE)
        click.echo(f"\nSaved ranked_keywords DataFrame → {_KEYWORDS_PICKLE}")


@cli.command(name="url-bulk")
@click.pass_context
def url_bulk(ctx):
    """Group 2a: bulk_pages_summary only."""
    _confirm(ctx, "GROUP 2a — url (bulk_pages_summary)")
    client, bq, project, domain_id = _load_client_and_ids(ctx.obj["project"])
    asyncio.run(_run_url_bulk_pages_summary(client, bq, project, domain_id, ctx.obj["job_id"]))


@cli.command(name="url-summary")
@click.pass_context
def url_summary(ctx):
    """Group 2b: backlinks-summary (URL form)."""
    _confirm(ctx, "GROUP 2b — url (backlinks-summary)")
    client, bq, project, domain_id = _load_client_and_ids(ctx.obj["project"])
    asyncio.run(_run_url_backlinks_summary(client, bq, project, domain_id, ctx.obj["job_id"]))


@cli.command(name="url-backlinks")
@click.pass_context
def url_backlinks(ctx):
    """Group 2c: backlinks-backlinks (URL form, limit=10/URL)."""
    _confirm(ctx, "GROUP 2c — url (backlinks-backlinks, limit=10)")
    client, bq, project, domain_id = _load_client_and_ids(ctx.obj["project"])
    asyncio.run(_run_url_backlinks_backlinks(client, bq, project, domain_id, ctx.obj["job_id"]))


@cli.command()
@click.option("--only", default=None,
              help=f"Comma-separated endpoint keys to run. Valid: {','.join(_KEYWORD_ENDPOINT_KEYS)}. Omit to run all 8.")
@click.pass_context
def keyword(ctx, only):
    """Group 3: keyword-level endpoints."""
    only_list = [k.strip() for k in only.split(",")] if only else None
    _confirm(ctx, f"GROUP 3 — keyword (only={only_list or 'ALL'})")
    client, bq, project, domain_id = _load_client_and_ids(ctx.obj["project"])
    rk_df = None
    if _KEYWORDS_PICKLE.exists():
        try:
            rk_df = pd.read_pickle(_KEYWORDS_PICKLE)
            click.echo(f"Loaded ranked_keywords pickle ({len(rk_df)} rows) from {_KEYWORDS_PICKLE}")
        except Exception as e:
            click.echo(f"Failed to load pickle ({e}); falling back to BQ.")
    asyncio.run(_run_keyword_group(client, bq, project, domain_id, ctx.obj["job_id"], rk_df, only=only_list))


@cli.command(name="all")
@click.pass_context
def all_cmd(ctx):
    """Run domain → url → keyword end-to-end in one invocation."""
    _confirm(ctx, "ALL THREE GROUPS")
    client, bq, project, domain_id = _load_client_and_ids(ctx.obj["project"])
    job_id = ctx.obj["job_id"]

    async def _chain():
        rk_df = await _run_domain_group(client, domain_id, job_id)
        await _run_url_bulk_pages_summary(client, bq, project, domain_id, job_id)
        await _run_url_backlinks_summary(client, bq, project, domain_id, job_id)
        await _run_url_backlinks_backlinks(client, bq, project, domain_id, job_id)
        await _run_keyword_group(client, bq, project, domain_id, job_id, rk_df)

    asyncio.run(_chain())

    _section("QA RUN COMPLETE")
    click.echo(f"QA job_id: {job_id}")
    click.echo(f"Query rows for this run with:")
    click.echo(f"  WHERE job_id = '{job_id}'")


if __name__ == "__main__":
    cli(obj={})
