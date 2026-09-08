-- db/supabase/migrations/0005_site_competitors.sql
-- Site -> site competitor relationships (directional, manual).
-- Additive: does not modify client_domains or any existing table.
create table meta.site_competitors (
    domain_id            bigint not null references meta.domains(domain_id),
    competitor_domain_id bigint not null references meta.domains(domain_id),
    priority             text not null default 'NORMAL'
                            check (priority in ('VERY LOW','LOW','NORMAL','HIGH','VERY HIGH')),
    notes                text,
    created_at           timestamptz not null default now(),
    primary key (domain_id, competitor_domain_id),
    check (domain_id <> competitor_domain_id)
);

create index site_competitors_competitor_idx
    on meta.site_competitors (competitor_domain_id);
