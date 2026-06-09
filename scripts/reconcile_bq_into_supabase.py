"""One-time additive merge of outstanding BQ Meta edits INTO Supabase, now that
Supabase is the source of truth and BQ is frozen/stale.

ADDITIVE ONLY — never deletes Supabase rows (SF and other writers own Supabase
now). Matches on natural keys (domain string, project name + client) so it works
even though BQ and Supabase IDs have diverged. Colliding IDs are remapped: the
incoming BQ row gets a fresh Supabase id, and its references are repointed.

    export $(grep '^SUPABASE_DB_URL=' secrets/skyward-ops-supabase.env | xargs)
    python scripts/reconcile_bq_into_supabase.py            # dry-run (no writes)
    python scripts/reconcile_bq_into_supabase.py --apply    # writes to Supabase
"""
from __future__ import annotations

import argparse
import os
import sys

from skyward.config import load_config
from skyward.data.bigquery import BigQueryClient
from skyward.data.supabase import SupabaseClient


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    P = cfg.datahub_project_id
    bq = BigQueryClient(project_id=P, credentials_info=cfg.datahub_credentials)
    sb = SupabaseClient(cfg.supabase_db_url)
    sb._conn.autocommit = False  # one transaction

    def bqdf(sql):
        return bq.client.query(sql).result().to_dataframe()

    # ---- load both sides ----
    bq_dom = bqdf(f"SELECT domain_id, domain, domain_name, is_active, notes FROM `{P}.Meta.domains`")
    pg_dom = sb.query("select domain_id, domain, domain_name, is_active, notes from meta.domains")
    pg_dom_by_str = dict(zip(pg_dom["domain"], pg_dom["domain_id"]))      # domain -> pg id
    pg_dom_ids = set(int(x) for x in pg_dom["domain_id"])
    bq_id_to_str = dict(zip(bq_dom["domain_id"], bq_dom["domain"]))

    plan = {"domains": [], "client_domains": [], "projects": [], "project_domains": []}

    # ---- 1. new domains (in BQ, not in Supabase by domain string) ----
    domain_id_map = {}  # bq_domain_id -> pg_domain_id (after merge)
    for _, r in bq_dom.iterrows():
        if r["domain"] in pg_dom_by_str:
            domain_id_map[int(r["domain_id"])] = int(pg_dom_by_str[r["domain"]])
            continue
        bq_id = int(r["domain_id"])
        keep_id = bq_id if bq_id not in pg_dom_ids else None  # reuse BQ id only if free in PG
        plan["domains"].append({"bq_id": bq_id, "keep_id": keep_id, "domain": r["domain"],
                                "domain_name": r["domain_name"],
                                "is_active": bool(r["is_active"]), "notes": r["notes"]})

    # ---- 3. new projects (by client_id + project_name) ----
    bq_proj = bqdf(f"SELECT project_id, client_id, project_type, project_name, status, notes FROM `{P}.Meta.projects`")
    pg_proj = sb.query("select project_id, client_id, project_name from meta.projects")
    pg_proj_keys = set(zip(pg_proj["client_id"].astype(int), pg_proj["project_name"]))
    project_id_map = {}
    for _, r in bq_proj.iterrows():
        key = (int(r["client_id"]), r["project_name"])
        if key in pg_proj_keys:
            continue  # already present (matched by name) — skip (split-offs etc.)
        plan["projects"].append({"bq_id": int(r["project_id"]), "client_id": int(r["client_id"]),
                                 "project_type": r["project_type"], "project_name": r["project_name"],
                                 "status": r["status"], "notes": r["notes"]})

    # ---- 2. new client_domains (by client_id + domain string) ----
    bq_cd = bqdf(f"SELECT client_id, domain_id, is_competitor, priority, notes FROM `{P}.Meta.client_domains`")
    pg_cd = sb.query("select client_id, domain_id from meta.client_domains")
    pg_cd_keys = set(zip(pg_cd["client_id"].astype(int), pg_cd["domain_id"].astype(int)))
    for _, r in bq_cd.iterrows():
        dom_str = bq_id_to_str.get(r["domain_id"])
        # resolved pg domain id: existing mapping, or a to-be-inserted new domain
        plan["client_domains"].append({"client_id": int(r["client_id"]), "bq_domain_id": int(r["domain_id"]),
                                        "domain": dom_str, "is_competitor": bool(r["is_competitor"]),
                                        "priority": r["priority"], "notes": r["notes"], "_raw": (int(r["client_id"]), int(r["domain_id"]))})
    # keep only client_domains not already in PG (need final domain id resolved later)

    # ---- 4. new project_domains (for the new projects only) ----
    bq_pd = bqdf(f"SELECT project_id, domain_id, role, priority, notes FROM `{P}.Meta.project_domains`")
    new_proj_bq_ids = {p["bq_id"] for p in plan["projects"]}
    for _, r in bq_pd.iterrows():
        if int(r["project_id"]) in new_proj_bq_ids:
            plan["project_domains"].append({"bq_project_id": int(r["project_id"]), "bq_domain_id": int(r["domain_id"]),
                                            "domain": bq_id_to_str.get(r["domain_id"]),
                                            "role": r["role"], "priority": r["priority"], "notes": r["notes"]})

    # ---- report plan ----
    print("=== RECONCILE PLAN (BQ -> Supabase, additive) ===")
    print(f"new domains: {len(plan['domains'])}")
    for d in plan["domains"]:
        tag = f"keep id {d['keep_id']}" if d["keep_id"] else "REMAP -> fresh id (BQ id taken in PG)"
        print(f"    {d['domain']} (BQ {d['bq_id']}) -> {tag}")
    print(f"new projects: {len(plan['projects'])}")
    for p in plan["projects"]:
        print(f"    {p['project_name']} (client {p['client_id']}, BQ {p['bq_id']}) -> REMAP -> fresh Supabase id")
    print(f"new client_domains (pre-dedup): {len(plan['client_domains'])}  (filtered to genuinely-new at apply time)")
    print(f"new project_domains: {len(plan['project_domains'])}")

    if not args.apply:
        print("\nDRY-RUN. Re-run with --apply to write.")
        sb.close()
        return

    # ---- apply (single transaction) ----
    print("\n=== APPLYING ===")
    # 1. domains
    for d in plan["domains"]:
        if d["keep_id"]:
            sb.execute("insert into meta.domains (domain_id, domain, domain_name, is_active, notes) "
                       "overriding system value values (%(i)s,%(d)s,%(n)s,%(a)s,%(o)s)",
                       {"i": d["keep_id"], "d": d["domain"], "n": d["domain_name"], "a": d["is_active"], "o": d["notes"]})
            domain_id_map[d["bq_id"]] = d["keep_id"]
            print(f"  + domain {d['domain']} (id {d['keep_id']})")
        else:
            rows = sb.execute("insert into meta.domains (domain, domain_name, is_active, notes) "
                              "values (%(d)s,%(n)s,%(a)s,%(o)s) returning domain_id",
                              {"d": d["domain"], "n": d["domain_name"], "a": d["is_active"], "o": d["notes"]})
            new_id = int(rows[0][0])
            domain_id_map[d["bq_id"]] = new_id
            print(f"  + domain {d['domain']} (BQ {d['bq_id']} -> Supabase {new_id})  [REMAPPED]")
    # reset domain identity seq above max
    sb.execute("select setval(pg_get_serial_sequence('meta.domains','domain_id'), (select max(domain_id) from meta.domains))")

    # 3. projects
    for p in plan["projects"]:
        rows = sb.execute("insert into meta.projects (client_id, project_type, project_name, status, notes) "
                          "values (%(c)s,%(t)s,%(n)s,%(s)s,%(o)s) returning project_id",
                          {"c": p["client_id"], "t": p["project_type"], "n": p["project_name"], "s": p["status"], "o": p["notes"]})
        new_id = int(rows[0][0])
        project_id_map[p["bq_id"]] = new_id
        print(f"  + project {p['project_name']} (BQ {p['bq_id']} -> Supabase {new_id})  [REMAPPED]")
    sb.execute("select setval(pg_get_serial_sequence('meta.projects','project_id'), (select max(project_id) from meta.projects))")

    # 2. client_domains (resolve final domain id; skip ones already present)
    pg_cd_now = sb.query("select client_id, domain_id from meta.client_domains")
    have = set(zip(pg_cd_now["client_id"].astype(int), pg_cd_now["domain_id"].astype(int)))
    added = 0
    for cd in plan["client_domains"]:
        pg_did = domain_id_map.get(cd["bq_domain_id"])
        if pg_did is None:  # domain not in PG and not inserted -> resolve by string
            pg_did = pg_dom_by_str.get(cd["domain"])
        if pg_did is None:
            print(f"  ! skip client_domain (client {cd['client_id']}, domain {cd['domain']}): domain not in Supabase")
            continue
        if (cd["client_id"], pg_did) in have:
            continue  # already linked
        sb.execute("insert into meta.client_domains (client_id, domain_id, is_competitor, priority, notes) "
                   "values (%(c)s,%(d)s,%(k)s,%(p)s,%(o)s)",
                   {"c": cd["client_id"], "d": pg_did, "k": cd["is_competitor"], "p": cd["priority"], "o": cd["notes"]})
        have.add((cd["client_id"], pg_did))
        added += 1
    print(f"  + {added} client_domains")

    # 4. project_domains
    for pd_ in plan["project_domains"]:
        new_pid = project_id_map.get(pd_["bq_project_id"])
        pg_did = domain_id_map.get(pd_["bq_domain_id"]) or pg_dom_by_str.get(pd_["domain"])
        if new_pid is None or pg_did is None:
            print(f"  ! skip project_domain ({pd_['bq_project_id']},{pd_['domain']}): unresolved")
            continue
        sb.execute("insert into meta.project_domains (project_id, domain_id, role, priority, notes) "
                   "values (%(p)s,%(d)s,%(r)s,%(pr)s,%(o)s)",
                   {"p": new_pid, "d": pg_did, "r": pd_["role"], "pr": pd_["priority"], "o": pd_["notes"]})
        print(f"  + project_domain (project {new_pid} -> domain {pg_did})")

    sb._conn.commit()
    print("\nCOMMITTED.")
    sb.close()


if __name__ == "__main__":
    main()
