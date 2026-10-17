-- 20260928193000_pipeline_015_natural_keys_treat_nulls_as_equal.sql
--
-- Two natural keys that cannot match the rows they are meant to replace.
--
-- Found by the Phase 0 lane on 2026-09-28, the first time their new test
-- `test_key_null_matching_matches_the_index` met these two specs: the test
-- lived only on their branch, the specs only on main, and neither branch
-- alone held both. A real defect sitting invisibly, not one the merge
-- created -- and an argument for merging lanes sooner.
--
-- pipeline.score and pipeline.change_report both declare
-- `key_nulls_distinct=False`, meaning two NULLs in the key count as the
-- SAME value. Their live indexes are plain UNIQUE, which in Postgres means
-- NULLS DISTINCT: two NULLs count as DIFFERENT.
--
-- THE SPECS ARE RIGHT AND THE INDEXES ARE WRONG, because of how both tables
-- are written. Every write is an upsert on the natural key, and both keys
-- contain `project_id`, which is nullable and is not in either spec's
-- `required` set. With NULLS DISTINCT an ON CONFLICT on a row whose
-- project_id is NULL matches nothing, so the write INSERTS A SECOND ROW
-- instead of replacing the first:
--
--   * pipeline.score would accumulate duplicate scores for one page at one
--     version, and every read that takes the latest per page would then be
--     choosing arbitrarily between them.
--   * pipeline.change_report says in its own header that re-running the
--     same comparison overwrites it, "a second row would be a duplicate
--     rather than a history". With NULLS DISTINCT it would be exactly that
--     second row.
--
-- LATENT, NOT ACTIVE. Checked before writing this: 5,418 score rows and 3
-- change_report rows, none with a null project_id, so no duplicates exist
-- to clean up. This closes the hole before something writes into it rather
-- than after.
--
-- Numbered 015 because pipeline_014 was taken by another lane's
-- term_exclusion_default the same day, applied at 18:40. Worth noting that
-- its FILE is on neither main nor this branch, so the database currently
-- holds a migration no branch can show you.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

-- Rebuilt rather than altered: Postgres has no ALTER for the null
-- behaviour of an existing unique index.
alter table pipeline.score
    drop constraint if exists score_natural_key;
drop index if exists pipeline.score_natural_key;
alter table pipeline.score
    add constraint score_natural_key
        unique nulls not distinct (domain_id, project_id, module, version,
                                   subject_type, subject_key);

alter table pipeline.change_report
    drop constraint if exists change_report_natural_key;
drop index if exists pipeline.change_report_natural_key;
alter table pipeline.change_report
    add constraint change_report_natural_key
        unique nulls not distinct (domain_id, project_id, module,
                                   version_before, version_after);

comment on constraint score_natural_key on pipeline.score is
    'NULLS NOT DISTINCT: a score row with no project_id must still be '
    'replaced by the next run rather than duplicated.';
comment on constraint change_report_natural_key on pipeline.change_report is
    'NULLS NOT DISTINCT: re-running one comparison overwrites it, which an '
    'upsert cannot do if two null project_ids count as different rows.';
