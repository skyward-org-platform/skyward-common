-- 20261005120000_keyword_analysis_001_dashboard_cache.sql
--
-- Phase 3 (Keyword & Prompt Analysis) results, cached for the operator
-- dashboard. Schema keyword_analysis (Adam, 2026-10-05: named for the
-- phase, not "p3", as Phase 1 is wqa).
--
-- WHY THIS EXISTS
--
-- Phase 3's bulk data lives in BigQuery, its system of record (V1
-- milestone). The dashboard reads Supabase only, never BigQuery, because a
-- page must be fast. So the step that builds the Phase 3 workbook also
-- writes the same rows here, from the same frames: the workbook, the
-- dashboard and BigQuery cannot disagree (Adam, 2026-10-05).
--
-- Spec: skyward-seo-pipeline docs/superpowers/specs/2026-10-05-p3-dashboard-design.md
--
-- VERSIONED PER BUILD. Every table keys on build_id; a build is never
-- overwritten, so an older workbook's view stays readable. Readers take the
-- newest build per site + country. The writer prunes to the last 3 builds
-- per site + country; deleting a keyword_analysis.build row cascades.
--
-- One exception, by decision: the full LOCAL search results per market are
-- NOT cached (busbank at full scale would be ~44M rows). A keyword page
-- loads them from BigQuery on demand. National results are cached.
--
-- READ ONLY for the dashboard, as wqa_005 set for Phase 1: dashboard_ro
-- gets usage and select, never insert/update/delete.
--
-- Apply as postgres (it creates a schema and grants).

create schema if not exists keyword_analysis;

-- ---------------------------------------------------------------------
-- One build of one country's Keywords & Clusters workbook.
-- ---------------------------------------------------------------------
create table keyword_analysis.build (
    build_id        uuid primary key default gen_random_uuid(),
    domain_id       bigint not null
                        references meta.site(domain_id) on delete cascade,
    project_id      bigint not null,
    country_code    integer not null,
    country_name    text not null,
    built_at        timestamptz not null default now(),
    -- The Drive file, when it was filed.
    workbook_file_id text,
    workbook_url    text,
    -- The workbook's Summary tab rows: [{"setting": ..., "value": ...}].
    summary         jsonb not null default '[]'::jsonb,
    -- The taxonomy layers as shown: [{"layer": ..., "values": [...]}].
    taxonomy        jsonb not null default '[]'::jsonb
);
create index build_site_country_built
    on keyword_analysis.build (domain_id, country_code, built_at desc);

-- ---------------------------------------------------------------------
-- A lane (the national set, or one market) in a build.
-- ---------------------------------------------------------------------
create table keyword_analysis.lane (
    build_id        uuid not null references keyword_analysis.build(build_id) on delete cascade,
    -- 'national' or the brand.market market_id.
    lane_key        text not null,
    market_id       text,
    name            text not null,
    tier            text,
    codes           integer[] not null default '{}',
    cluster_version integer,
    cluster_job_id  text,
    search_volume_status text,
    n_clusters      integer,
    n_keywords      integer,
    primary key (build_id, lane_key)
);

-- ---------------------------------------------------------------------
-- A cluster in a lane. Cluster ids are 0-based, as in BigQuery.
-- ---------------------------------------------------------------------
create table keyword_analysis.cluster (
    build_id        uuid not null,
    lane_key        text not null,
    cluster_id      integer not null,
    representative  text not null,
    n_keywords      integer not null,
    -- Google Ads, close variants counted once; NULL until step 10 ran.
    search_volume   bigint,
    raw_total       bigint,
    dedup_groups    integer,
    partially_corrected boolean,
    avg_relevancy   numeric,
    avg_opportunity numeric,
    -- The most common value per taxonomy layer: {"<layer>": "<value>"}.
    layer_tops      jsonb not null default '{}'::jsonb,
    geo_share       numeric,
    informational_share numeric,
    generic_share   numeric,
    client_best_rank integer,
    primary key (build_id, lane_key, cluster_id),
    foreign key (build_id, lane_key) references keyword_analysis.lane(build_id, lane_key)
        on delete cascade
);

-- ---------------------------------------------------------------------
-- Every member of every cluster. Searching the clusters list for a keyword
-- matches here, at any position, so truncating a long member list in the
-- page never hides a match.
-- ---------------------------------------------------------------------
create table keyword_analysis.cluster_member (
    build_id        uuid not null,
    lane_key        text not null,
    cluster_id      integer not null,
    keyword         text not null,
    -- Order within the cluster: by the lane's volume, highest first.
    position        integer not null,
    -- The lane's Google Ads figure; NULL with a label when there is none.
    search_volume   bigint,
    volume_label    text,         -- 'close variant' | 'no data' | 'not pulled'
    sv_group        integer,
    counted         boolean,
    local_rank      integer,
    primary key (build_id, lane_key, cluster_id, keyword),
    foreign key (build_id, lane_key, cluster_id)
        references keyword_analysis.cluster(build_id, lane_key, cluster_id) on delete cascade
);
create index cluster_member_keyword_trgm
    on keyword_analysis.cluster_member using gin (keyword public.gin_trgm_ops);
