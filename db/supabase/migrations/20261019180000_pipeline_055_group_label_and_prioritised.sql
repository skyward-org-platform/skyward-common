-- pipeline_055: which group an action belongs to, and whether somebody
-- has put it in the plan.
--
-- Paul asked for both on 2026-10-18 and asked again the same morning,
-- because his Strategy and Planning build needs them: Findings and
-- Planning show the same five groups so the two match one to one, and
-- Planning's Work view is empty until an item can be marked chosen.
-- Adam's answer, 2026-10-19: write it.
--
-- THE TWO ARE NOT THE SAME KIND OF FACT, which is why one is a table
-- and the other is two columns.
--
-- A GROUP IS A PROPERTY OF THE ACTION TYPE, not of the item. Every
-- p2-page-speed item on every site belongs to Technical development,
-- and always will. Stored per item it would be 51 rows to fill today,
-- every phase would need code to write it, Paul's view would show
-- nothing until they all did, and two items of the same type could
-- disagree. So it is declared once per (module, action_type), seeded
-- below, and joined. A new action type needs one row, which is a
-- reviewed diff rather than a column somebody forgot to set.
--
-- BEING CHOSEN IS A DECISION ABOUT ONE ITEM, so it goes on the item.
-- And it is somebody's decision, so the column records who made it and
-- when rather than a bare boolean: "who put this in the plan" is the
-- first question anybody asks of a plan, and a flag cannot answer it.
-- `prioritised_at IS NOT NULL` is the flag, so the two can never
-- disagree the way a boolean beside a timestamp can.
--
-- Grants: the two new tables get theirs explicitly. The view is
-- CREATE OR REPLACE with the new columns APPENDED, which keeps its
-- existing ACL including dashboard_ro -- a drop and recreate would
-- silently take the dashboard's read away.

-- ---------------------------------------------------------------------
-- pipeline.action_group: the five groups, declared per action type.
-- ---------------------------------------------------------------------
create table if not exists pipeline.action_group (
    module text not null,
    action_type text not null,

    -- The five Paul and Drew locked on 2026-10-18. Slugs rather than
    -- display text, because the dashboard groups on this and a label
    -- somebody retypes as "Technical Development" would become a sixth
    -- group nobody notices.
    group_label text not null
        check (group_label in ('organic_content', 'technical_development',
                               'local', 'aeo', 'authority')),

    -- Why this type sits in this group. Several of these are
    -- judgements, and a judgement with no reasoning beside it cannot be
    -- argued with.
    because text not null
        check (length(btrim(because)) > 0),

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint action_group_key primary key (module, action_type)
);

comment on table pipeline.action_group is
    'Which of the five groups an action type belongs to: organic '
    'content, technical development, local, AEO or authority. Declared '
    'per action type rather than per work item, because the group is a '
    'property of the type and never varies between two items of it.';

insert into pipeline.action_group (module, action_type, group_label, because)
values
    -- Phase 1. The split is whether the fix changes what the page says
    -- or how the site serves it.
    ('phase_1_wqa', 'p1-optimize', 'organic_content',
     'Rewriting a page that already ranks: its titles, headings and copy.'),
    ('phase_1_wqa', 'p1-restore', 'organic_content',
     'Bringing back a page that used to earn traffic, which means '
     'writing it again.'),
    ('phase_1_wqa', 'p1-investigate', 'organic_content',
     'Phase 1 investigates pages whose earnings moved, so the question '
     'is about the content and what it ranks for. A judgement: an '
     'investigation that turns out to be a server problem is in the '
     'wrong group until somebody moves it.'),
    ('phase_1_wqa', 'p1-confirm-excluded', 'organic_content',
     'Confirms a page is right as it is. Not work, and it is grouped '
     'with the content it is a judgement about rather than left with no '
     'group at all.'),
    ('phase_1_wqa', 'p1-redirect-map', 'technical_development',
     'Server-level redirects. Nothing about the page changes.'),
    ('phase_1_wqa', 'p1-canonicalize', 'technical_development',
     'A tag telling Google which URL is the real one.'),
    ('phase_1_wqa', 'p1-remove', 'technical_development',
     'Taking pages down, which is a configuration change.'),
    ('phase_1_wqa', 'p1-remove-noindex', 'technical_development',
     'The same as p1-remove by another mechanism. Retired from Phase '
     '1''s current set, but live items still carry it.'),
    -- Phase 2. Same split.
    ('phase_2_tsa', 'p2-on-page-first-pass', 'organic_content',
     'Titles, meta descriptions, H1s and near-duplicate copy: what the '
     'page says.'),
    ('phase_2_tsa', 'p2-near-duplicates', 'organic_content',
     'Merging articles published twice, which is content work. Split out '
     'of p2-on-page-first-pass by the Phase 2 lane on 2026-10-19.'),
    ('phase_2_tsa', 'p2-content-phase-3', 'organic_content',
     'Content work handed to Phase 3 once it has the keyword set.'),
    ('phase_2_tsa', 'p2-indexation', 'technical_development',
     'Sitemaps, robots.txt and crawler access.'),
    ('phase_2_tsa', 'p2-architecture', 'technical_development',
     'Internal linking and depth, which is how the site is wired '
     'rather than what any page says.'),
    ('phase_2_tsa', 'p2-schema-coverage', 'technical_development',
     'Structured data where there was none.'),
    ('phase_2_tsa', 'p2-schema-errors', 'technical_development',
     'Markup already present but invalid.'),
    ('phase_2_tsa', 'p2-page-speed', 'technical_development',
     'Server response and Core Web Vitals.'),
    ('phase_2_tsa', 'p2-broken', 'technical_development',
     'Pages that do not answer. Declared in action_expectation with no '
     'live items yet.')
