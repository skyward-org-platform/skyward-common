-- 20261020120000_p3_006_ce.sql
--
-- Content enrichment reads the approved Phase 3 sitemap (spec
-- docs/superpowers/specs/2026-10-20-ce-reads-the-approved-sitemap-design.md
-- in skyward-seo-pipeline).
--
-- 1. p3.sitemap_added_page: a page an operator asked for (p3 sitemap-add-page).
--    It is approved on adding; its cluster is approved with it when the
--    operator names one, else the sitemap build looks for one (topic + path).
--    Keyed on the site and URL like pins, so it survives every rebuild.
-- 2. p3.sitemap_new_page: operator pages are written as new pages
--    (origin='operator'), possibly with no cluster yet, and carry the build's
--    candidate clusters (options) and its recommendation, as existing pages do.
-- 3. p3.enrichment_page: the dashboard's copy of content enrichment, one row
--    per page holding its latest result (the Content enrichment tab).
--
-- Apply as postgres from skyward-common's .env, with Adam's OK.

create table p3.sitemap_added_page (
    sitemap_added_page_id uuid primary key default gen_random_uuid(),
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    url_key       text not null,
    path          text not null check (path like '/%'),
    topic         text,
    lane_key      text,
    cluster_id    integer,
    cluster_label text,
    cluster_keywords jsonb,
    parent        text,
    added_by      text not null,
    note          text not null,
    active        boolean not null default true,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    check ((lane_key is null) = (cluster_id is null))
);
alter table p3.sitemap_added_page add constraint sitemap_added_page_key
    unique (domain_id, country_code, url_key);
comment on table p3.sitemap_added_page is 'A page an operator asked for. Approved on adding; its cluster (lane, id, label, members) is approved with it when given, else the sitemap build proposes one for review.';

alter table p3.sitemap_new_page alter column lane_key drop not null;
alter table p3.sitemap_new_page alter column cluster_id drop not null;
alter table p3.sitemap_new_page alter column cluster_label drop not null;
alter table p3.sitemap_new_page add column origin text not null default 'proposed'
    check (origin in ('proposed','operator'));
alter table p3.sitemap_new_page add column options jsonb;
alter table p3.sitemap_new_page add column recommendation jsonb;
comment on column p3.sitemap_new_page.origin is 'proposed: the build proposed it. operator: an operator added it (p3.sitemap_added_page); it may have no cluster yet.';

create table p3.enrichment_page (
    enrichment_page_id uuid primary key default gen_random_uuid(),
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    url_key       text not null,
    url           text not null,
    is_new        boolean not null,
    sitemap_version integer not null,
    build_id      text not null,
    lane_key      text not null,
    cluster_id    integer not null,
    cluster_label text,
    category      text,
    primary_keyword text,
    secondary_keywords jsonb,
    metrics       jsonb,
    current       jsonb,
    recommended   jsonb,
    subtopics     jsonb,
    modifier_angles jsonb,
    faqs          jsonb,
    shared_competitor_urls jsonb,
    client_in_top5 boolean,
    competitors_in_top5 jsonb,
    named_entities jsonb,
    generic_entities jsonb,
    anchor_plan   jsonb,
    inbound_links jsonb,
    ce_job_id     text not null,
    enriched_at   timestamptz not null default now(),
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
alter table p3.enrichment_page add constraint enrichment_page_key
    unique (domain_id, country_code, url_key);
comment on table p3.enrichment_page is 'Content enrichment for one page of the approved sitemap, its latest result: the same fields as the delivered Sheet. Read by the dashboard''s Content enrichment tab.';

create trigger log_change after insert or update or delete on p3.sitemap_added_page
    for each row execute function pipeline.log_change();
create trigger log_change after insert or update or delete on p3.enrichment_page
    for each row execute function pipeline.log_change();

grant select on p3.sitemap_added_page to dashboard_ro;
grant select on p3.enrichment_page to dashboard_ro;
grant select, insert, update, delete on p3.sitemap_added_page to adam, pipeline_bot;
grant select, insert, update, delete on p3.enrichment_page to adam, pipeline_bot;
