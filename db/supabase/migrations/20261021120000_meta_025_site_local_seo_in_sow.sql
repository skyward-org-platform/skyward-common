-- meta.site.local_seo_in_sow: the scope of work includes local SEO.
--
-- WHY
-- Adam, 2026-10-02: "if a client's scope of work includes local SEO, P0
-- should also run your intake step". P0 has to know that at step 3,
-- before a single site.gbp row exists. gbp.local_seo_in_scope is the
-- per-LISTING filter and cannot carry a decision about a site that has
-- no listing row yet. Same reasoning as national_only (meta_016): a
-- property of the site, set where there is no child row to put it on.
--
-- NULLABLE, THREE STATES, like white_label (meta_023) rather than NOT
-- NULL default false like national_only: null = nobody has ruled,
-- true = run the local lane, false = decided not to. The intake REFUSES
-- on null rather than guessing, as deliver.is_white_label does, because
-- a silent skip during P0 is the failure nobody notices until the client
-- asks where their listing work is. p0-setup start-engagement gains the
-- required pair --local-seo | --no-local-seo so the ruling is an act.
--
-- Named _in_sow, not _in_scope, so it cannot be read as the same thing
-- as site.gbp.local_seo_in_scope one hop away: this says the ENGAGEMENT
-- includes local SEO; that says WE work THIS listing.
--
-- Designed by Abdullah Sajjad (skyward-local-seo), 2026-10-09. Reviewed
-- and transcribed here 2026-10-21; the three changes made in review are
-- listed in local_001.

alter table meta.site
    add column local_seo_in_sow boolean;

comment on column meta.site.local_seo_in_sow is
    'The scope of work includes local SEO, so Phase 0 step 3b runs the '
    'local intake (local-intake queue / run). null = nobody has ruled '
    'and the intake refuses. site.gbp.local_seo_in_scope is the '
    'per-listing filter this enables.';
