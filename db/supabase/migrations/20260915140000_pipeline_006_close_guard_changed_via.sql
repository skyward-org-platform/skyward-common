-- 20260915140000_pipeline_006_close_guard_changed_via.sql
-- Make the audit trail say WHEN a question closed, WHO closed it, and what
-- wrote each change -- automatically, so no script or agent has to remember.
--
-- Agreed with Adam 2026-09-15 while reviewing Phase 0 against the V1 draft,
-- which says a closed row records who did it, when, what the data was, and
-- the new status. Checked against BusBank's live rows before writing this:
--
--   - answered_at was empty on all 69 closed questions. The column existed
--     and nothing ever set it. updated_at is no substitute: it moves on any
--     later edit.
--   - 20 of 69 closed questions had no answered_by.
--   - pipeline.change_log credited all 302 writes to `postgres`, the
--     database login every script shares, so it answered "what changed" and
--     never "what did it".
--
-- 1. CLOSING A QUESTION STAMPS answered_at AND REQUIRES answered_by
--
-- Enforced by trigger for the same reason the change log is: "the scripts
-- always set it" is a policy, and a direct write would break it silently.
--
-- The guard fires only on the TRANSITION into answered or dismissed, never
-- on a later edit to a row that is already closed. That matters: the 20
-- legacy rows with no answered_by are still edited by re-scans, and a guard
-- that re-checked them on every update would make the gap scan fail on data
-- nobody can now reconstruct. They stay as they are; everything closed from
-- here on carries both fields.
--
-- A caller that already knows when a question was answered may pass
-- answered_at; it is only stamped when missing.
--
-- 2. THE CHANGE LOG GAINS changed_via
--
-- Two columns, two questions:
--
--   changed_by   WHO. The database login. `postgres` today for everyone;
--                becomes a person or a bot on its own once each has a login.
--   changed_via  WHAT. The run and command that made the write, e.g.
--                "kb_maintain run v2 . kb brand.market update". Set by the
--                scripts on their database session.
--
-- The scripts set it as a session setting right after connecting. That is
-- safe here because the pooler runs in SESSION mode (port 5432): a client
-- keeps its server connection for the whole session, so one script's label
-- cannot land on another client's writes. If this ever moves to transaction
-- mode (6543), the label has to be set inside each transaction instead.
--
-- NULL when a write did not come through a labelled script -- a migration, a
-- psql session, the dashboard. That NULL is itself informative.

create or replace function pipeline.open_question_close_guard()
    returns trigger
    language plpgsql
as $$
begin
    if new.status in ('answered', 'dismissed')
       and (tg_op = 'INSERT' or old.status is distinct from new.status) then
        if new.answered_by is null or length(trim(new.answered_by)) = 0 then
            raise exception
                'pipeline.open_question: cannot mark a question % without answered_by. Say who or what closed it: a person, the client, or the command (e.g. "kb gaps").',
                new.status
                using errcode = 'check_violation';
        end if;
        if new.answered_at is null then
            new.answered_at := now();
        end if;
    end if;
    return new;
end;
$$;

comment on function pipeline.open_question_close_guard() is
    'On the transition into answered or dismissed: require answered_by, and '
    'stamp answered_at if missing. Rows already closed are not re-checked.';

drop trigger if exists close_guard on pipeline.open_question;
create trigger close_guard
    before insert or update on pipeline.open_question
    for each row execute function pipeline.open_question_close_guard();


alter table pipeline.change_log
    add column if not exists changed_via text;

comment on column pipeline.change_log.changed_via is
    'The run and command that made this write, set by the scripts on their '
    'session. NULL when the write did not come through one: a migration, a '
    'psql session, the dashboard. changed_by says who; this says what.';

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
        select coalesce(array_agg(key order by key), '{}')
          into changed
          from jsonb_each(new_row) as n(key, value)
         where key <> 'updated_at'
           and value is distinct from (old_row -> n.key);

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
        changed_columns, before, after, changed_via)
    values (
        coalesce((new_row ->> 'domain_id')::bigint,
                 (old_row ->> 'domain_id')::bigint),
        TG_TABLE_SCHEMA, TG_TABLE_NAME, lower(TG_OP),
        changed, old_row, new_row,
        nullif(current_setting('app.changed_via', true), ''));

    return coalesce(NEW, OLD);
end;
$$;
