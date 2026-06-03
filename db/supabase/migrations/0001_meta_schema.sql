-- db/supabase/migrations/0001_meta_schema.sql
-- Meta reference layer migrated from BigQuery Meta.* dataset.
create schema if not exists meta;

create table meta.clients (
    client_id    bigint generated always as identity primary key,
    client_name  text not null,
    abbreviation text,
    is_active    boolean not null default true,
    notes        text,
    created_at   timestamptz not null default now()
);

create table meta.domains (
    domain_id   bigint generated always as identity primary key,
    domain      text not null unique,
    domain_name text,
    is_active   boolean not null default true,
    notes       text
);

create table meta.client_domains (
    client_id     bigint not null references meta.clients(client_id),
    domain_id     bigint not null references meta.domains(domain_id),
    is_competitor boolean not null default false,
    priority      text not null default 'NORMAL'
                    check (priority in ('VERY LOW','LOW','NORMAL','HIGH','VERY HIGH')),
    primary key (client_id, domain_id)
);

create table meta.projects (
    project_id   bigint generated always as identity primary key,
    client_id    bigint not null references meta.clients(client_id),
    project_type text not null,
    project_name text,
    status       text not null default 'active',
    notes        text,
    created_at   timestamptz not null default now()
);

create table meta.project_domains (
    project_id bigint not null references meta.projects(project_id),
    domain_id  bigint not null references meta.domains(domain_id),
    role       text not null default 'client',
    priority   text not null default 'NORMAL',
    primary key (project_id, domain_id)
);

create table meta.dataset_catalog (
    dataset         text primary key,
    dataset_type    text,
    hostname        text,
    is_standardized boolean default false,
    owner           text,
    active          boolean not null default true,
    updated_at      timestamptz not null default now()
);

create table meta.client_datasets (
    client_id  bigint not null references meta.clients(client_id),
    domain_id  bigint references meta.domains(domain_id),
    dataset_id text not null references meta.dataset_catalog(dataset),
    is_active  boolean not null default true,
    notes      text,
    created_at timestamptz not null default now(),
    primary key (client_id, dataset_id)
);

create table meta.table_catalog (
    dataset           text not null,
    table_name        text not null,
    row_count         bigint,
    size_bytes        bigint,
    is_active         boolean not null default true,
    status_changed_at timestamptz,
    notes             text,
    last_indexed_at   timestamptz,
    primary key (dataset, table_name)
);
