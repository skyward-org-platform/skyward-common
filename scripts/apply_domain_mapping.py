"""Apply the annotated domain-mapping CSV to the two migrated tables.

Reads docs/domain_mapping_2026_04_22.csv (or --csv). Per row decision:

  MATCH <domain_id>       use this existing Meta.domains entry
  CREATE                  provision raw_domain in Meta.domains (client_id=None)
  CREATE <replacement>    provision the replacement instead
  SKIP                    leave the row's domain_id NULL (no-op)

Flow:
  1. Parse CSV.
  2. Collect every domain that needs provisioning (CREATE + CREATE <replacement>).
  3. Call MetaClient.add_domains(unique_list, client_id=None) — one batch query.
     Existing entries are returned with their existing IDs; new ones get new IDs.
  4. Build a raw_domain -> (domain_id, canonical_domain) map spanning all
     MATCH and CREATE decisions.
  5. Run one MERGE per affected table (ranked_keywords + backlinks-backlinks)
     using UNNEST'd mapping array.
  6. Report row counts.

Idempotent: re-running against an already-applied state is a no-op.

Usage:
  uv run python scripts/apply_domain_mapping.py --dry-run
  uv run python scripts/apply_domain_mapping.py --yes
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

_SOURCE_DATASET = "DataForSEO"
_TARGET_TABLES = ["dataforseo_labs-google-ranked_keywords", "backlinks-backlinks"]


def _parse_decisions(df: pd.DataFrame) -> tuple[dict, list[str], list[str]]:
    """Return (raw_map, to_provision, errors).

    raw_map: {raw_domain: ('MATCH', domain_id, canonical) | ('CREATE', provision_target) | ('SKIP',)}
    to_provision: unique list of domain strings to pass to add_domains (preserving first-seen order).
    errors: any unparseable decisions.
    """
    raw_map: dict[str, tuple] = {}
    to_provision: list[str] = []
    seen_provision: set[str] = set()
    errors: list[str] = []

    for _, row in df.iterrows():
        raw = str(row["raw_domain"]).strip()
        decision = str(row["decision"]).strip()
        if not decision:
            errors.append(f"empty decision for raw={raw!r}")
            continue
        if decision == "SKIP":
            raw_map[raw] = ("SKIP",)
            continue
        m = re.fullmatch(r"MATCH\s+(\d+)", decision)
        if m:
            raw_map[raw] = ("MATCH", int(m.group(1)), str(row["meta_match_domain"]))
            continue
        if decision == "CREATE":
            provision_target = raw
        else:
            m = re.fullmatch(r"CREATE\s+(.+)", decision)
            if not m:
                errors.append(f"unparseable decision for raw={raw!r}: {decision!r}")
                continue
            provision_target = m.group(1).strip()
        raw_map[raw] = ("CREATE", provision_target)
        if provision_target not in seen_provision:
            seen_provision.add(provision_target)
            to_provision.append(provision_target)

    return raw_map, to_provision, errors


def _build_final_map(raw_map: dict, provision_result: dict, meta_client) -> dict[str, tuple[int, str]]:
    """Build raw_domain -> (domain_id, canonical_domain) for every non-SKIP row.

    provision_result: {cleaned_domain: (domain_id, cleaned_domain)} from add_domains output
    """
    final: dict[str, tuple[int, str]] = {}
    for raw, decision in raw_map.items():
        if decision[0] == "SKIP":
            continue
        if decision[0] == "MATCH":
            _, domain_id, canonical = decision
            final[raw] = (domain_id, canonical)
            continue
        # CREATE path — look up provision target's cleaned form in provision_result
        _, target = decision
        cleaned = meta_client._clean_domain(target, preserve_path=True)
        if cleaned not in provision_result:
            raise RuntimeError(
                f"After add_domains, couldn't find cleaned form '{cleaned}' "
                f"for raw='{raw}' (CREATE target='{target}') in returned mapping"
            )
        domain_id, canonical = provision_result[cleaned]
        final[raw] = (domain_id, canonical)
    return final


def _build_merge_sql(project: str, table: str, final_map: dict[str, tuple[int, str]]) -> str:
    """MERGE that updates each row's (domain, domain_id) based on raw_domain lookup."""
    values = ",\n    ".join(
        f"STRUCT({_sql_str(raw)} AS raw_domain, "
        f"{domain_id} AS domain_id, "
        f"{_sql_str(canonical)} AS canonical)"
        for raw, (domain_id, canonical) in final_map.items()
    )
    return (
        f"MERGE `{project}.{_SOURCE_DATASET}.{table}` t\n"
        f"USING (\n"
        f"  SELECT raw_domain, domain_id, canonical\n"
        f"  FROM UNNEST([\n"
        f"    {values}\n"
        f"  ])\n"
        f") m\n"
        f"ON t.domain = m.raw_domain\n"
        f"WHEN MATCHED THEN\n"
        f"  UPDATE SET domain_id = m.domain_id, domain = m.canonical;"
    )


