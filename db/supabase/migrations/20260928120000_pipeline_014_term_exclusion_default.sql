-- 20260928120000_pipeline_014_term_exclusion_default.sql
--
-- The shared corpus denylist (Phase 3 step 1).
--
-- Terms whose keywords are removed from EVERY client's keyword corpus, at
-- every KAGG stage, so they never cost money downstream: job boards, map
-- products, marketplaces, the terms that recur in expansion output across
-- clients and never belong to any of them ("indeed", "google maps", "turo").
--
-- Per-site terms stay where Phase 0 already keeps them, in
-- brand.term_exclusion. The two lists are read together; a pattern present
-- in both is reported as the site's.
--
-- match_mode:
--   contains  the pattern appears as whole words ("turo" removes
--             "turo bus rental", never "futuro")
--   exact     the whole keyword equals the pattern
--
-- Not versioned like Phase 3 outputs. Every change is recorded by the
-- pipeline.log_change trigger instead, with the before and after values.
-- domain_id is absent, so change_log rows for this table carry a NULL
-- domain_id; the trigger handles that.
--
-- Spec: skyward-seo-pipeline
--   docs/superpowers/specs/2026-09-28-p3-step1-corpus-design.md (Denylist)
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

create table pipeline.term_exclusion_default (
    term_exclusion_default_id uuid primary key default gen_random_uuid(),
    pattern     text        not null unique,
    match_mode  text        not null default 'contains'
                check (match_mode in ('contains', 'exact')),
    category    text,
    sources     text[]      not null,
    notes       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

comment on table pipeline.term_exclusion_default is
    'Shared corpus denylist applied to every client at every KAGG stage. '
    'contains = whole-word match. Per-site terms live in brand.term_exclusion.';

comment on column pipeline.term_exclusion_default.category is
    'Why the term is here, e.g. job board, marketplace, maps. Free text.';

create trigger log_change after insert or update or delete
    on pipeline.term_exclusion_default
    for each row execute function pipeline.log_change();
