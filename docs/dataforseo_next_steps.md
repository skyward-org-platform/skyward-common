# DataForSEO Standardization — Next Steps

**Branch:** `dataforseo-standardization` (19 commits, unpushed)
**Status as of this doc:** all code / tests / docs / migration scripts written. BigQuery untouched. Live tests un-run. Ready for human review → merge → migration → QA.

This doc is your punch list. Check items off as you go. Anything with ⚠️ is a blocker for the next phase.

---

## Phase A — Review the branch (you, locally)

- [ ] **Walk the commits.** `git log --oneline main..dataforseo-standardization` (19 commits). Read the messages, spot-check the diffs that look most sensitive to you. `git diff main..dataforseo-standardization -- src/skyward/data/dataforseo/base.py` is the load-bearing one to understand.
- [ ] **Skim the Obsidian mirror** under `/Projects/Skyward/Go-Skyward Repo Split/Stage 1 — skyward-common/` for the spec and 6 milestone plans.
- [ ] **Run the full non-live test suite locally to confirm green on your machine:** `uv run pytest -v --ignore=tests/live 2>&1 | tail -5` — should show `350 passed, 11 skipped`.
- [ ] **Confirm live tests collect + skip by default:** `uv run pytest tests/live/ -v 2>&1 | tail -5` — should show `23 skipped`. Don't pass `--run-live` yet.

---

## Phase B — Downstream consumer audit ⚠️ (you, before merging)

**This is the single biggest blocker.** The migration renames BQ tables and deletes two `BigQueryClient` methods. Anything outside this repo that references them will break when we flip the switch.

- [ ] **Grep `skyward-seo`** for:
  - Old DataForSEO table names: `backlinks_backlinks_live`, `backlinks_bulk_pages_summary_live`, `backlinks_summary_live`, `serp_google_organic_live_advanced`, `google_keyword-suggestions_live`, `google_related-keywords_live`, `keyword_data-google_ads-search_volume`, `dataforseo_labs_google_keyword_overview`, `dataforseo_labs_google_search_intent`
  - Broken BQ client methods: `bq_client.get_client_domains`, `bq.get_client_domains`, `bq_client.get_project_domains`, `bq.get_project_domains`
- [ ] **Repeat for `skyward-ai-faqs`**.
- [ ] **Repeat for `skyward-data-hub-admin`**.
- [ ] **Ahrefs / Looker / manual BQ dashboards / notebooks:** any saved queries or views referencing the old table names need updating.
- [ ] For each hit: migrate to the new canonical table name or to the correct `MetaClient` method. The canonical names are in `docs/dataforseo_naming_convention.md`.

---

## Phase C — Merge to main

- [ ] **Push the branch:** `git push -u origin dataforseo-standardization`
- [ ] **Open a PR** against `main` (title suggestion: "DataForSEO standardization — Phase 1 (rename + new BaseEndpoint + backlinks_summary + search_volume POST/GET)"). Paste the commit list or a summary of the 6 milestones into the description.
- [ ] **Self-review the diff on GitHub.** Large renames + new files are easier to spot in the web UI than in terminal `git diff`.
- [ ] **Merge.** Squash or merge-commit — your call. Squashing collapses the 19 commits into one tidy `feat(dataforseo): Phase 1 standardization`.

---

## Phase D — Tag a release

- [ ] **Bump version** in `pyproject.toml` and `src/skyward/__init__.py` from `1.2.0` → `1.3.0` (minor — new features, breaking changes to `DataForSEOClient` signature).
- [ ] **Commit** the version bump on `main`.
- [ ] **Create a GitHub release** with tag `v1.3.0` and a changelog pulled from the 19 commit messages. The existing GitHub Action will publish to GitHub Packages automatically.

---

## Phase E-prerequisite — Add column descriptions to the migration manifest ⚠️

**New scope:** every column in every new BQ table should carry a `description` pulled from DataForSEO's own API docs, so that BQ's Schema UI / Looker / manual querying surfaces what each field means. Right now the manifest's `*_COLS` constants only have `name TYPE`; they need `name TYPE OPTIONS(description="...")`.

- [ ] **For each of the 11 endpoints**, pull field descriptions from the DataForSEO v3 API docs (e.g., `https://docs.dataforseo.com/v3/backlinks/backlinks/live/`). Extract the short description per field.
- [ ] **Extend the `*_COLS` constants** in `scripts/migrate_dataforseo_manifest.py` from `col_name TYPE,` to `col_name TYPE OPTIONS(description="..."),`.
- [ ] **Also add descriptions to the metadata block** (`METADATA_BLOCK_SQL`): job_id, upload_id, ingest_timestamp, domain_id, domain, task_id, endpoint_mode — one-line each.
- [ ] **Dry-run the migration** and audit that every column in every `CREATE TABLE` has a description.
- [ ] **Update the Layer B fidelity audit workflow** (`docs/dataforseo_qa_checklist.md`) to also verify the column descriptions read correctly in the BQ Schema UI after migration.

