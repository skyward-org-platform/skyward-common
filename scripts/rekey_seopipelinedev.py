"""Re-key SEOPipelineDev integer project_id to match Supabase (the source of truth).

The original get_next_id race collided projects 15 & 17 (3 domains each). The
migration split them in Supabase (and thedentalshop moved 23->28). SEOPipelineDev
data was written under the OLD collided ids. Each data row carries a job_id, and
SEOPipelineDev.runs.config_snapshot.project.domain records the true domain per
job — so we map job_id -> domain -> Supabase project_id and UPDATE every table.

ONLY touches rows whose job_id belongs to the affected projects (15/17/23); all
other data is untouched. Dry-run by default.

    export $(grep '^SUPABASE_DB_URL=' secrets/skyward-ops-supabase.env | xargs)
    python scripts/rekey_seopipelinedev.py            # dry-run
    python scripts/rekey_seopipelinedev.py --apply     # writes to BQ SEOPipelineDev
"""
from __future__ import annotations

import argparse
import os
import sys

from skyward.config import load_config
from skyward.data.bigquery import BigQueryClient
from skyward.data.supabase import SupabaseClient

DS = "SEOPipelineDev"
AFFECTED_PROJECTS = (15, 17, 23)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    P = cfg.datahub_project_id
    bq = BigQueryClient(project_id=P, credentials_info=cfg.datahub_credentials)
    sb = SupabaseClient(cfg.supabase_db_url)

    # 1. domain -> canonical Supabase project_id
    dmap = sb.query("select d.domain, pd.project_id from meta.project_domains pd "
                    "join meta.domains d on d.domain_id=pd.domain_id")
    domain_to_proj = dict(zip(dmap["domain"], dmap["project_id"].astype(int)))

    # 2. job_id -> new project_id, from runs.config_snapshot.project.domain
    runs = bq.client.query(f"""
        SELECT job_id, JSON_VALUE(config_snapshot,'$.project.domain') AS domain
        FROM `{P}.{DS}.runs` WHERE project_id IN {AFFECTED_PROJECTS}
    """).result().to_dataframe()
    job_to_new = {}
    unmapped = []
    for _, r in runs.iterrows():
        np = domain_to_proj.get(r["domain"])
        if np is None:
            unmapped.append((r["job_id"], r["domain"]))
        else:
            job_to_new[r["job_id"]] = int(np)
    if unmapped:
        print("ABORT — job(s) whose domain has no Supabase project:")
        for j, d in unmapped:
            print(f"  {j}  domain={d!r}")
        sys.exit(1)
    print(f"{len(job_to_new)} jobs mapped to Supabase project_ids.")

    # 3. every SEOPipelineDev BASE TABLE (not VIEW) with both project_id and job_id
    base_tables = set(bq.client.query(f"""
        SELECT table_name FROM `{P}.{DS}.INFORMATION_SCHEMA.TABLES`
        WHERE table_type = 'BASE TABLE'
    """).result().to_dataframe()["table_name"])
    cols = bq.client.query(f"""
        SELECT table_name, STRING_AGG(column_name) AS cols
        FROM `{P}.{DS}.INFORMATION_SCHEMA.COLUMNS`
        WHERE column_name IN ('project_id','job_id') GROUP BY table_name
    """).result().to_dataframe()
    tables = [r["table_name"] for _, r in cols.iterrows()
              if r["table_name"] in base_tables
              and "project_id" in r["cols"].split(",") and "job_id" in r["cols"].split(",")]

    # tables with project_id but NO job_id — can't re-key by job; warn if they hold affected data
    pid_only = bq.client.query(f"""
        SELECT table_name FROM `{P}.{DS}.INFORMATION_SCHEMA.COLUMNS`
        WHERE column_name='project_id'
          AND table_name NOT IN ({",".join(repr(t) for t in tables) or "''"})
    """).result().to_dataframe()["table_name"].tolist()

    jobstr = ",".join(f"'{j}'" for j in job_to_new)
    case = " ".join(f"WHEN '{j}' THEN {np}" for j, np in job_to_new.items())

    print("\n=== PLAN (rows to re-key, by table) ===")
    total = 0
    for t in tables:
        df = bq.client.query(f"""
            SELECT COUNT(*) n FROM `{P}.{DS}.{t}`
            WHERE job_id IN ({jobstr}) AND project_id != (CASE job_id {case} END)
        """).result().to_dataframe()
        n = int(df.iloc[0]["n"])
        total += n
        print(f"  {t}: {n} rows change project_id")
    print(f"  TOTAL: {total} rows")

    if pid_only:
        print("\n  Tables with project_id but NO job_id (checking for affected data):")
        for t in pid_only:
            df = bq.client.query(f"SELECT COUNT(*) n FROM `{P}.{DS}.{t}` WHERE project_id IN {AFFECTED_PROJECTS}").result().to_dataframe()
            n = int(df.iloc[0]["n"])
            flag = "  <-- HAS affected rows, CANNOT auto-rekey (no job_id)" if n else ""
            print(f"    {t}: {n}{flag}")

    if not args.apply:
        print("\nDRY-RUN. Re-run with --apply to write.")
        sb.close()
        return

    print("\n=== APPLYING ===")
    for t in tables:
        job = bq.client.query(f"""
            UPDATE `{P}.{DS}.{t}` SET project_id = CASE job_id {case} END
            WHERE job_id IN ({jobstr}) AND project_id != (CASE job_id {case} END)
        """).result()
        print(f"  {t}: updated {job.num_dml_affected_rows} rows")
    sb.close()
    print("\nDONE — SEOPipelineDev project_ids now match Supabase.")


if __name__ == "__main__":
    main()
