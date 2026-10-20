-- 20261015120000_p3_004_sitemap.sql
--
-- The Phase 3 sitemap module (spec docs/superpowers/specs/
-- 2026-10-15-p3-sitemap-module-design.md in skyward-seo-pipeline). One
-- sitemap version per run, its pages and proposed pages, the reviews people
-- (and models) leave on it, and operator pins. Reviews and pins key on the
-- site and URL, not the version, so an approval carries forward. Every edit
-- goes to pipeline.change_log. Keyword vectors are stored once, shared by
-- every site, the sitemap and content enrichment (which today re-embeds every
-- keyword on every run); a cluster's vector is the mean of its keywords',
-- computed in memory, so it needs no table.
--
-- Apply as postgres from skyward-common's .env, with Adam's OK.

create table p3.sitemap_version (
    sitemap_version_id uuid primary key default gen_random_uuid(),
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    version       integer not null,
    builds        jsonb not null,
    settings      jsonb not null,
    counts        jsonb not null default '{}'::jsonb,
    status        text not null default 'draft'
                  check (status in ('draft','in review','approved','superseded')),
    approved_by   text,
    approved_at   timestamptz,
    note          text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
alter table p3.sitemap_version add constraint sitemap_version_key
    unique (domain_id, country_code, version);
comment on table p3.sitemap_version is 'One sitemap build for a site and country: the cluster build pinned per lane (builds), the rule settings, counts and review status. Content enrichment reads only an approved version.';

create table p3.sitemap_page (
    sitemap_page_id uuid primary key default gen_random_uuid(),
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    version       integer not null,
    url_key       text not null,
    url           text not null,
    page_type     text,
    p1_action     text,
    place         text,
    lane_key      text,
    cluster_id    integer,
    cluster_label text,
    rule          text,
    confidence    numeric,
    band          text check (band in ('high','low','no match','excluded')),
    status        text not null,
    excluded_reason text,
    recommendation jsonb,
    options       jsonb,
    verdict       text check (verdict in ('PROTECT','FIX','REBUILD')),
    cluster_strength text check (cluster_strength in ('high','medium','low')),
    primary_keyword text,
    silo_hub_url  text,
    subject_flag  jsonb,
    move          jsonb,
    overlap       jsonb,
    enrich        boolean not null default false,
    note          text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
alter table p3.sitemap_page add constraint sitemap_page_key
    unique (domain_id, country_code, version, url_key);
comment on table p3.sitemap_page is 'One page of one sitemap version: its cluster (lane, id, label) or why none, the rule that decided it, confidence and band, recommended action, verdict, primary keyword, silo hub, checks, suggested move, and whether content enrichment runs on it.';

create table p3.sitemap_new_page (
    sitemap_new_page_id uuid primary key default gen_random_uuid(),
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    version       integer not null,
    lane_key      text not null,
    cluster_id    integer not null,
    cluster_label text not null,
    suggested_path text not null,
    head_keyword  text,
    keyword_confidence numeric,
    cluster_strength text check (cluster_strength in ('high','medium','low')),
    silo_hub_url  text,
    note          text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
alter table p3.sitemap_new_page add constraint sitemap_new_page_key
    unique (domain_id, country_code, version, lane_key, cluster_id);
comment on table p3.sitemap_new_page is 'BUILD PAGE: a strong cluster with no page in one sitemap version. A proposal for an operator; no URL is created.';

create table p3.sitemap_review (
    sitemap_review_id uuid primary key default gen_random_uuid(),
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    url_key       text not null,
    url           text not null,
    round         integer not null,
    reviewer      text not null,
    reviewer_kind text not null check (reviewer_kind in ('person','model')),
    status        text not null check (status in ('approved','not approved')),
    reviewed_version integer not null,
    lane_key      text,
    cluster_id    integer,
    cluster_label text,
    cluster_keywords jsonb,
    review_note   text,
    reviewed_at   timestamptz not null,
    fix_status    text not null default 'open' check (fix_status in ('open','fixed','wont fix')),
    fix_note      text,
    fixed_in_version integer,
    note          text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
alter table p3.sitemap_review add constraint sitemap_review_key
    unique (domain_id, url_key, round, reviewer);
comment on table p3.sitemap_review is 'A verdict someone left on one page of a sitemap version (approved / not approved, with a note), keyed on the site and URL so it carries forward. Only a person''s approval locks a page; a model''s is a proposal. fix_status answers the note.';

create table p3.sitemap_pin (
    sitemap_pin_id uuid primary key default gen_random_uuid(),
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    url_key       text not null,
    lane_key      text,
    cluster_id    integer,
    cluster_label text,
    active        boolean not null default true,
    note          text not null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
alter table p3.sitemap_pin add constraint sitemap_pin_key
    unique (domain_id, country_code, url_key);
comment on table p3.sitemap_pin is 'An operator pin: this page takes this cluster (or none, when cluster_id is null) on every future sitemap version, until active=false.';

create trigger log_change after insert or update or delete on p3.sitemap_version
    for each row execute function pipeline.log_change();
create trigger log_change after insert or update or delete on p3.sitemap_page
    for each row execute function pipeline.log_change();
create trigger log_change after insert or update or delete on p3.sitemap_new_page
    for each row execute function pipeline.log_change();
create trigger log_change after insert or update or delete on p3.sitemap_review
    for each row execute function pipeline.log_change();
create trigger log_change after insert or update or delete on p3.sitemap_pin
    for each row execute function pipeline.log_change();
grant select on p3.sitemap_version to dashboard_ro;
grant select on p3.sitemap_page to dashboard_ro;
grant select on p3.sitemap_new_page to dashboard_ro;
grant select on p3.sitemap_review to dashboard_ro;
grant select on p3.sitemap_pin to dashboard_ro;

create schema if not exists vector;
create extension if not exists vector;

create table if not exists vector.keyword_embedding (
    keyword     text not null,
    model_id    text not null,
    model_dims  integer not null,
    task_type   text not null,
    embedding   vector(768) not null,
    created_at  timestamptz not null default now(),
    primary key (keyword, model_id, model_dims, task_type)
);
comment on table vector.keyword_embedding is 'One vector per keyword per model, shared by every site and lane. A re-run embeds only keywords not here.';

grant usage on schema vector to adam, pipeline_bot;
grant select, insert, update, delete on vector.keyword_embedding to adam, pipeline_bot;
