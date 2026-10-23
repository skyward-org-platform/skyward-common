-- The `local` schema: what the local SEO lane produces.
--
-- Two tables, both OUR OUTPUT rather than a client fact: GBP content
-- with an approval state (services, posts, Q&A, FAQs, offers) and
-- map-pack competitors, which are per listing and per market and may
-- have no website at all, so meta.site_competitors (keyed by domain)
-- cannot hold them.
--
-- WHY A NEW SCHEMA AND NOT `site` (Adam, 2026-10-21)
-- The design these came from put both tables in `site` beside the spine
-- and kept the gap scanner off them with a new per-table
-- client_record=False flag on the TableSpec. That reverses a decision
-- the scanner already records: gaps.is_client_record asks the SCHEMA,
-- not a list of table names, and the comment above it says why -- a
-- name list nobody remembered to update is what produced nine bogus
-- client questions on InSoFast on 2026-09-28. A schema is the existing
-- mechanism: `local` is simply not in CLIENT_RECORD_SCHEMAS, so these
-- can never become something we ask a client about, and no flag is
-- needed. It costs this grants block, which `site` would have given for
-- free, and one line adding `local` to gaps.OUTPUT_SCHEMAS so an empty
-- table is not reported as a hole in the client's record.
--
-- Keyed by (domain_id, gbp_cid, ...): a caller names the listing by cid
-- and never by uuid, so no link pass is needed, and the composite FK
-- guarantees the child's domain_id is the listing's. A listing with no
-- cid therefore holds nothing here, which is deliberate -- site_004's
-- header has the full reasoning and the readiness check that enforces
-- it.
--
-- local_content keys on `key`, not position: posts are numbered
-- ('1'..'52'); services, Q&A, FAQs and offers key on slug(title),
-- because an LLM re-run reorders a list and a positional key would swap
-- approved text under a slot. APPROVED TEXT IS IMMUTABLE: the guard
-- refuses an in-place edit of an approved or pushed row. A redraft
-- retires the row and inserts a new key.
--
-- The approval guard is the open_question close guard (pipeline_006): on
-- the transition into approved or pushed, approved_by is required and
-- approved_at is stamped.
--
-- Change log: local_content logs update and delete (pipeline_007 -- a
-- draft is reproducible from job_id, a person's edit is not).
-- local_competitor logs the person's columns only (status, in_scope,
-- notes), the pipeline_022 rule, because every re-run rewrites its
-- measured columns and logging them would repeat them. A DELETE is
-- logged on both: a removed row is nobody's measurement.
--
-- Designed by Abdullah Sajjad (skyward-local-seo), 2026-10-09. Three
-- changes in review, 2026-10-21: the schema (above), the numbering
-- (meta_022 -> meta_025, site_005 -> local_001, site_006 -> local_002,
-- since meta_022 through meta_024 are taken), and the cid requirement
-- written down as a decision rather than left implicit.

create schema if not exists local;

grant usage on schema local to adam, pipeline_bot, dashboard_ro;

alter default privileges in schema local
    grant select, insert, update, delete on tables to adam, pipeline_bot;
alter default privileges in schema local
    grant usage, select on sequences to adam, pipeline_bot;
alter default privileges in schema local
    grant select on tables to dashboard_ro;

comment on schema local is
    'What the local SEO lane produces: GBP content awaiting approval, '
    'and map-pack competitors. Our output, never the client''s record, '
    'which is why it is not `site`: the gap scanner exempts a schema, '
    'not a table list.';

