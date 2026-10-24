-- 20261008230000_pipeline_022_work_item_group.sql
--
-- Actions, grouped, stored. Spec: skyward-seo-pipeline
-- docs/superpowers/specs/2026-10-08-central-actions-tab-notes.md
-- (Adam and Paul, call of 2026-10-08).
--
-- An audit finds instances; a task ships rules. Phase 1 already collapses
-- instances into rules when it writes ClickUp tasks -- eight slices today,
-- keyed on WHAT IS ACTUALLY WRONG rather than on the triage verdict, so
-- "redirect landing on a dead destination" and "multi-hop chain to
-- collapse" are different jobs even though both are Redirect. That
-- grouping existed only in memory at push time and was thrown away
-- afterwards, so Supabase held work items and URL lists but no rule
-- structure, and the dashboard could only show raw per-URL rows.
--
-- This persists it, so the dashboard and ClickUp read ONE grouping rather
-- than each computing its own. Two implementations of one grouping rule is
-- how this phase has drifted before.
--
-- THE NATURAL KEY IS (work_item_id, slice_key), matching
-- pipeline.work_item_task. A slice_key is the stable identity; the number
-- a task displays (P1.1.2) is positional over the non-empty slices and
-- renumbers when a slice empties, so it is never an identity.

create table if not exists pipeline.work_item_group (
    work_item_group_id  uuid primary key default gen_random_uuid(),
    domain_id           bigint not null,
    work_item_id        uuid not null
        references pipeline.work_item(work_item_id) on delete cascade,
    slice_key           text not null,
    name                text not null,
    position            integer not null,
    url_count           integer not null,
    high_priority_count integer not null default 0,
    size_band           text not null
        check (size_band in ('normal', 'judgment', 'oversize')),
    split_reason        text not null
        check (split_reason in ('reason', 'priority', 'section', 'whole')),
    priority_band       text
        check (priority_band is null or priority_band in ('high', 'rest')),
    note                text,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    unique (work_item_id, slice_key)
);

create index if not exists work_item_group_domain_idx
    on pipeline.work_item_group (domain_id);
create index if not exists work_item_group_item_idx
    on pipeline.work_item_group (work_item_id);

comment on table pipeline.work_item_group is
    'One grouped action: the rule, not the instances. A work item with no '
    'rows here is one job that does not slice.';

comment on column pipeline.work_item_group.slice_key is
    'The group''s stable identity, e.g. dead_destination, '
    'chain_to_collapse, redirect_loop. Matches the slice_key on '
    'pipeline.work_item_task so a stored group and a pushed ClickUp task '
    'reconcile on the same value.';

comment on column pipeline.work_item_group.position is
    'Display order only. NOT an identity: it renumbers when a sibling '
    'slice empties, which is why InSoFast''s children went from three to '
    'four when the measured-chain fix landed.';

comment on column pipeline.work_item_group.high_priority_count is
    'Pages in this group at a high tier (Revenue-Critical, Page 1, '
    'Striking Distance, Core Page). Recorded rather than derived so the '
    'split that produced this group can be audited against the data as it '
    'was, not as it is now.';

comment on column pipeline.work_item_group.size_band is
    'normal under 500 URLs; judgment 500-1000, where Adam asked for a '
    'human call rather than an automatic split; oversize above 1000, '
    'which must be split unless `note` says why it could not be. Bands '
    'agreed with Adam 2026-10-08 against the real distribution: median '
    'work item is 123 URLs, p90 is 957, largest 1358.';

comment on column pipeline.work_item_group.split_reason is
    'Why this group exists apart from its siblings. `reason` is the '
    'default and the only one the task template really wants: a different '
    'job done by different means. `priority` is a DELIBERATE EXCEPTION, '
    'carved 2026-10-08 -- clickup_tasks.py says a child is a slice and not '
    'a priority, because splitting by priority makes two tasks doing the '
    'same job. Adam and Paul overrode that for volume: 40 high-priority '
    'pages beside 500 low-priority ones are scheduled differently even '
    'though the work is identical, while 1 beside 15 are not. The '
    'threshold is 25. `section` splits an oversize group by URL path, '
    'which is how a developer implements it. `whole` means it was left '
    'intact and `note` says why.';

comment on column pipeline.work_item_group.note is
    'Why this group is as it is, where that needs saying -- above all, why '
    'an oversize group was NOT split. A thousand URLs sharing one pattern '
    'are one regex rule; chopping them into "part 1 of 4" would be four '
    'people writing four versions of one rule, which is the duplicate work '
    'the whole grouping exists to prevent.';

-- WHICH GROUP EACH URL IS IN. Nullable: a URL on a work item that does not
-- slice has no group, and every row written before this migration has none.
alter table pipeline.work_item_url
    add column if not exists group_slice_key text;

comment on column pipeline.work_item_url.group_slice_key is
    'The pipeline.work_item_group this URL belongs to, by slice_key within '
    'the same work item. Null where the work item is one job, or where the '
    'row predates grouping.';