on conflict (module, action_type) do nothing;

-- NOTHING IS SEEDED FOR local, aeo OR authority, and nothing is seeded
-- for the three demo-snapshot-* rows. The first three audits have not
-- run for any site, so Paul's view correctly reads "No items" rather
-- than showing an empty group we invented a member for; the demo rows
-- are not real work and carry no group on purpose, so a group label
-- never makes a fabricated row look like a finding.

create trigger log_change after update or delete on pipeline.action_group
    for each row execute function pipeline.log_change();

-- ---------------------------------------------------------------------
-- Being chosen for the plan, on the item, with who chose it.
-- ---------------------------------------------------------------------
alter table pipeline.work_item
    add column if not exists prioritised_at timestamptz,
    add column if not exists prioritised_by text;

comment on column pipeline.work_item.prioritised_at is
    'When somebody chose this item for the plan. NULL means nobody has. '
    'This IS the flag: there is no separate boolean, so the two can '
    'never disagree.';

comment on column pipeline.work_item.prioritised_by is
    'Who chose it. Required whenever prioritised_at is set, because '
    '"who put this in the plan" is the first question asked of a plan.';

-- Both or neither. A date with nobody's name on it is the state this
-- column exists to prevent.
alter table pipeline.work_item
    drop constraint if exists work_item_prioritised_has_a_person;
alter table pipeline.work_item
    add constraint work_item_prioritised_has_a_person
    check ((prioritised_at is null) = (prioritised_by is null));

create index if not exists work_item_prioritised
    on pipeline.work_item (domain_id, prioritised_at)
    where prioritised_at is not null;

-- ---------------------------------------------------------------------
-- The dashboard's view gains both, appended.
--
-- CREATE OR REPLACE with the new columns at the END, so the existing
-- ACL survives. Recreating it would drop dashboard_ro's select and the
-- RICE column would go blank with nothing saying why.
-- ---------------------------------------------------------------------
create or replace view pipeline.v_work_item_rice as
select
    w.work_item_id,
    w.domain_id,
    w.project_id,
    w.module,
    w.slug,
    w.title,
    w.status,
    w.priority,
    w.url_count,
    w.estimate_hours,
    r.reach,
    r.reach_formula,
    r.reach_basis,
    r.impact,
    r.impact_formula,
    r.impact_basis,
    r.confidence,
    r.confidence_formula,
    r.confidence_basis,
    r.effort_hours,
    r.effort_formula,
    r.effort_basis,
    r.score,
    r.score_formula,
    r.rice_version,
    r.computed_at,
    (r.work_item_id is not null) as rice_computed,
    exists (
        select 1 from pipeline.work_item_rice_override o
        where o.work_item_id = w.work_item_id
          and o.superseded_at is null
    ) as rice_is_held,
    -- Appended by pipeline_055.
    g.group_label,
    (w.prioritised_at is not null) as prioritised,
    w.prioritised_at,
    w.prioritised_by
from pipeline.work_item w
left join pipeline.work_item_rice r using (work_item_id)
-- LEFT, so an action type nobody has grouped still appears. A missing
-- group is a row to add to action_group, not a reason for the item to
-- vanish out of the list.
left join pipeline.action_group g
    on g.module = w.module and g.action_type = w.slug;

comment on view pipeline.v_work_item_rice is
    'Every work item with its RICE score, the formula behind each '
    'input, whether a person has overridden any of them, which of the '
    'five groups it belongs to, and whether it has been chosen for the '
    'plan. Every item appears whether or not it has a score or a '
    'group, so the dashboard can show a dash with its reason instead '
    'of dropping the row.';

grant select, insert, update, delete
    on pipeline.action_group to adam, pipeline_bot;
grant select on pipeline.action_group to dashboard_ro;
-- Restated rather than assumed: a CREATE OR REPLACE keeps the view's
-- ACL, and saying so here costs nothing and survives somebody later
-- changing it to a drop.
grant select on pipeline.v_work_item_rice to dashboard_ro;