Scope estimate: ~300 column descriptions across 11 endpoints. Mechanical but tedious. Can be parallelized across multiple subagents (one per endpoint's `*_COLS` constant).

**Why this is blocking Phase E:** column descriptions are baked in at CREATE TABLE time. Adding them after-the-fact requires ALTER TABLE for every column on every table — much more annoying than getting them right the first time.

---

## Phase E — BigQuery migration ⚠️ (you, by hand, after merge)

This is where BigQuery actually changes. Do it carefully; run dry-run first.

- [ ] **Pull the merged main and switch to it:** `git checkout main && git pull`.
- [ ] **Install the new release locally:** `uv sync --all-groups` (picks up `skyward-common==1.3.0`).
- [ ] **Dry run the migration** — writes nothing to BQ:
  ```
  uv run python scripts/migrate_dataforseo_tables.py --dry-run > /tmp/dataforseo_migration_preview.sql 2>&1
  ```
- [ ] **Audit the SQL** in `/tmp/dataforseo_migration_preview.sql`. Spot-check:
  - Every `CREATE TABLE` has the metadata block (job_id, upload_id, ingest_timestamp, domain_id, domain, task_id, endpoint_mode) first
  - `PARTITION BY DATE(ingest_timestamp)` on every table
  - `CLUSTER BY domain_id, job_id` on every table
  - `project_id` is NOT in any CREATE TABLE or INSERT column list
  - Historical defaults present (`'live' AS endpoint_mode`, `CAST(NULL AS INT64) AS domain_id`, `CAST(NULL AS STRING) AS task_id`)
  - Backup tables named `<old>_backup_04-20-2026`
- [ ] **Execute phase 1** (BACKUP + CREATE NEW + COPY DATA):
  ```
  uv run python scripts/migrate_dataforseo_tables.py --yes
  ```
  Watch the output. If anything fails partway through, the `--only <new_table_name>` flag lets you retry a single migration.
- [ ] **Verify new tables exist** in BigQuery: `bq ls data-hub-468216:DataForSEO`. Should see all 11 new canonical names plus the old names still present.
- [ ] **Spot-check row counts** match between backup and new on a few tables:
  ```
  bq query --nouse_legacy_sql --format=csv \
    "SELECT (SELECT COUNT(*) FROM \`data-hub-468216.DataForSEO.backlinks-backlinks\`) as new_n,
            (SELECT COUNT(*) FROM \`data-hub-468216.DataForSEO.backlinks_backlinks_live\`) as old_n"
  ```
- [ ] **Drop old tables** after verification:
  ```
  uv run python scripts/migrate_dataforseo_tables.py --drop-old --yes
  ```
  Aborts automatically if any row count mismatches.
- [ ] **Backups retained indefinitely** — if anything downstream breaks, recreate from `<old>_backup_04-20-2026`.

---

## Phase F — Bump downstream repos

- [ ] **In each downstream repo that uses `skyward-common`** (`skyward-seo`, `skyward-ai-faqs`, `skyward-data-hub-admin`): bump the `skyward-common` version to `1.3.0` in their `pyproject.toml`. If any caller migrations from Phase B still need coding, do them in the same PR.
- [ ] Run their test suites, open PRs, merge.

---

## Phase G — Live QA + Layer B fidelity audit

- [ ] **Seed `example.com` in `Meta.domains`** if it's not already there (live tests use it as the known-good domain):
  ```
  uv run python -c "
  from skyward.config import load_config
  from skyward.data.bigquery import BigQueryClient
  from skyward.data.meta import MetaClient
  cfg = load_config()
  bq = BigQueryClient(project_id=cfg.datahub_project_id)
  meta = MetaClient(bq)
  if meta.get_domain('example.com') is None:
      print(meta.add_domains(['example.com']))
  else:
      print('already seeded')
  "
  ```
- [ ] **Run the live suite end-to-end once:**
  ```
  uv run pytest tests/live/ -m live --run-live -v 2>&1 | tee /tmp/dataforseo_live_run.log
  ```
  Expected: 23 tests run, most complete in seconds; the SERP/search_volume `post_all` tests take up to 2 minutes. Total cost printed at end.
- [ ] **Share the total cost** (just eyeball the end of the log) — the cost-tracker fixture prints a summary by endpoint.
- [ ] **Layer B fidelity audit** (per `docs/dataforseo_qa_checklist.md`): one endpoint per review sitting. Open each `tests/live/responses/<endpoint>_*.json` alongside the BQ table, note any fields DataForSEO returns that we drop. For each field: add to the endpoint schema, or document intentional skip with reason.

---

## Phase H — Phase 2 planning (separate session)

Phase 2 adds detached mode + Cloud Run for async task retrieval. Skeleton spec lives at `docs/superpowers/specs/2026-04-20-dataforseo-detached-mode-phase-2-design.md` (local, gitignored) and the Obsidian mirror. When you're ready to pick that up, open a fresh session — Phase 2 needs operational data from Phase 1 (actual task latencies, frequency of 2h timeouts) to make good design decisions.

---

## Out of scope for this tracker

- **GCP IAM permissions review** (item #5 from the original ask). Separate conversation.
- **Orphan tables** in `DataForSEO` dataset (`chatgpt_llm_scraper`, `serp_enriched`, `serp_google_organic_live_advanced_backup`) — documented for Paul's review; untouched by migration.

---

## Quick reference

| Artifact | Path |
|---|---|
| Feature branch | `dataforseo-standardization` |
| Phase 1 spec (local) | `docs/superpowers/specs/2026-04-20-dataforseo-standardization-design.md` |
| Phase 2 skeleton (local) | `docs/superpowers/specs/2026-04-20-dataforseo-detached-mode-phase-2-design.md` |
| 6 milestone plans (local) | `docs/superpowers/plans/2026-04-20-dataforseo-m[1-6]-*.md` |
| Naming convention (tracked) | `docs/dataforseo_naming_convention.md` |
| QA checklist (tracked) | `docs/dataforseo_qa_checklist.md` |
| This doc (tracked) | `docs/dataforseo_next_steps.md` |
| Migration script | `scripts/migrate_dataforseo_tables.py` |
| Migration manifest | `scripts/migrate_dataforseo_manifest.py` |
| Live tests | `tests/live/test_dataforseo_live.py` |
| Adversarial tests | `tests/live/test_dataforseo_adversarial.py` |
| New BaseEndpoint | `src/skyward/data/dataforseo/base.py` |
| Per-endpoint classes | `src/skyward/data/dataforseo/endpoints/<name>.py` (11 files) |
