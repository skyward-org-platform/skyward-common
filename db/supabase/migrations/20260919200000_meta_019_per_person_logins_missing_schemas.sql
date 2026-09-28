-- Grant the per-person roles the schemas meta_017 missed.
--
-- WHY
-- 20260919160000_meta_017_per_person_logins granted USAGE on four schemas:
-- meta, brand, site, pipeline. There are nine. Reported by the
-- P1-P2-Migration thread, who hit it directly:
--
--     scripts/generate_table_specs.py  -> permission denied for schema wqa
--     kb wqa.page_verdict <anything>   -> same
--     Phase 1 step 4 writes verdicts into wqa.page_verdict, so it is blocked
--
-- Measured as `adam` before this migration:
--
--     brand               USAGE  yes
--     meta                USAGE  yes
--     pipeline            USAGE  yes
--     site                USAGE  yes
--     vector              USAGE  NO
--     wqa                 USAGE  NO
--     knowledge_base_dev  USAGE  NO
--     skillshare          USAGE  NO
--     skillshare_dev      USAGE  NO
--
-- WHY ONLY TWO OF THE FIVE
-- wqa and vector are pipeline schemas: wqa holds Phase 1 page verdicts,
-- vector holds the embedding tables. Both are ours and both are reached by
-- pipeline code, so both belong.
--
-- knowledge_base_dev, skillshare and skillshare_dev are deliberately left
-- alone. They are not reached by any pipeline code, nobody has hit them,
-- and granting a login role access to a schema because it happens to exist
-- is how a role ends up able to touch everything. If one of them turns out
-- to be in scope, that is its own decision and its own migration.
--
-- THE REAL LESSON, WHICH THIS MIGRATION DOES NOT FIX
-- An enumerated schema list is one new schema away from being wrong, and
-- wqa is exactly that case: it did not exist when meta_017 was written.
-- The P1-P2-Migration thread hit the same shape earlier the same day in
-- kb's argparse, which kept its own hardcoded copy of the lane list and
-- silently rejected a lane that had been registered.
--
-- The answer is NOT "grant on every schema" -- that is how the three above
-- would get swept in. It is that this list is a DECISION, and adding a
-- schema means revisiting it. Anyone creating a schema the pipeline reads
-- or writes has to grant it here, and a role that cannot reach a new
-- schema will say so loudly (permission denied) rather than silently, which
-- is the failure mode we want.

grant usage on schema wqa, vector to adam, pipeline_bot;

grant select, insert, update, delete
    on all tables in schema wqa, vector
    to adam, pipeline_bot;

grant usage, select on all sequences in schema wqa, vector
    to adam, pipeline_bot;

-- Tables created later are covered too, or the next migration in either
-- schema silently locks both roles out of its new table.
alter default privileges in schema wqa, vector
    grant select, insert, update, delete on tables to adam, pipeline_bot;

alter default privileges in schema wqa, vector
    grant usage, select on sequences to adam, pipeline_bot;
