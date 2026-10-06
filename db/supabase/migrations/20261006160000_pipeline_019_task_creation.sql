-- 20261006120000_pipeline_019_task_creation.sql
--
-- One on/off switch per site and phase: may a run create that phase's
-- ClickUp implementation tasks?
--
-- Agreed with Adam 2026-10-06. Until now Phase 2 kept this in its phase
-- file (clickup.create_tasks), which a person had to edit by hand, which
-- the dashboard could not see, and which left no record of who turned it
-- on or why. This moves it into the database.
--
-- THE RULES
--
--   - It belongs to the SITE (meta.site, by domain_id) and the PHASE,
--     never the client. Two sites of one client can differ.
--   - No row means OFF. A site nobody has decided about never gets tasks.
--   - module names the phase module that creates tasks. Today only
--     'phase_1_wqa' and 'phase_2_tsa' do; a new phase is added by a
--     migration that widens the check, not by writing a new name.
--
-- HISTORY IS THE CHANGE LOG
--
-- There is no history table. pipeline.log_change() is attached on insert,
-- update and delete, as pipeline_005 does for the knowledge-base tables,
-- so the first switch-on is recorded as well as every flip after it. The
-- whole row is snapshotted, so set_by and note travel with each entry.
--
-- THE DASHBOARD WRITES THROUGH ONE FUNCTION, AND NOTHING ELSE
--
-- dashboard_ro stays read only. It gets SELECT on the table and EXECUTE on
-- pipeline.set_task_creation(), which is SECURITY DEFINER: the function
-- can write this one table and nothing more, and it insists on knowing
-- who is asking.
--
-- changed_via: the function labels the transaction "dashboard
-- task-creation switch by <who>: <note>" so the change log says who and
-- why. A caller that has already labelled its own transaction or session
-- (the pipeline's `p2-tsa task-creation` verb sets its own label) keeps
-- that label: the function only fills it in when it is blank. See
-- pipeline_006 for how changed_via is read.
--
-- EXECUTE is revoked from public, so the per-person pipeline roles
-- (meta_017) are granted it alongside dashboard_ro; the pipeline's verb
-- calls the same function.
--
-- Grants: this migration grants, so apply it as postgres like the others.

create table if not exists pipeline.task_creation (
    domain_id   bigint      not null references meta.site(domain_id),
    module      text        not null
                check (module in ('phase_1_wqa', 'phase_2_tsa')),
    enabled     boolean     not null default false,
    set_by      text,
    note        text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    primary key (domain_id, module)
);

comment on table pipeline.task_creation is
    'Whether a run may create ClickUp implementation tasks for one site '
    'and phase. No row means off. Written by the dashboard and the '
    'pipeline through pipeline.set_task_creation(); history is in '
    'pipeline.change_log.';
comment on column pipeline.task_creation.domain_id is
    'The site (meta.site). The switch belongs to the site, never the client.';
comment on column pipeline.task_creation.module is
    'The phase module that creates tasks: phase_1_wqa or phase_2_tsa. A new '
    'phase is added by migration.';
comment on column pipeline.task_creation.enabled is
    'True: runs of this phase may create its ClickUp tasks for this site. '
    'False, or no row: they must not.';
comment on column pipeline.task_creation.set_by is
    'Who last set the switch: the dashboard user''s email, or the operator '
    'identity a pipeline verb ran as.';
comment on column pipeline.task_creation.note is
    'Why it was last set, in the words of whoever set it.';
comment on column pipeline.task_creation.created_at is
    'When the switch was first set for this site and phase.';
comment on column pipeline.task_creation.updated_at is
    'When the switch was last set.';

drop trigger if exists log_change on pipeline.task_creation;
create trigger log_change
    after insert or update or delete on pipeline.task_creation
    for each row execute function pipeline.log_change();

-- Default privileges on pipeline already give the dashboard SELECT on new
-- tables (see wqa_005's header). Granted explicitly as well, as tsa_001
-- and wqa_007 do, so this file does not depend on who created the table.
grant select on pipeline.task_creation to dashboard_ro;


create or replace function pipeline.set_task_creation(
    p_domain_id bigint,
    p_module    text,
    p_enabled   boolean,
    p_set_by    text,
    p_note      text
)
    returns pipeline.task_creation
    language plpgsql
    security definer
    set search_path = pg_catalog, pg_temp
as $$
declare
    result pipeline.task_creation;
begin
    if p_set_by is null or length(trim(p_set_by)) = 0 then
        raise exception
            'pipeline.set_task_creation: set_by is required. Say who is switching task creation.'
            using errcode = 'check_violation';
    end if;
    if p_enabled is null then
        raise exception
            'pipeline.set_task_creation: enabled must be true or false.'
            using errcode = 'check_violation';
    end if;

    -- Label the change log for this transaction only, unless the caller
    -- already labelled it.
    if coalesce(current_setting('app.changed_via', true), '') = '' then
        perform set_config(
            'app.changed_via',
            'dashboard task-creation switch by ' || trim(p_set_by)
                || coalesce(': ' || nullif(trim(p_note), ''), ''),
            true);
    end if;

    insert into pipeline.task_creation as t
        (domain_id, module, enabled, set_by, note)
    values
        (p_domain_id, p_module, p_enabled, trim(p_set_by), p_note)
    on conflict (domain_id, module) do update
        set enabled    = excluded.enabled,
            set_by     = excluded.set_by,
            note       = excluded.note,
            updated_at = now()
    returning t.* into result;

    return result;
end;
$$;

comment on function pipeline.set_task_creation(bigint, text, boolean, text, text) is
    'Switch ClickUp task creation on or off for one site and phase. The '
    'only write the dashboard is granted. Requires set_by; labels the '
    'change log unless the caller already did.';

revoke all on function pipeline.set_task_creation(bigint, text, boolean, text, text)
    from public;
grant execute on function pipeline.set_task_creation(bigint, text, boolean, text, text)
    to dashboard_ro, adam, pipeline_bot;