create table local.local_content (
    local_content_id  uuid primary key default gen_random_uuid(),
    domain_id         bigint not null
                          references meta.site(domain_id)
                          on delete cascade,
    gbp_cid           text not null,
    kind              text not null
                          check (kind in ('service', 'post', 'qa',
                                          'faq', 'offer')),
    key               text not null,
    slot              integer check (slot is null or slot >= 1),
    title             text not null,
    body              text,
    url               text,
    summary           text,
    cta               text check (cta is null or cta in
                          ('CALL', 'LEARN_MORE', 'BOOK', 'ORDER',
                           'SHOP', 'SIGN_UP')),
    cta_url           text,
    post_type         text,
    month             smallint
                          check (month is null or month between 1 and 12),
    scheduled_for     date,
    offer_starts      date,
    offer_ends        date,
    coupon            text,
    terms             text,
    keywords          text[] not null default '{}',
    state             text not null default 'draft'
                          check (state in ('draft', 'checkpoint',
                                           'approved', 'pushed',
                                           'retired')),
    approved_by       text,
    approved_at       timestamptz,
    pushed_at         timestamptz,
    gbp_resource_name text,
    job_id            text,
    sources           text[] not null
                          constraint local_content_sources_not_empty
                          check (cardinality(sources) > 0),
    notes             text,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),

    constraint local_content_natural_key
        unique (domain_id, gbp_cid, kind, key),
    constraint local_content_listing
        foreign key (domain_id, gbp_cid)
        references site.gbp (domain_id, gbp_cid)
        on update cascade on delete cascade,
    constraint local_content_offer_fields check (
        kind = 'offer'
        or (offer_starts is null and offer_ends is null
            and coupon is null and terms is null)),
    constraint local_content_offer_dates check (
        offer_starts is null or offer_ends is null
        or offer_ends >= offer_starts)
);

create index local_content_listing_idx
    on local.local_content (domain_id, gbp_cid, kind, slot);

create or replace function local.local_content_approval_guard()
    returns trigger
    language plpgsql
as $$
begin
    if new.state in ('approved', 'pushed')
       and (tg_op = 'INSERT' or old.state is distinct from new.state) then
        if new.approved_by is null or length(trim(new.approved_by)) = 0 then
            raise exception
                'local.local_content: cannot mark %/% as % without '
                'approved_by. Say who approved it.',
                new.kind, new.key, new.state
                using errcode = 'check_violation';
        end if;
        if new.approved_at is null then
            new.approved_at := now();
        end if;
    end if;

    if tg_op = 'UPDATE'
       and old.state in ('approved', 'pushed')
       and new.state = old.state
       and (new.title, new.body, new.summary, new.cta, new.cta_url,
            new.url, new.offer_starts, new.offer_ends, new.coupon,
            new.terms)
           is distinct from
           (old.title, old.body, old.summary, old.cta, old.cta_url,
            old.url, old.offer_starts, old.offer_ends, old.coupon,
            old.terms) then
        raise exception
            'local.local_content: %/% is %; approved text is immutable. '
            'Retire it and insert a new key, or set state back to draft '
            'in the same statement.',
            new.kind, new.key, old.state
            using errcode = 'check_violation';
    end if;

    return new;
end;
$$;

comment on function local.local_content_approval_guard() is
    'On the transition into approved or pushed: require approved_by and '
    'stamp approved_at. Refuse an in-place edit of approved or pushed '
    'text; a redraft retires the row and inserts a new key. It fires '
    'only while the state is unchanged, so flipping back to draft in '
    'the same statement is the documented way to edit -- the change log '
    'is what records that somebody did.';

create trigger approval_guard
    before insert or update on local.local_content
    for each row execute function local.local_content_approval_guard();

create trigger log_change after update or delete on local.local_content
    for each row execute function pipeline.log_change();

comment on table local.local_content is
    'Repeating GBP content per listing with an approval state. key = '
    'post number for posts, slug(title) otherwise; slot is display '
    'order only. Written by local-intake under a lane; approvals come '
    'from the NAP pack (ingest-pack) or a kb-brand update by a '
    'strategist.';

comment on column local.local_content.body is
    'Service description (<= 1000, ends with the address-and-phone '
    'CTA), post body (<= 200 words), the answer, or the offer '
    'description (<= 1500). Not enforced in SQL; the SOP owns the '
    'limits.';

