-- 20260907120200_meta_008_data_access.sql
-- Per-domain, per-tool data access. REPLACES meta.client_datasets.
--
-- client_datasets is NOT dropped here. Both tables run in parallel
-- until every consumer is repointed -- see the cutover ticket. The
-- drop is that ticket's last step, not this migration's.
--
-- Keying on domain removes the scope problem: client_datasets.domain_id
-- is nullable, and a NULL that should have been domain-scoped silently
-- contaminates a sibling territory. Here the row IS the scope. A GA4
-- property shared across three sites becomes three rows, same
-- dataset_id, different hostname.
create table meta.data_access (
    data_access_id     uuid primary key default gen_random_uuid(),
    domain_id          bigint not null
                           references meta.site(domain_id)
                           on delete cascade,
    tool               text not null
                           check (tool in
                               ('ga4','gsc','google_ads','ahrefs',
                                'screaming_frog','dataforseo','gbp',
                                'looker','facebook')),
    access_status      text not null
                           check (access_status in
                               ('granted','pending','not_applicable',
                                'revoked')),
    account_identifier text,
    property_form      text
                           check (property_form in
                               ('domain_property','url_prefix')),
    hostname           text,
    storage_platform   text not null default 'none'
                           check (storage_platform in
                               ('bigquery','supabase','none')),
    storage_project    text,
    dataset_id         text references meta.dataset_catalog(dataset),
    is_active          boolean not null default true,
    source             text not null,
    notes              text,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    unique (domain_id, tool)
);

create index data_access_dataset_idx on meta.data_access (dataset_id);

comment on column meta.data_access.storage_platform is
    'none is a real state: tool access exists but nothing is exported '
    'anywhere queryable. True of Ahrefs, Screaming Frog and '
    'DataForSEO for every client. Distinct from an unfilled row, '
    'which is access_status = pending.';

comment on column meta.data_access.property_form is
    'GSC only.';
