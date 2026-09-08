-- db/supabase/migrations/0006_country_language.sql
-- Country and language reference tables.
--
-- Generic by design: the table is the ISO standard, each vendor's
-- identifier for a row is a column. Adding Ahrefs/Semrush later is
-- an "alter table add column", not a new table.
--
-- Additive: creates two new tables, modifies nothing existing.
-- DDL only -- populated by its own script, per one-script-per-table.
create table meta.country (
    iso2              char(2) primary key
                          check (iso2 ~ '^[A-Z]{2}$'),
    name              text not null,
    -- DataForSEO national location_code (US = 2840, CA = 2124).
    -- Labs endpoints accept only country-level codes.
    dfs_location_code int unique,
    created_at        timestamptz not null default now()
);

comment on table meta.country is
    'ISO 3166-1 alpha-2 countries. One row per country; vendor '
    'location identifiers are columns.';

create table meta.language (
    code             text primary key,
    name             text not null,
    -- DataForSEO language_code. Usually matches ISO 639-1 but
    -- carries regional tags for some languages (zh-TW, pt-BR),
    -- so it is stored separately rather than assumed equal.
    dfs_language_code text unique,
    created_at       timestamptz not null default now()
);

comment on table meta.language is
    'ISO 639-1 languages. One row per language; vendor language '
    'identifiers are columns.';
