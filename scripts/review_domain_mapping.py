"""Interactive domain-mapping review.

Walks through the inventory CSV one row at a time. For each row:

  - If `decision` is already filled in (from a prior session): skip silently.
  - If a Meta.domains MATCH was suggested: auto-accept, print "already matched"
    and move on (no keypress needed).
  - If no match: prompt:
        [c] create this domain in Meta.domains now (calls MetaClient.add_domains)
        [e] edit — replace with a different domain string, then re-check
        [s] skip — leave this row's domain_id NULL
        [q] quit — save progress and exit

Progress is saved to the CSV after every decision. Safe to Ctrl+C and resume.

The resulting CSV will have a `decision` column with one of:
  MATCH <domain_id>       — use this Meta.domains entry
  SKIP                    — don't backfill; leave domain_id NULL

(There's no CREATE-only state — if you choose 'c' we create immediately and
record the new domain_id as MATCH <id> so the apply step is a pure lookup.)

Usage:
  uv run python scripts/review_domain_mapping.py
  uv run python scripts/review_domain_mapping.py --csv /path/to/mapping.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


_DEFAULT_CSV = Path(__file__).resolve().parents[1] / "docs" / "domain_mapping_2026_04_22.csv"


def _save(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False)


def _lookup_meta(meta_client, domain: str) -> dict | None:
    """Return dict(domain_id, domain) if found in Meta.domains, else None."""
    result = meta_client.get_domain(domain)
    return result if result else None


def _create_in_meta(meta_client, domain: str) -> int:
    """Provision a new Meta.domains entry and return its domain_id."""
    added = meta_client.add_domains([domain])
    if not added:
        raise RuntimeError(f"add_domains returned no rows for '{domain}'")
    return int(added[0]["domain_id"])


def _handle_no_match(meta_client, raw_domain: str, normalized: str, row_count: int) -> str | None:
    """Prompt the user to CREATE / EDIT / SKIP / QUIT.

    Returns the new value for the `decision` column, or None for QUIT.
    """
    click.echo(f"\n  domain:    {raw_domain}")
    if raw_domain != normalized:
        click.echo(f"  normalized: {normalized}")
    click.echo(f"  used in:   {row_count:,} rows")
    click.echo(f"  status:    not found in Meta.domains")

    while True:
        choice = click.prompt(
            "  [c]reate / [e]dit / [s]kip / [q]uit",
            default="c", show_default=True,
            type=click.Choice(["c", "e", "s", "q"], case_sensitive=False),
        ).lower()

        if choice == "s":
            click.echo(f"  → SKIP (domain_id will stay NULL for these {row_count:,} rows)")
            return "SKIP"

        if choice == "q":
            return None

        if choice == "c":
            if not click.confirm(f"  Create Meta.domain '{raw_domain}'?", default=True):
                continue
            try:
                new_id = _create_in_meta(meta_client, raw_domain)
            except Exception as e:
                click.echo(f"  ERROR creating domain: {e}", err=True)
                continue
            click.echo(f"  → Created domain_id={new_id}. Decision: MATCH {new_id}")
            return f"MATCH {new_id}"

        if choice == "e":
            edited = click.prompt("  Enter replacement domain").strip()
            if not edited:
                continue
            existing = _lookup_meta(meta_client, edited)
            if existing:
                domain_id = existing["domain_id"]
                canonical = existing["domain"]
                click.echo(f"  '{edited}' IS in Meta.domains (id={domain_id}, canonical='{canonical}')")
                if click.confirm(f"  Map rows with raw_domain='{raw_domain}' to domain_id={domain_id}?",
                                 default=True):
                    click.echo(f"  → Decision: MATCH {domain_id}")
                    return f"MATCH {domain_id}"
                continue
            click.echo(f"  '{edited}' is NOT in Meta.domains.")
            if click.confirm(f"  Create Meta.domain '{edited}' now?", default=True):
                try:
                    new_id = _create_in_meta(meta_client, edited)
                except Exception as e:
                    click.echo(f"  ERROR creating domain: {e}", err=True)
                    continue
                click.echo(f"  → Created domain_id={new_id}. Decision: MATCH {new_id}")
                return f"MATCH {new_id}"
            # user said no to creating the edited form — loop back
            continue


@click.command()
@click.option(
    "--csv", "csv_path",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    default=_DEFAULT_CSV,
    help="Path to the inventory CSV.",
)
@click.option("--project", default=None, help="Override GCP project id.")
def cli(csv_path: Path, project: str | None):
    """Walk through the inventory CSV one domain at a time."""
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    from skyward.data.meta import MetaClient

    cfg = load_config()
    resolved_project = project or cfg.datahub_project_id
    bq = BigQueryClient(project_id=resolved_project)
    meta = MetaClient(bq)

    df = pd.read_csv(csv_path, dtype={
        "meta_match_domain_id": "Int64",
        "rows_ranked_keywords": "Int64",
        "rows_backlinks_backlinks": "Int64",
        "rows_total": "Int64",
    })
    # Ensure 'decision' column exists (CSV was written by the inventory script).
    if "decision" not in df.columns:
        df["decision"] = ""
    df["decision"] = df["decision"].fillna("").astype(str)

    total = len(df)
    already_done = int((df["decision"].str.strip() != "").sum())
    auto_matches = int((df["suggested"] == "MATCH").sum())
    click.echo(f"CSV: {csv_path}")
    click.echo(f"Total rows: {total}")
    click.echo(f"  Already decided: {already_done}")
    click.echo(f"  Suggested MATCH (will auto-accept): {auto_matches}")
    click.echo(f"  Need manual review: {total - auto_matches - already_done}")
    click.echo("")

    processed_this_session = 0
    for idx, row in df.iterrows():
        if str(row["decision"]).strip():
            continue  # skip already-decided

        progress = f"[{idx + 1}/{total}]"

        if row["suggested"] == "MATCH" and pd.notna(row["meta_match_domain_id"]):
            domain_id = int(row["meta_match_domain_id"])
            click.echo(f"{progress} ✓ '{row['raw_domain']}' — already in Meta.domains "
                       f"(id={domain_id}, canonical='{row['meta_match_domain']}')")
            df.at[idx, "decision"] = f"MATCH {domain_id}"
            processed_this_session += 1
            _save(df, csv_path)
            continue

        click.echo(f"\n{progress} ─────────────────────────────────────────")
        new_decision = _handle_no_match(
            meta,
            raw_domain=str(row["raw_domain"]),
            normalized=str(row["normalized_domain"]),
            row_count=int(row["rows_total"]),
        )
        if new_decision is None:
            click.echo(f"\nQuitting. Progress saved to {csv_path}.")
            click.echo(f"Decisions made this session: {processed_this_session}")
            break

        df.at[idx, "decision"] = new_decision
        processed_this_session += 1
        _save(df, csv_path)
    else:
        # Loop completed without a quit
        click.echo(f"\n=== All {total} rows processed. ===")
        click.echo(f"Decisions made this session: {processed_this_session}")

    # Summary
    decided = int((df["decision"].str.strip() != "").sum())
    matched = int(df["decision"].str.startswith("MATCH").sum())
    skipped = int((df["decision"] == "SKIP").sum())
    remaining = total - decided
    click.echo(f"\nFinal status:")
    click.echo(f"  MATCH:     {matched:,}")
    click.echo(f"  SKIP:      {skipped:,}")
    click.echo(f"  Remaining: {remaining:,}")
    click.echo(f"\nCSV saved to: {csv_path}")


if __name__ == "__main__":
    cli()
