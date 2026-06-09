# Migrating consumers to skyward-common v1.5.0 (Supabase-backed Meta)

> Canonical consumer-upgrade guide for v1.5.0. The README Quick Start has been
> updated to match (`DataHub(sb, bq)` + `SUPABASE_DB_URL`); the verified
> constructor signatures below are authoritative.

## What changed

v1.5.0 moves the **Meta layer** (domains, clients, projects, datasets) out of
BigQuery and into the **skyward-ops Supabase** project (`meta` schema). Meta
reads/writes now go through a new `SupabaseClient`. Analytics data,
`Logs.upload_events`, and `INFORMATION_SCHEMA` scans stay in BigQuery — `DataHub`
is now **hybrid** (Supabase for Meta, BQ for the rest).

The Meta data itself has already been migrated and verified (drift-clean,
live-smoke 42/42). This guide is only about updating the **consumer code**.

## Breaking changes (the only API surface that changed)

```python
# Constructors — verified signatures in v1.5.0:
SupabaseClient(db_url: str)            # new — skyward.data.supabase
MetaClient(sb_client)                  # was MetaClient(bq_client)
DataHub(sb_client, bq_client)          # was DataHub(bq_client) — sb is FIRST arg
```

- New required env var **`SUPABASE_DB_URL`** — the skyward-ops session/transaction
  pooler connection string. `load_config()` exposes it as `cfg.supabase_db_url`
  (reads `os.getenv("SUPABASE_DB_URL")`).
- `SupabaseClient` connects at construction (`psycopg.connect(db_url, autocommit=True)`),
  so a missing/empty URL raises immediately:
  `ValueError: SupabaseClient requires a connection string (SUPABASE_DB_URL)`.
- `psycopg[binary]>=3.2` is now a transitive dependency of skyward-common — no
  separate install needed in consumers.
- **The connection string is the entire credential.** No service-role key, no
  anon key, no separate Supabase auth. The pooler URL embeds the password —
  treat it as a secret (`.env` / secret manager, never committed).

Everything else is source-compatible. The Meta methods consumers actually call
are unchanged in signature: `get_domain(domain)`, `add_domain(domain)`,
`search_domains(query, limit=10)`, and the static `MetaClient._clean_domain(...)`.

## Standard fix (per affected repo)

1. Pin skyward-common to `v1.5.0`:
   ```toml
   # pyproject.toml — [project] dependencies (PEP 508 string, the form already
   # used by the consumer repos)
   dependencies = [
       "skyward-common @ git+https://github.com/skyward-org-platform/skyward-common.git@v1.5.0",
   ]
   ```
   ```text
   # requirements.txt
   skyward-common @ git+https://github.com/skyward-org-platform/skyward-common.git@v1.5.0
   ```
   (If the repo uses `[tool.uv.sources]`, set `tag = "v1.5.0"` there instead.)
   Re-lock (`uv lock`) / reinstall so the pin actually resolves.

2. Add `SUPABASE_DB_URL` to that repo's environment / secrets (see "what differs"
   below — the *where* is per-repo).

3. Build the Supabase client and pass it into Meta/DataHub:
   ```python
   from skyward.data.supabase import SupabaseClient

   sb = SupabaseClient(cfg.supabase_db_url)
   meta = DataHub(sb, bq)     # was DataHub(bq)
   # or, if the repo uses MetaClient directly:
   meta = MetaClient(sb)      # was MetaClient(bq)
   ```

4. Smoke-test the Meta reads/writes that repo actually uses.

## What's the same vs what differs per consumer

**Same everywhere (the "what"):** the 3-line constructor change, the
`SUPABASE_DB_URL` requirement, and the transitive `psycopg`. Copy/paste-able.

**Differs per repo (the "how/where"):**
- **Where `SUPABASE_DB_URL` is sourced.** e.g. a VM-based job bakes it into the
  `.env` on the machine image (may require an image re-snapshot); Cloud Run /
  container repos use their own secret store. Confirm the deploy mechanics per repo.
- **Which Meta methods are called** → different smoke-test surface per repo.
- **Whether the repo constructs `DataHub`, `MetaClient`, or both.**
- **Deploy mechanics** (machine-image rebuild vs container redeploy).

## Testing

- **Automated/unit suites need nothing.** If your tests inject the Meta client as
  a mock (the standard pattern), they never connect to Supabase and
  `SUPABASE_DB_URL` is **not** required to run them.
- **Any live run requires a reachable `SUPABASE_DB_URL`** — `SupabaseClient`
  connects at construction, and `get_domain`/`add_domain` hit real Postgres.
- Before a live run, confirm: (a) the pooler string has **write** access (not a
  read replica — `add_domain` writes), and (b) network egress from your runtime
  to Supabase is allowed.

## Affected vs unaffected

- **Affected (use Meta/DataHub):** `skyward-seo-pipeline`, `skyward-platform-app`,
  `skyward-platform`, `sf-csv-to-bq-vm-uploader`.
- **Unaffected:** `reddit-monitor` — uses only `skyward.llm`; bump freely, no Meta
  work.

## Failure mode if you bump without the fix

Loud `TypeError` at `MetaClient`/`DataHub` construction (or the `ValueError`
above if `SUPABASE_DB_URL` is unset) — **not** silent. Safe to break-and-mark:
nothing breaks until a repo actually bumps to v1.5.0, since old pins stay on the
BQ-backed versions.

## ⚠️ Production cutover is lockstep (don't flip one writer at a time)

A v1.5.0 consumer writes Meta to **Supabase**; a v1.4.x consumer writes to
**BigQuery**. Running both in production = split-brain. Dev/test each repo freely,
but the *production* flip of all Meta **writers** should happen together with a
BQ-write freeze. Until then, Supabase is kept current by re-running
`scripts/check_meta_drift.py` + rebuilding from BQ on demand.
