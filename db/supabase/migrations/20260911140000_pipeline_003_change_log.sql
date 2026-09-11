-- 20260911140000_pipeline_003_change_log.sql
-- What changed in the knowledge base, when, and what it was before.
--
-- Phase 0 only ever writes into empty space, so an overwrite costs nothing
-- and no history is needed. Maintenance is the opposite: its entire purpose
-- is changing facts that are already there, and today an answer recorded in
-- Phase 0 and corrected in Phase 3 leaves no trace of the original.
--
-- `sources` records where a claim came from. It does not record that a
-- claim replaced another one, who replaced it, or when. That is the gap,
-- and it is also what we would want the day a client says "we never told
-- you that".
--
-- A TRIGGER, NOT THE VERB LAYER
--
-- Every write today goes through the p0-onboard verbs, which could log this
-- themselves in one place. But "the verbs are the only write path" is a
-- policy, not a guarantee -- a migration, a psql session or a future app
-- writes straight to the table and the log silently stops being the truth.
-- A trigger cannot be bypassed by anything that writes SQL, and a history
-- with holes in it is worse than none because it reads as complete.
--
-- FULL ROW SNAPSHOTS
--
-- before/after hold the whole row, not just the changed columns, even
-- though changed_columns names them. These tables are small -- the largest
-- is 128 market rows -- so the storage is irrelevant, and a self-describing
-- entry can be read years later without reconstructing what the schema
-- looked like at the time.
create table pipeline.change_log (
    change_id       bigserial primary key,
    domain_id       bigint,
    schema_name     text        not null,
    table_name      text        not null,
    op              text        not null
                    check (op in ('insert', 'update', 'delete')),
    changed_columns text[]      not null default '{}',
    before          jsonb,
    after           jsonb,
    changed_at      timestamptz not null default now(),
    changed_by      text        not null default current_user
);

comment on table pipeline.change_log is
    'Every insert, update and delete on a knowledge-base table, written by '
    'trigger so nothing that writes SQL can bypass it. The record of what '
    'a fact used to be, which `sources` cannot give.';

create index change_log_domain_time
    on pipeline.change_log (domain_id, changed_at desc);
create index change_log_table_time
    on pipeline.change_log (schema_name, table_name, changed_at desc);

create or replace function pipeline.log_change()
    returns trigger
    language plpgsql
as $$
declare
    old_row jsonb := case when TG_OP = 'INSERT' then null else to_jsonb(OLD) end;
    new_row jsonb := case when TG_OP = 'DELETE' then null else to_jsonb(NEW) end;
    changed text[];
begin
    if TG_OP = 'UPDATE' then
        -- Columns whose value actually moved. updated_at is excluded
        -- because it moves on every write by definition, and an entry
        -- saying only "updated_at changed" is noise that hides the real
        -- entries around it.
        select coalesce(array_agg(key order by key), '{}')
          into changed
          from jsonb_each(new_row) as n(key, value)
         where key <> 'updated_at'
           and value is distinct from (old_row -> n.key);

        -- A write that changed nothing is not a change. Re-running an
        -- upsert is idempotent by design, and logging those would bury
        -- the real history under repeat runs.
        if changed = '{}' then
            return NEW;
        end if;
    elsif TG_OP = 'INSERT' then
        select coalesce(array_agg(key order by key), '{}')
          into changed from jsonb_object_keys(new_row) as k(key);
    else
        changed := '{}';
    end if;

    insert into pipeline.change_log (
        domain_id, schema_name, table_name, op,
        changed_columns, before, after)
    values (
        coalesce((new_row ->> 'domain_id')::bigint,
                 (old_row ->> 'domain_id')::bigint),
        TG_TABLE_SCHEMA, TG_TABLE_NAME, lower(TG_OP),
        changed, old_row, new_row);

    return coalesce(NEW, OLD);
end;
$$;

comment on function pipeline.log_change() is
    'Generic change-log trigger. Skips updates that moved nothing, and '
    'ignores updated_at when deciding that.';

-- Attach to every scoped, writable knowledge-base table. pipeline.request
-- is deliberately absent: it records our own tooling complaints, not
-- client facts, and nobody needs its history.
do $$
declare
    t text;
begin
    foreach t in array array[
        'brand.brand_term', 'brand.commercial_rules',
        'brand.engagement_context', 'brand.goal', 'brand.identity',
        'brand.intake_keyword', 'brand.lead_rule', 'brand.market',
        'brand.offering', 'brand.payment_rule', 'brand.persona',
        'brand.proof_asset', 'brand.term_exclusion', 'brand.value_input',
        'brand.voice_rule', 'meta.data_access', 'meta.site',
        'meta.site_competitors', 'pipeline.open_question',
        'site.business_location', 'site.crawl_config', 'site.gbp',
        'site.structure'
    ] loop
        execute format(
            'drop trigger if exists log_change on %s', t);
        execute format(
            'create trigger log_change after insert or update or delete '
            'on %s for each row execute function pipeline.log_change()', t);
    end loop;
end;
$$;
