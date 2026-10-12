-- 20261012140000_pipeline_028_request_ownership.sql
--
-- Who owns a pipeline request, in what order it should be done, and who
-- else it touches.
--
-- pipeline.request has held "things a run wants changed about the
-- pipeline, not about a client" since 2026-09-09, and it is the right
-- table for the cross-thread build list: it is deliberately not
-- domain-scoped, it closes with a status and a resolution, and every
-- thread can already read and write it through kb-brand from whatever
-- worktree it is sitting in. A markdown file cannot do that last part,
-- because a file lives on a branch and six worktrees conflict on every
-- close (Adam, 2026-10-12).
--
-- What it could not say was who is doing it, which is the whole reason
-- several threads could not share one list.
--
-- WHY PRIORITY AND IMPACT ARE BOTH HERE AND ARE NOT THE SAME THING.
-- `impact` says what the problem COSTS: blocks, slows, degrades_output,
-- cosmetic. `priority` says what we do FIRST. They come apart constantly:
-- a cosmetic wording fix in front of a client this week outranks a schema
-- gap that blocks a phase nobody is running yet. Collapsing them would
-- lose whichever question was asked second.
--
-- The four priority words are the ones pipeline_027 settled on for
-- open_question (urgent, high, normal, low). Deliberately the same
-- vocabulary: a reader should not have to learn two, and P0 to P3 is
-- retired because those strings also name our phases.
--
-- `owner` is free text, not a CHECK. The owners are threads and lanes
-- whose names change -- P0-Migration, the P3 lane, Paul, Drew -- and a
-- constraint would mean a migration every time one is renamed or a new
-- one starts. It is still a column rather than prose in `detail`, because
-- "whose is this" is the question the list exists to answer and it has to
-- be filterable.
--
-- `affects` is an array because the common case is more than one: a
-- change to work_item's columns reaches the phase that writes them, the
-- dashboard that reads them and whoever is mid-run against them. Stored
-- so a thread can find what lands on it with `affects && array['...']`
-- without reading every row.
--
-- `raised_by` is the fourth column and was not in the approved three.
-- It is here because of the rule pipeline_012 wrote down: who and when
-- are columns, never prose in a note. Half this list comes from Paul and
-- Drew rather than from a run, and "who asked for this" decides who gets
-- told when it closes. Buried in `detail` it cannot be counted or
-- answered.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

alter table pipeline.request
    add column if not exists owner      text,
    add column if not exists priority   text not null default 'normal'
                                 check (priority in ('urgent', 'high',
                                                     'normal', 'low')),
    add column if not exists affects    text[] not null default '{}',
    add column if not exists raised_by  text;

comment on column pipeline.request.owner is
    'Which thread or person is doing it. Free text, because thread names '
    'change; a column rather than a note, because this is the question '
    'the list exists to answer.';
comment on column pipeline.request.priority is
    'What we do first, which is not what `impact` says the problem costs. '
    'Same four words as pipeline.open_question.priority.';
comment on column pipeline.request.affects is
    'Other threads or people this item lands on. An array because the '
    'common case is more than one. Filter with affects && array[...].';
comment on column pipeline.request.raised_by is
    'Who asked for it: a thread, Paul, Drew, Adam. Decides who gets told '
    'when it closes.';

-- The list's own read: what is open, worst first, within an owner.
create index if not exists request_owner_priority_idx
    on pipeline.request (owner, priority)
    where status in ('open', 'accepted');

-- Updates and deletes only, as on the other pipeline tables: a first
-- write is reproducible from whoever raised it, and what needs recovering
-- is somebody's resolution being overwritten.
do $$
begin
    if not exists (select 1 from pg_trigger
                   where tgname = 'log_change'
                     and tgrelid = 'pipeline.request'::regclass) then
        create trigger log_change after update or delete on pipeline.request
            for each row execute function pipeline.log_change();
    end if;
end $$;