comment on column local.local_content.scheduled_for is
    'The post schedule: one live per week. Null until scheduled.';

create table local.local_competitor (
    local_competitor_id    uuid primary key default gen_random_uuid(),
    domain_id              bigint not null
                               references meta.site(domain_id)
                               on delete cascade,
    gbp_cid                text not null,
    location_code          integer not null,
    market_id              uuid references brand.market(market_id),
    competitor_cid         text not null,
    competitor_place_id    text,
    business_name          text,
    competitor_domain      text,
    competitor_domain_id   bigint references meta.domains(domain_id),
    website                text,
    phone                  text,
    address_snippet        text,
    primary_category       text,
    additional_categories  text[] not null default '{}',
    review_rating          numeric,
    review_count           integer,
    verified               boolean,
    total_photos           integer,
    attributes             jsonb not null default '{}'::jsonb,
    keyword_in_name        boolean,
    role                   text not null default 'competitor'
                               check (role in ('competitor', 'sibling')),
    rank                   integer,
    pack_appearances       integer,
    share_of_keywords      numeric,
    avg_pack_rank          numeric,
    best_rank              integer,
    keywords_won           integer,
    audit                  jsonb not null default '{}'::jsonb,
    as_of                  date,
    pack_job_ids           text[] not null default '{}',
    listing_source         text,
    status                 text not null default 'active'
                               check (status in ('active', 'rejected')),
    in_scope               boolean,
    job_id                 text,
    sources                text[] not null
                               constraint local_competitor_sources_not_empty
                               check (cardinality(sources) > 0),
    notes                  text,
    created_at             timestamptz not null default now(),
    updated_at             timestamptz not null default now(),

    constraint local_competitor_natural_key
        unique (domain_id, gbp_cid, location_code, competitor_cid),
    constraint local_competitor_listing
        foreign key (domain_id, gbp_cid)
        references site.gbp (domain_id, gbp_cid)
        on update cascade on delete cascade
);

create index local_competitor_listing_idx
    on local.local_competitor (domain_id, gbp_cid, location_code);

create trigger log_human_change
    after update of status, in_scope, notes on local.local_competitor
    for each row
    when (old.status is distinct from new.status
          or old.in_scope is distinct from new.in_scope
          or old.notes is distinct from new.notes)
    execute function pipeline.log_change();

-- A delete is logged separately from the column-scoped update trigger:
-- `after update of <columns>` cannot carry a DELETE, and a competitor
-- row somebody removed is the one change the measured-column exemption
-- was never meant to hide.
create trigger log_delete
    after delete on local.local_competitor
    for each row execute function pipeline.log_change();

comment on table local.local_competitor is
    'Map-pack competitors and sibling brands per client listing and per '
    'SERP market. Keyed by cid, website optional. meta.site_competitors '
    'stays the organic, domain-keyed set, bridged by '
    'competitor_domain_id.';

comment on column local.local_competitor.location_code is
    'The DataForSEO location the pack ranks came from: the BigQuery '
    'pin. market_id is the market identity (a location is not a '
    'market).';

comment on column local.local_competitor.competitor_cid is
    'Google cid. The nocid:<domain> fallback is accepted here (never on '
    'site.gbp) and flagged in notes.';

comment on column local.local_competitor.role is
    'sibling = the same client''s other brand appearing in the same '
    'packs; never a competitor.';

comment on column local.local_competitor.in_scope is
    'Ours: whether we treat them as a competitor. status is the fact '
    'about them. Both insert_only: a person''s decision survives '
    're-runs.';

comment on trigger log_human_change on local.local_competitor is
    'Logs a person''s decision (status, in_scope, notes) to '
    'pipeline.change_log. Measured columns are rewritten every run and '
    'deliberately not logged (pipeline_022); log_delete covers removal.';

grant select, insert, update, delete
    on local.local_content, local.local_competitor
    to adam, pipeline_bot;
grant select on local.local_content, local.local_competitor
    to dashboard_ro;