create index cluster_member_keyword
    on keyword_analysis.cluster_member (build_id, keyword);

-- ---------------------------------------------------------------------
-- A corpus keyword in a build: the Keywords-tab columns typed for sorting
-- and filtering, and every detail we hold as JSON for the keyword page.
-- ---------------------------------------------------------------------
create table keyword_analysis.keyword (
    build_id        uuid not null references keyword_analysis.build(build_id) on delete cascade,
    keyword         text not null,
    bucket          text,
    national_cluster_id integer,
    national_cluster text,
    national_sv_ads bigint,
    national_sv_ads_label text,   -- 'close variant' | 'no data' | 'not pulled'
    national_sv_labs bigint,
    keyword_difficulty numeric,
    intent          text,
    detected_place  text,
    relevancy       numeric,
    opportunity     numeric,
    keyword_gate    text,
    local_gate      text,
    local_gate_reason text,
    client_rank_national integer,
    best_competitor_rank_national integer,
    source          text,
    -- {"<layer>": "<value>"}
    labels          jsonb not null default '{}'::jsonb,
    -- The full rows behind the keyword page: keyword_facts, the categorize
    -- vote (distributions, confidence, skip reason, intent summary),
    -- keyword_scores with every intermediate, and the DFS ranked-keywords
    -- rankings for client and competitors.
    facts           jsonb not null default '{}'::jsonb,
    categorize      jsonb not null default '{}'::jsonb,
    scores          jsonb not null default '{}'::jsonb,
    rankings        jsonb not null default '[]'::jsonb,
    primary key (build_id, keyword)
);
create index keyword_trgm on keyword_analysis.keyword using gin (keyword public.gin_trgm_ops);

-- ---------------------------------------------------------------------
-- A keyword in a market: its cluster there and its local figures.
-- ---------------------------------------------------------------------
create table keyword_analysis.keyword_market (
    build_id        uuid not null,
    keyword         text not null,
    lane_key        text not null,
    cluster_id      integer not null,
    search_volume   bigint,
    volume_label    text,
    monthly_searches jsonb,
    local_rank      integer,
    local_pack_rank integer,
    primary key (build_id, keyword, lane_key),
    foreign key (build_id, lane_key) references keyword_analysis.lane(build_id, lane_key)
        on delete cascade
);

-- ---------------------------------------------------------------------
-- The national search results per keyword (local ones load on demand).
-- ---------------------------------------------------------------------
create table keyword_analysis.keyword_serp (
    build_id        uuid not null references keyword_analysis.build(build_id) on delete cascade,
    keyword         text not null,
    rank            integer not null,
    url             text not null,
    domain          text,
    role            text,         -- 'client' | 'competitor' | 'other'
    primary key (build_id, keyword, rank, url)
);

-- The incumbents' authority, from competition scoring, once per URL.
create table keyword_analysis.url_authority (
    build_id        uuid not null references keyword_analysis.build(build_id) on delete cascade,
    url             text not null,
    domain_rank     numeric,
    backlinks       bigint,
    referring_domains bigint,
    primary key (build_id, url)
);

-- ---------------------------------------------------------------------
-- Every Phase 3 step's state per country (and market), mirrored by each p3
-- command from the site YAML. Drives the progress bars, Costs and Config.
-- Not per build: it is the live state, upserted in place.
-- ---------------------------------------------------------------------
create table keyword_analysis.step_status (
    domain_id       bigint not null
                        references meta.site(domain_id) on delete cascade,
    project_id      bigint,
    country_code    integer not null,
    -- '' for a country-level step, else the market_id (or 'national' for
    -- the national lane of a per-lane step).
    lane_key        text not null default '',
    step            text not null,
    status          text,
    job_id          text,
    version         integer,
    estimate_upper_usd numeric,
    estimate_expected_usd numeric,
    actual_usd      numeric,
    detail          jsonb not null default '{}'::jsonb,
    updated_at      timestamptz not null default now(),
    primary key (domain_id, country_code, lane_key, step)
);

-- READ ONLY for the dashboard.
grant usage on schema keyword_analysis to dashboard_ro;
grant select on all tables in schema keyword_analysis to dashboard_ro;
alter default privileges in schema keyword_analysis grant select on tables to dashboard_ro;