def _sql_str(s: str) -> str:
    """SQL single-quoted string literal; escape embedded single quotes."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _execute_or_print(bq, sql: str, *, dry_run: bool, label: str) -> None:
    prefix = "[dry-run]" if dry_run else "[exec]"
    click.echo(f"\n{prefix} {label}")
    # For very long MERGEs, truncate preview
    if len(sql) > 6000:
        click.echo(sql[:3000] + "\n... [truncated] ...\n" + sql[-1500:])
    else:
        click.echo(sql)
    if not dry_run:
        bq.client.query(sql).result()
        click.echo(f"{prefix} {label} — OK")


@click.command()
@click.option(
    "--csv", "csv_path",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    default=_DEFAULT_CSV,
    help="Path to the annotated inventory CSV.",
)
@click.option("--dry-run", is_flag=True, help="Preview without provisioning or updating.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--project", default=None, help="Override GCP project id.")
def cli(csv_path: Path, dry_run: bool, yes: bool, project: str | None):
    """Apply the annotated domain-mapping CSV."""
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    from skyward.data.meta import MetaClient

    cfg = load_config()
    resolved_project = project or cfg.datahub_project_id
    bq = BigQueryClient(project_id=resolved_project)
    meta = MetaClient(bq)

    click.echo(f"Project: {resolved_project}")
    click.echo(f"CSV:     {csv_path}")
    click.echo(f"Mode:    {'DRY RUN' if dry_run else 'EXECUTE'}")

    df = pd.read_csv(csv_path, dtype={"meta_match_domain_id": "Int64"})
    df["decision"] = df["decision"].fillna("").astype(str)

    raw_map, to_provision, errors = _parse_decisions(df)
    if errors:
        click.echo(f"\nERROR: {len(errors)} unparseable decisions. First 5:", err=True)
        for e in errors[:5]:
            click.echo(f"  {e}", err=True)
        sys.exit(1)

    match_count = sum(1 for v in raw_map.values() if v[0] == "MATCH")
    create_count = sum(1 for v in raw_map.values() if v[0] == "CREATE")
    skip_count = sum(1 for v in raw_map.values() if v[0] == "SKIP")
    click.echo(f"\nParsed {len(raw_map)} decisions:")
    click.echo(f"  MATCH:  {match_count}")
    click.echo(f"  CREATE: {create_count}  (unique domains to provision: {len(to_provision)})")
    click.echo(f"  SKIP:   {skip_count}")

    if not dry_run and not yes:
        click.confirm(f"\nProceed with provisioning {len(to_provision)} "
                      f"Meta.domains entries (client_id=None) "
                      f"and MERGEing {len(_TARGET_TABLES)} tables?", abort=True)

    # Step 1: batch-create domains
    if dry_run:
        click.echo(f"\n[dry-run] Would call MetaClient.add_domains({len(to_provision)} domains, client_id=None)")
        if to_provision:
            click.echo(f"[dry-run] First 10 to provision: {to_provision[:10]}")
        click.echo("\n[dry-run] Would then build raw_domain -> (domain_id, canonical) map "
                   "merging MATCH entries with the newly-provisioned IDs.")
    else:
        click.echo(f"\n[exec] Provisioning {len(to_provision)} domains…")
        added = meta.add_domains(to_provision, client_id=None) if to_provision else []
        # add_domains returns [{'domain_id': int, 'domain': str (cleaned), ...}, ...]
        # Build {cleaned: (domain_id, cleaned)} lookup.
        provision_result = {d["domain"]: (int(d["domain_id"]), d["domain"]) for d in added}
        new_count = sum(1 for d in added if not d.get("skipped", False))
        click.echo(f"  added={len(added)} (client_id=None orphans)")
        final_map = _build_final_map(raw_map, provision_result, meta)
        click.echo(f"  final raw_domain -> domain_id map has {len(final_map)} entries")

    if dry_run:
        # For dry-run, synthesize a small placeholder final_map for SQL preview
        click.echo("\n[dry-run] MERGE template per table (actual will have real domain_ids):")
        sample_map = {raw: (0, raw) for raw, v in list(raw_map.items())[:3] if v[0] != "SKIP"}
        if sample_map:
            for table in _TARGET_TABLES:
                preview = _build_merge_sql(resolved_project, table, sample_map)
                click.echo(f"\n-- Preview for {table} (3 sample rows, real run will have {match_count + create_count}):")
                click.echo(preview)
        click.echo("\n=== Dry run complete. ===")
        return

    # Step 2: MERGE per table
    for table in _TARGET_TABLES:
        merge_sql = _build_merge_sql(resolved_project, table, final_map)
        _execute_or_print(bq, merge_sql, dry_run=False, label=f"MERGE {table}")

        # Verify: how many rows got domain_id populated
        verify_sql = (
            f"SELECT COUNTIF(domain_id IS NOT NULL) AS populated, COUNT(*) AS total "
            f"FROM `{resolved_project}.{_SOURCE_DATASET}.{table}`"
        )
        r = list(bq.client.query(verify_sql).result())[0]
        click.echo(f"  {table}: domain_id populated on {r.populated:,}/{r.total:,} rows")

    click.echo("\n=== Apply complete. ===")


if __name__ == "__main__":
    cli()
