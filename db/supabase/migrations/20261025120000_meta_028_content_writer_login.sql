-- A content writer's database login (Nikhil), for the skyward-skills
-- seo-pipeline plugin's Brand DNA and Phase 3 read skills.
--
-- WHY
-- The content team reads a client's Brand DNA, Phase 3 sitemap and content
-- enrichment, and corrects Brand DNA, through the plugin (no pipeline
-- checkout). Their writes must be attributed to them in pipeline.change_log,
-- and they must not be able to write meta.* -- above all meta.data_access --
-- or any Phase 1-3 output. kb-brand's generic verbs will write any registry
-- table not flagged read-only, so this role is the real guard, not the
-- skill text (spec: docs/specs/2026-10-21-pipeline-skills-design.md in
-- skyward-skills).
--
-- WHAT IT GRANTS
--   write   brand.*, site.* (the Brand DNA tables) and nothing else
--   read    the meta tables the verbs join, every per-site table that
--           kb-brand readiness and run finish select, and the Phase 3 /
--           content enrichment tables the read skills use
--   log     INSERT on pipeline.change_log (log_change() runs as the
--           caller) and UPDATE of a progress row's closing columns
--
-- INERT until LOGIN and a password are set by hand (as meta_017 does):
--   alter role nikhil with login password '...';
-- A per-site table added by a later migration must be granted SELECT here
-- too, or kb-brand readiness / run finish fail for this role.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'nikhil') then
        create role nikhil nologin;
    end if;
end
$$;

comment on role nikhil is
    'Content writer (seo-pipeline plugin). Brand DNA read/write, Phase 3 '
    'read. No meta writes, no DDL. Set LOGIN and a password to activate.';

grant usage on schema
  meta, brand, site, pipeline,
  p3, tsa, wqa, local
  to nikhil;

-- Brand DNA: the only tables he writes.
grant select, insert, update, delete
  on all tables in schema brand, site
  to nikhil;
alter default privileges in schema brand, site
  grant select, insert, update, delete
  on tables to nikhil;

-- meta: read only. data_access is read by
-- gaps, readiness and run finish.
grant select on
  meta.site, meta.domains, meta.clients,
  meta.projects, meta.project_domains,
  meta.site_competitors, meta.data_access,
  meta.dataset_catalog
  to nikhil;

-- log_change() is SECURITY INVOKER: the
-- writer inserts the log row itself.
grant select, insert
  on pipeline.change_log to nikhil;
grant usage
  on sequence pipeline.change_log_change_id_seq
  to nikhil;

-- run finish/cancel close the progress row.
grant select on pipeline.run_progress
  to nikhil;
grant update (status, finished_at, error)
  on pipeline.run_progress to nikhil;

-- readiness and run finish read every
-- per-site registry table.
grant select on
  pipeline.open_question,
  pipeline.work_item,
  pipeline.deliverable,
  pipeline.score,
  pipeline.work_item_url,
  pipeline.change_report,
  pipeline.change_finding,
  pipeline.work_item_check,
  pipeline.work_item_outcome,
  pipeline.work_item_task,
  pipeline.work_item_group,
  pipeline.work_item_expectation
  to nikhil;

-- run finish's "added" counts.
grant select on
  pipeline.page_snapshot,
  pipeline.page_snapshot_row,
  pipeline.request,
  pipeline.work_item_rice_override
  to nikhil;

grant select on wqa.page_verdict to nikhil;
grant select on tsa.target, tsa.check_result
  to nikhil;
grant select on
  p3.keyword_override, p3.prompt,
  p3.category_override,
  p3.cluster_override, p3.decision,
  p3.sitemap_version, p3.sitemap_page,
  p3.sitemap_new_page, p3.sitemap_review,
  p3.sitemap_pin, p3.sitemap_added_page,
  p3.enrichment_page
  to nikhil;
grant select on
  local.local_content,
  local.local_competitor,
  local.gbp_push
  to nikhil;
