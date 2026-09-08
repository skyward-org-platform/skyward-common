-- 20260907120400_brand_001_init.sql
-- The brand namespace: the client's business knowledge.
--
-- Every table is domain-scoped and cascades from meta.site, so
-- removing a site removes its brand knowledge with it.
--
-- Table order below is FK order: market and persona precede the
-- tables that reference them.
create schema if not exists brand;

-- One row per market served. BusBank has 128.
create table brand.market (
    market_id         uuid primary key default gen_random_uuid(),
    domain_id         bigint not null
                          references meta.site(domain_id)
                          on delete cascade,
    city              text not null,
    region            text,
    country           char(2) references meta.country(iso2),
    dfs_location_code int,
    language_code     text not null default 'en'
                          references meta.language(code),
    priority_rank     int,
    tier              text,
    sub_area_tier     text,
    status            text not null default 'active'
                          check (status in
                              ('active','target','excluded')),
    has_page          boolean,
    rolls_up_regions  text[],
    code_rationale    text,
    rejected_codes    jsonb,
    ruling            text,
    source            text not null,
    notes             text,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create index market_domain_idx on brand.market (domain_id);

comment on column brand.market.country is
    'FK to meta.country. Nullable on purpose: a market whose country '
    'cannot be resolved is left null and flagged, never defaulted to '
    'the client primary country. Toronto, Montreal, Ottawa and '
    'Winnipeg are Canadian and nothing in the source says so.';

comment on column brand.market.dfs_location_code is
    'DataForSEO regional or city code, genuinely per-market. The '
    'national code lives once on meta.country.dfs_location_code, '
    'because Labs endpoints accept only country-level codes.';

comment on column brand.market.priority_rank is
    'Preserves the stored order, which the source records as roughly '
    'demand priority. A naive alphabetical migration destroys it.';

-- One row per buying audience. BusBank has 16.
create table brand.persona (
    persona_id       uuid primary key default gen_random_uuid(),
    domain_id        bigint not null
                         references meta.site(domain_id)
                         on delete cascade,
    name             text not null,
    status           text not null default 'current'
                         check (status in ('current','target')),
    role_titles      text[],
    company_types    text[],
    company_sizes    text[],
    icp_fit          text check (icp_fit in ('high','medium','low')),
    bio              text,
    jtbd             text,
    pain_points      text,
    awareness_kw     text[],
    consideration_kw text[],
    decision_kw      text[],
    source           text not null,
    notes            text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create index persona_domain_idx on brand.persona (domain_id);

comment on column brand.persona.status is
    'target is a future audience. The intent to shift lives on a '
    'brand.goal row with kind = audience_shift pointing here.';

-- What the client sells. BusBank has 19.
create table brand.offering (
    offering_id      uuid primary key default gen_random_uuid(),
    domain_id        bigint not null
                         references meta.site(domain_id)
                         on delete cascade,
    name             text not null,
    type             text check (type in
                         ('service','solution','product')),
    status           text not null default 'current'
                         check (status in ('current','retired')),
    brand_relation   text check (brand_relation in
                         ('owner','partner')),
    url              text,
    category         text,
    primary_buyer_id uuid references brand.persona(persona_id),
    source           text not null,
    notes            text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create index offering_domain_idx on brand.offering (domain_id);

comment on column brand.offering.primary_buyer_id is
    'Set by a separate linking step after both tables exist, so '
    'neither table waits on the other.';

-- Turns keyword volume into a revenue estimate.
create table brand.value_input (
    value_input_id uuid primary key default gen_random_uuid(),
    domain_id      bigint not null
                       references meta.site(domain_id)
                       on delete cascade,
    metric         text not null
                       check (metric in
                           ('aov','value_per_lead','conversion_rate')),
    value          numeric not null,
    currency       text,
    market_id      uuid references brand.market(market_id),
    offering_id    uuid references brand.offering(offering_id),
    source         text not null,
    notes          text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create index value_input_domain_idx on brand.value_input (domain_id);

comment on table brand.value_input is
    'Both links nullable deliberately. Null on both means the '
    'site-wide default; a reference narrows it. Read the most '
    'specific row that matches.';

-- One row per site.
create table brand.engagement_context (
    domain_id        bigint primary key
                         references meta.site(domain_id)
                         on delete cascade,
    marketing_budget text,
    seo_budget       text,
    code_freeze      text,
    upcoming_updates text,
    seasonality      text,
    upcoming_events  text,
    seo_history      text,
    penalty_history  text,
    source           text not null,
    notes            text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

comment on table brand.engagement_context is
    'Nothing reads this yet. Recorded because the questions are '
    'already asked and the answers currently evaporate. Free text '
    'rather than structured, because none of it drives logic today.';

-- One row per site.
create table brand.identity (
    domain_id          bigint primary key
                           references meta.site(domain_id)
                           on delete cascade,
    brand_name         text not null,
    legal_name         text,
    parent_company     text,
    founded_year       int,
    hq_location        text,
    tagline            text,
    social_profiles    text[],
    contact_name       text,
    contact_role       text,
    team_size          int,
    tooling            text[],
    franchise_model    text check (franchise_model in
                           ('franchise','independent')),
    franchisor         text,
    territory          text,
    registered_in      text,
    excluded_audience  text,
    source             text not null,
    notes              text,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

comment on column brand.identity.legal_name is
    'The name only. The holding company goes in parent_company.';

-- One row per site.
create table brand.commercial_rules (
    domain_id          bigint primary key
                           references meta.site(domain_id)
                           on delete cascade,
    business_model     text check (business_model in
                           ('service','product','marketplace')),
    sales_motion       text check (sales_motion in
                           ('sales_led','self_serve','hybrid')),
    pricing_visibility text check (pricing_visibility in
                           ('quote_only','public','tiered')),
    price_range        text,
    primary_cta        text,
    primary_kpi        text,
    source             text not null,
    notes              text,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

comment on table brand.commercial_rules is
    'Hours are not here -- they belong to a physical place, so they '
    'live on site.business_location. geographic_focus is dropped; '
    'the brand.market rows say it better.';

-- BusBank has 8.
create table brand.payment_rule (
    payment_rule_id uuid primary key default gen_random_uuid(),
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,
    name            text not null,
    detail          text not null,
    source          text not null,
    notes           text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index payment_rule_domain_idx on brand.payment_rule (domain_id);

comment on table brand.payment_rule is
    'Site level. Payment terms are usually set by the company, so '
    'sibling sites each hold a copy -- duplication accepted.';

-- Business goals, not pipeline goals.
create table brand.goal (
    goal_id      uuid primary key default gen_random_uuid(),
    domain_id    bigint not null
                     references meta.site(domain_id)
                     on delete cascade,
    statement    text not null,
    kind         text not null
                     check (kind in
                         ('market_expansion','market_growth',
                          'segment_growth','offering_growth',
                          'offering_launch','audience_shift','brand',
                          'retention','operational','constraint',
                          'avoid')),
    target_type  text check (target_type in
                     ('market','offering','persona','site','none')),
    target_id    uuid,
    metric       text,
    target_value text,
    horizon      text,
    priority     text check (priority in ('high','normal','low')),
    status       text not null default 'active'
                     check (status in
                         ('active','achieved','dropped','superseded')),
    source       text not null,
    notes        text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create index goal_domain_idx on brand.goal (domain_id);

comment on column brand.goal.target_id is
    'Soft reference resolved by target_type, so no FK. A goal should '
    'still make sense to a client who had never heard of SEO.';

-- BusBank has 46, from three source blobs.
create table brand.lead_rule (
    lead_rule_id uuid primary key default gen_random_uuid(),
    domain_id    bigint not null
                     references meta.site(domain_id)
                     on delete cascade,
    rule         text not null,
    kind         text not null
                     check (kind in
                         ('qualifies','disqualifies','not_offered')),
    source       text not null,
    notes        text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create index lead_rule_domain_idx on brand.lead_rule (domain_id);

comment on table brand.lead_rule is
    'Overlapping boundaries stay separate rows. "Looking for '
    'self-drive rental" and "Does not provide self-drive rentals" are '
    'the same boundary from two directions, used by different '
    'consumers. Already feeds KAGG not_in_scope.';

-- BusBank has 31.
create table brand.proof_asset (
    proof_asset_id uuid primary key default gen_random_uuid(),
    domain_id      bigint not null
                       references meta.site(domain_id)
                       on delete cascade,
    title          text not null,
    claim          text not null,
    type           text not null
                       check (type in
                           ('use-case proof','testimonial theme',
                            'statistic','service proof',
                            'product capability',
                            'safety / compliance proof',
                            'company milestone',
                            'rating / positioning claim',
                            'review proof','third-party credential',
                            'risk-reduction proof','product proof',
                            'customer proof','location proof',
                            'internal proof')),
    active         boolean not null default true,
    best_use       text[],
    source         text not null,
    notes          text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create index proof_asset_domain_idx on brand.proof_asset (domain_id);

comment on column brand.proof_asset.type is
    'All 15 values kept from the source. Long tail accepted; '
    're-audit after use.';

-- BusBank has 36.
create table brand.voice_rule (
    voice_rule_id uuid primary key default gen_random_uuid(),
    domain_id     bigint not null
                      references meta.site(domain_id)
                      on delete cascade,
    rule          text not null,
    rule_type     text not null
                      check (rule_type in
                          ('trait','do','dont','avoid','one_sentence',
                           'writing_style','reading_level',
                           'good_example','bad_example',
                           'length_target')),
    source        text not null,
    notes         text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index voice_rule_domain_idx on brand.voice_rule (domain_id);

comment on column brand.voice_rule.rule_type is
    'dont and avoid are different: a dont is a hard prohibition, an '
    'avoid is a soft preference. length_target repeats -- one row '
    'per page type.';

-- BusBank has 47: 37 branded, 10 exception.
create table brand.brand_term (
    brand_term_id uuid primary key default gen_random_uuid(),
    domain_id     bigint not null
                      references meta.site(domain_id)
                      on delete cascade,
    pattern       text not null,
    kind          text not null
                      check (kind in ('branded','exception')),
    match_mode    text not null default 'contains'
                      check (match_mode in ('contains','exact')),
    brand_type    text check (brand_type in
                      ('own_brand','competitor_brand')),
    brand_subtype text check (brand_subtype in
                      ('primary brand','related brand',
                       'legal/entity','parent/company')),
    reason        text,
    source        text not null,
    notes         text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index brand_term_domain_idx on brand.brand_term (domain_id);

comment on table brand.brand_term is
    'Exceptions evaluate after branded rules. Not enforced by the '
    'schema; the consumer has to.';

-- Seed keywords from kagg upload-intake.
create table brand.intake_keyword (
    intake_keyword_id uuid primary key default gen_random_uuid(),
    domain_id         bigint not null
                          references meta.site(domain_id)
                          on delete cascade,
    keyword           text not null,
    intent            text check (intent in
                          ('informational','commercial',
                           'transactional')),
    priority          text check (priority in
                          ('high','normal','low')),
    source            text not null,
    notes             text,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create index intake_keyword_domain_idx
    on brand.intake_keyword (domain_id);

comment on table brand.intake_keyword is
    'Named for the source, not the totality. Personas carry their own '
    'keywords; the full seed set exists only at discovery time when '
    'KAGG unions every source.';

-- Applied at kagg assemble, before the final keyword list.
create table brand.term_exclusion (
    term_exclusion_id uuid primary key default gen_random_uuid(),
    domain_id         bigint not null
                          references meta.site(domain_id)
                          on delete cascade,
    pattern           text not null,
    match_mode        text not null default 'contains'
                          check (match_mode in ('contains','exact')),
    source            text not null,
    notes             text,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create index term_exclusion_domain_idx
    on brand.term_exclusion (domain_id);

comment on column brand.term_exclusion.match_mode is
    'exact is the escape hatch for a term like "bank" that would '
    'otherwise destroy every branded variant.';
