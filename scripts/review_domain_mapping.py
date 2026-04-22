"""Interactive domain-mapping review — fast, no confirmations, no live writes.

Walks through the inventory CSV one row at a time and records your decision
in the CSV. Does NOT touch Meta.domains — all creates are deferred to the
apply step (a separate script that reads the finalized CSV).

For each row:
  - If `decision` is already filled in (prior session): skip silently.
  - If a Meta.domains MATCH was suggested (31 such rows): auto-accept, print
    a line, move on with no keypress.
  - Otherwise prompt with a single letter:
      c — CREATE (queue the raw domain for provisioning)
      e — EDIT   (type a replacement; instant local lookup against the
                  Meta.domains snapshot loaded at startup; records MATCH if
                  found, CREATE <replacement> otherwise)
      s — SKIP   (no provisioning, domain_id stays NULL on those rows)
      q — QUIT   (saves progress and exits)

One keypress + Enter per undecided row. No API calls, no round-trips.

Decision column values after review:
  MATCH <domain_id>       existing Meta.domains entry to use
  CREATE                  provision the row's raw_domain (apply step will do it)
  CREATE <replacement>    provision this replacement instead
  SKIP                    don't backfill

Progress saves to the CSV after every decision. Ctrl+C is safe — re-run to
resume from the first undecided row.

Usage:
  uv run python scripts/review_domain_mapping.py
  uv run python scripts/review_domain_mapping.py --csv /path/to/mapping.csv
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


_DEFAULT_CSV = Path(__file__).resolve().parents[1] / "docs" / "domain_mapping_2026_04_22.csv"

_NORMALIZE = re.compile(r"^(?:https?://)?(?:www\.)?", re.IGNORECASE)
_TRAILING_SLASH = re.compile(r"/+$")
_WHITESPACE = re.compile(r"\s+")


def _normalize(raw: str) -> str:
    if raw is None:
        return ""
    s = _NORMALIZE.sub("", raw.strip())
    s = _TRAILING_SLASH.sub("", s)
    s = _WHITESPACE.sub("", s)
    return s.lower()


def _load_meta_snapshot(bq, project: str) -> dict[str, int]:
    """Read all Meta.domains once into a {normalized_domain: domain_id} dict."""
    df = bq.client.query(
        f"SELECT domain_id, domain FROM `{project}.Meta.domains`"
    ).result().to_dataframe()
    return {_normalize(d): int(did) for d, did in zip(df["domain"], df["domain_id"])}


def _save(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False)


def _prompt_key(progress: str, raw_domain: str, row_count: int) -> str:
    """One-line prompt returning c/e/s/q."""
    click.echo(f"\n{progress} {raw_domain!r} ({row_count:,} rows) — not in Meta")
    return click.prompt(
        "  [c]reate / [e]dit / [s]kip / [q]uit",
        default="c", show_default=True,
        type=click.Choice(["c", "e", "s", "q"], case_sensitive=False),
    ).lower()


def _handle_edit(meta_snapshot: dict[str, int]) -> str:
    """Prompt for a replacement, return the decision string.

    No confirmation — whatever the user types is the final call for this row.
    """
    edited = click.prompt("  Replacement domain").strip()
    if not edited:
        return "SKIP"  # empty input → treat as skip
    norm = _normalize(edited)
    if norm in meta_snapshot:
        domain_id = meta_snapshot[norm]
        click.echo(f"  → MATCH {domain_id} (found in Meta.domains)")
        return f"MATCH {domain_id}"
    click.echo(f"  → CREATE {edited} (queued for provisioning)")
    return f"CREATE {edited}"


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

    cfg = load_config()
    resolved_project = project or cfg.datahub_project_id
    bq = BigQueryClient(project_id=resolved_project)

    click.echo(f"CSV: {csv_path}")
    click.echo("Loading Meta.domains snapshot for instant local lookups…")
    meta_snapshot = _load_meta_snapshot(bq, resolved_project)
    click.echo(f"  loaded {len(meta_snapshot):,} Meta.domains entries")

    df = pd.read_csv(csv_path, dtype={
        "meta_match_domain_id": "Int64",
        "rows_ranked_keywords": "Int64",
        "rows_backlinks_backlinks": "Int64",
        "rows_total": "Int64",
    })
    if "decision" not in df.columns:
        df["decision"] = ""
    df["decision"] = df["decision"].fillna("").astype(str)

    total = len(df)
    already_done = int((df["decision"].str.strip() != "").sum())
    auto_matches = int((df["suggested"] == "MATCH").sum())
    remaining_manual = total - auto_matches - already_done
    click.echo(f"\nRows: {total}  already decided: {already_done}  "
               f"auto-MATCH: {auto_matches}  manual review: {remaining_manual}\n")

    processed = 0
    for idx, row in df.iterrows():
        if str(row["decision"]).strip():
            continue

        progress = f"[{idx + 1}/{total}]"

        if row["suggested"] == "MATCH" and pd.notna(row["meta_match_domain_id"]):
            domain_id = int(row["meta_match_domain_id"])
            click.echo(f"{progress} ✓ {row['raw_domain']!r} — auto-MATCH {domain_id}")
            df.at[idx, "decision"] = f"MATCH {domain_id}"
            processed += 1
            _save(df, csv_path)
            continue

        raw_domain = str(row["raw_domain"])
        row_count = int(row["rows_total"])
        choice = _prompt_key(progress, raw_domain, row_count)

        if choice == "c":
            click.echo(f"  → CREATE (queued for provisioning)")
            df.at[idx, "decision"] = "CREATE"
        elif choice == "e":
            df.at[idx, "decision"] = _handle_edit(meta_snapshot)
        elif choice == "s":
            click.echo(f"  → SKIP")
            df.at[idx, "decision"] = "SKIP"
        elif choice == "q":
            click.echo(f"\nQuitting. Progress saved to {csv_path}.")
            click.echo(f"Decisions made this session: {processed}")
            _save(df, csv_path)
            return

        processed += 1
        _save(df, csv_path)
    else:
        click.echo(f"\n=== All {total} rows processed. ===")
        click.echo(f"Decisions made this session: {processed}")

    # Summary
    decided = int((df["decision"].str.strip() != "").sum())
    matches = int(df["decision"].str.startswith("MATCH").sum())
    creates = int(df["decision"].str.startswith("CREATE").sum())
    skips = int((df["decision"] == "SKIP").sum())
    remaining = total - decided
    click.echo(f"\nFinal status:")
    click.echo(f"  MATCH (use existing):   {matches:,}")
    click.echo(f"  CREATE (to provision):  {creates:,}")
    click.echo(f"  SKIP:                   {skips:,}")
    click.echo(f"  Remaining:              {remaining:,}")
    click.echo(f"\nCSV saved to: {csv_path}")
    click.echo(f"\nNext: the apply script will read this CSV, call MetaClient.add_domains "
               f"for every CREATE, then run the UPDATEs/MERGEs.")


if __name__ == "__main__":
    cli()
