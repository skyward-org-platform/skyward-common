-- 20261014120000_p3_001_operator_decisions.sql
--
-- Phase 3 operator decisions (spec docs/superpowers/specs/
-- 2026-10-14-p3-lanes-and-maintenance-design.md in skyward-seo-pipeline).
-- Every change an operator makes to a site's Phase 3 (corpus keywords,
-- prompts, categories, clusters, setup confirmations) is a row here, scoped
-- to one site, and every edit goes to pipeline.change_log (trigger),
-- labelled with the lane and p3 command that made it. Not keyword_analysis:
-- that schema is a dashboard cache pruned to the last three builds.
--
-- Apply as postgres from skyward-common's .env, with Adam's OK.

create schema if not exists p3;
grant usage on schema p3 to dashboard_ro;

create table p3.keyword_override (
    keyword_override_id bigserial primary key,
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    keyword       text not null check (keyword = lower(btrim(keyword)) and keyword <> ''),
    action        text not null check (action in ('add','remove')),
    active        boolean not null default true,
    note          text not null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create unique index keyword_override_uniq on p3.keyword_override (domain_id, country_code, keyword);
comment on table p3.keyword_override is 'A keyword an operator added to (action=add) or removed from (action=remove) a site''s Phase 3 corpus, per country. active=false undoes it without deleting. Removals join the denylist (exact match, list source operator); additions join the Phase 0 seeds.';
create trigger log_change after insert or update or delete on p3.keyword_override
    for each row execute function pipeline.log_change();
grant select on p3.keyword_override to dashboard_ro;

create table p3.prompt (
    prompt_row_id bigserial primary key,
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    prompt_key    text not null,
    prompt        text not null check (btrim(prompt) <> ''),
    topic         text,
    source        text not null check (source in ('peec','generated','operator')),
    status        text not null default 'active' check (status in ('active','removed')),
    reviewed_at   timestamptz,
    note          text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create unique index prompt_uniq on p3.prompt (domain_id, country_code, prompt_key);
comment on table p3.prompt is 'A site''s prompt set (questions people ask AI assistants), replacing configs/p3/*_prompt_set_*.json. status=removed keeps the row. A fan-out pull needs every active prompt reviewed (reviewed_at set).';
create trigger log_change after insert or update or delete on p3.prompt
    for each row execute function pipeline.log_change();
grant select on p3.prompt to dashboard_ro;

create table p3.category_override (
    category_override_id bigserial primary key,
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    country_code  integer not null,
    keyword       text not null check (keyword = lower(btrim(keyword)) and keyword <> ''),
    layer         text not null,
    value         text not null,
    active        boolean not null default true,
    note          text not null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create unique index category_override_uniq on p3.category_override (domain_id, country_code, keyword, layer);
comment on table p3.category_override is 'One keyword''s category in one taxonomy layer, set by hand. Categorize applies it after the model, on every vote run, with categorization_method=operator.';
create trigger log_change after insert or update or delete on p3.category_override
    for each row execute function pipeline.log_change();
grant select on p3.category_override to dashboard_ro;

create table p3.cluster_override (
    cluster_override_id bigserial primary key,
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    build_id      uuid not null,
    lane_key      text not null default 'national',
    kind          text not null check (kind in ('rename','move')),
    cluster_id    integer not null,
    keyword       text,
    label         text,
    active        boolean not null default true,
    note          text not null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    check ((kind = 'rename' and label is not null and keyword is null)
        or (kind = 'move' and keyword is not null and label is null))
);
create unique index cluster_override_uniq on p3.cluster_override
    (domain_id, build_id, lane_key, kind, coalesce(keyword, ''), cluster_id);
comment on table p3.cluster_override is 'A cluster label rename (kind=rename, cluster_id + label) or a keyword moved into a cluster (kind=move, keyword -> cluster_id), for ONE build only: cluster numbers change every clustering run.';
create trigger log_change after insert or update or delete on p3.cluster_override
    for each row execute function pipeline.log_change();
grant select on p3.cluster_override to dashboard_ro;

create table p3.decision (
    decision_id   bigserial primary key,
    domain_id     bigint not null references meta.site(domain_id) on delete cascade,
    decision_key  text not null check (decision_key ~ '^[a-z_]+(\.[0-9a-z_]+)*$'),
    value         jsonb not null,
    note          text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create unique index decision_uniq on p3.decision (domain_id, decision_key);
comment on table p3.decision is 'A Phase 3 setup decision that also lives in the site''s P3 YAML, keyed by its YAML path (e.g. competitors.2840, centrality.2840, local_gate, categorize.2840, slug_review.2840). The database row wins: p3 copies it into the YAML before every command.';
create trigger log_change after insert or update or delete on p3.decision
    for each row execute function pipeline.log_change();
grant select on p3.decision to dashboard_ro;
