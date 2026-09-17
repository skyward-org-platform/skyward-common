-- Per-person database logins, so change_log.changed_by means something.
--
-- WHY
-- Every script connects as `postgres`, so every row in
-- pipeline.change_log carries changed_by = 'postgres'. All 302 entries on
-- 2026-09-15 read identically whether they came from Adam, an agent, a
-- test, or a hand edit. changed_via (added 2026-09-15) says WHICH COMMAND
-- wrote a change and under which lane run, which is most of the value --
-- but it cannot say WHO, and attribution is the one thing that cannot be
-- backfilled later.
--
-- Adam's call, 2026-09-19. His own framing, 2026-09-16: "can identity be
-- found via the supabase account? it might be data@ so perhaps i should
-- get setup with adam@... when this is on the cloud we will have a SA so
-- it can say bot."
--
-- WHAT THIS DOES NOT DO
-- It creates the ROLES and grants them what the pipeline verbs need. It
-- does NOT set passwords and does not put credentials anywhere: those
-- belong in each worktree's .env, which is Adam's to change.
--
-- So this migration is INERT until passwords are set. Nothing breaks in
-- the meantime -- `postgres` keeps working exactly as now -- and the only
-- difference after the switch is that changed_by stops saying 'postgres'.
--
-- WHY TWO ROLES RATHER THAN ONE PER HUMAN
--   adam          a person, at a keyboard, making decisions
--   pipeline_bot  an agent or a scheduled run, executing them
--
-- That is the distinction the change log actually needs to draw. Another
-- person gets their own role the same way; the cloud service account
-- replaces pipeline_bot when it arrives.

-- NOLOGIN until a password is set, so a half-finished setup cannot leave
-- a passwordless account reachable. Guarded so re-running is safe.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'adam') then
        create role adam nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'pipeline_bot') then
        create role pipeline_bot nologin;
    end if;
end
$$;

comment on role adam is
    'A person at a keyboard. Set a password and grant LOGIN to activate; '
    'until then this role cannot connect and postgres is still used.';
comment on role pipeline_bot is
    'An agent or a scheduled run. Replaced by the cloud service account '
    'when that exists.';

-- The schemas the pipeline reads and writes, enumerated rather than
-- granted on ALL: a role that can reach anything tells you nothing when
-- it turns up in an audit.
grant usage on schema meta, brand, site, pipeline to adam, pipeline_bot;

grant select, insert, update, delete
    on all tables in schema meta, brand, site, pipeline
    to adam, pipeline_bot;

grant usage, select on all sequences in schema meta, brand, site, pipeline
    to adam, pipeline_bot;

-- Tables created later are covered too, or the next migration silently
-- locks both roles out of its new table.
alter default privileges in schema meta, brand, site, pipeline
    grant select, insert, update, delete on tables to adam, pipeline_bot;

alter default privileges in schema meta, brand, site, pipeline
    grant usage, select on sequences to adam, pipeline_bot;
