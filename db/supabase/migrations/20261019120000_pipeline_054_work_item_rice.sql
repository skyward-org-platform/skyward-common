-- pipeline_054: RICE on every work item, with the formula that produced
-- it, and a person's override beside it.
--
-- WHAT WAS PROMISED, and why it needs storage at all. Paul and Drew were
-- told (Adam, Slack, 2026-10-17) that the pipeline generates all four
-- inputs on every work item rather than waiting on a person, that every
-- score records the formula that produced it so a RICE number is never
-- an unexplained figure, and that a person can override any of the four
-- with a note. The dashboard shows RICE as a column on every item and a
-- breakdown panel behind it, and shows a dash until these rows exist.
--
-- Computation alone could not deliver any of that. A formula string has
-- to be stored to be read, an override has to outlive the run that did
-- not know about it, and a score has to stay comparable to the one
-- beside it, which means recording which set of declarations produced
-- it.
--
-- ONE ROW PER WORK ITEM, NOT PER RUN, which is work_item's own rule: an
-- item is the current state of a piece of work, not a snapshot of one
-- run, so a re-score updates the same row. What a score was last week
-- is recoverable from pipeline.change_log, which the trigger below
-- feeds, rather than from version rows nobody would read.
--
-- WHY THE FORMULA IS TEXT AND NOT JSON. It is read by a person in a
-- panel, one line per input. Nothing queries inside it, and a jsonb
-- shape would invite somebody to start computing from it instead of
-- from the tables the formula was derived from.
--
-- EVERY VALUE IS NULLABLE ON PURPOSE. 16 of the 50 live items promise
-- nothing measurable yet (5 p1-investigate, 4 p1-confirm-excluded, 4
-- p1-remove-noindex with no declaration at all, 3 p2-content-phase-3
-- waiting on Phase 3), and no site but InSoFast has the P3 mapping that
-- the reach half needs. A zero would rank those against real work
-- looking deliberate. NULL is the dash Paul asked for, and the formula
-- column says which input was missing.
--
-- Grants: written explicitly, matching pipeline_052. Default privileges
-- on this schema cover adam and pipeline_bot; dashboard_ro needs select
-- or the column renders empty with nothing saying why.

-- ---------------------------------------------------------------------
-- pipeline.work_item_rice: the score, and how it was arrived at.
-- ---------------------------------------------------------------------
create table if not exists pipeline.work_item_rice (
    work_item_id uuid primary key
        references pipeline.work_item(work_item_id) on delete cascade,
    -- Carried so a read can scope by site without joining the item,
    -- as work_item_expectation does.
    domain_id bigint not null,

    -- Pages the action touches, plus the search volume of the clusters
    -- they map to once P3 has mapped them.
    reach numeric check (reach is null or reach >= 0),
    reach_formula text not null,

    -- Derived from the action's own promise: the metric, the direction,
    -- and the size where it states one. Reported on RICE's published
    -- ladder (3, 2, 1, 0.5, 0.25) so it reads beside any other score.
    impact numeric check (impact is null or impact >= 0),
    impact_formula text not null,

    -- Derived from the evidence: how specific the promise is, how many
    -- of the pages have a reading, and P3's own mapping confidence.
    confidence numeric
        check (confidence is null or (confidence > 0 and confidence <= 1)),
    confidence_formula text not null,

    -- Hours, from the action type and the page count, anchored on an
    -- estimate a person actually made on a ClickUp task.
    effort_hours numeric
        check (effort_hours is null or effort_hours > 0),
    effort_formula text not null,

    -- WHERE EACH NUMBER CAME FROM, which is the difference between a
    -- measurement and a judgement and must never be guessable from the
    -- value alone. 'measured' is a reading rather than a stand-in, as
    -- reach is once P3 gives the search volume behind the pages; 'by
    -- analogy' borrowed another action type's measured estimate,
    -- because no Phase 1 action has ever been estimated by a person;
    -- 'clickup' is the figure somebody typed on the task, read rather
    -- than derived; 'override' is a person's own number set in the
    -- dashboard, which beats all of them; and 'unknown' means the input
    -- could not be read at all, which is why its value is NULL.
    reach_basis text not null
        check (reach_basis in ('derived', 'measured', 'by analogy',
                               'clickup', 'override', 'unknown')),
    impact_basis text not null
        check (impact_basis in ('derived', 'measured', 'by analogy',
                                'clickup', 'override', 'unknown')),
    confidence_basis text not null
        check (confidence_basis in ('derived', 'measured', 'by analogy',
                                    'clickup', 'override', 'unknown')),
    effort_basis text not null
        check (effort_basis in ('derived', 'measured', 'by analogy',
                                'clickup', 'override', 'unknown')),

    score numeric check (score is null or score >= 0),
    score_formula text not null,

    -- WHICH SET OF DECLARATIONS PRODUCED THIS. The metric weights, the
    -- direction factors and the effort anchors are judgements in
    -- infra/rice.py, and editing one changes every score computed
    -- after it. Two scores from different sets are not comparable, and
    -- without this column nothing would say so.
    rice_version text not null,

    computed_at timestamptz not null default now(),
    job_id text,

    -- A SCORE CANNOT EXIST WITHOUT ITS FOUR INPUTS. Postgres cannot
    -- check the arithmetic, but it can refuse the shape that would make
    -- a score unexplainable.
    constraint work_item_rice_score_has_every_input
        check (score is null
               or (reach is not null and impact is not null
                   and confidence is not null and effort_hours is not null))
);

comment on table pipeline.work_item_rice is
    'RICE on one work item: (Reach x Impact x Confidence) / Effort, '
    'generated by the pipeline from what the action already states, '
    'with the formula behind each input stored beside it. NULL is a '
    'dash and means an input the formula needs is missing; it is never '
    'a zero. One row per item, updated in place on a re-score, with '
    'history in pipeline.change_log.';

comment on column pipeline.work_item_rice.rice_version is
    'The declaration set that produced these numbers. Scores from two '
    'different sets are not comparable.';

create index if not exists work_item_rice_by_domain
    on pipeline.work_item_rice (domain_id, score desc nulls last);

-- ---------------------------------------------------------------------
-- pipeline.work_item_rice_override: a person beats the formula.
--
-- A SEPARATE TABLE, not four more columns on the row above, for two
-- reasons. An override has to survive a re-score that knows nothing
-- about it, and the row above is rewritten wholesale every run. And an
-- override is somebody's decision, so it keeps who made it and when,
-- which a value column cannot.
--
-- THE NOTE IS NOT OPTIONAL. A number typed with no reason beside it is
-- indistinguishable from one the pipeline derived, and the whole point
-- of storing formulas is that no figure in this system is unexplained.
--
-- SUPERSEDED RATHER THAN DELETED, with a partial unique index so only
-- one override per input is live. Somebody changing their mind is a
-- thing a reader needs to see, and an override that vanished would look
-- like it had never been made.
-- ---------------------------------------------------------------------
create table if not exists pipeline.work_item_rice_override (
    work_item_rice_override_id uuid primary key default gen_random_uuid(),

    work_item_id uuid not null
        references pipeline.work_item(work_item_id) on delete cascade,
    domain_id bigint not null,

    input text not null
        check (input in ('reach', 'impact', 'confidence', 'effort')),
    value numeric not null check (value >= 0),

    note text not null
        constraint work_item_rice_override_note_says_something
        check (length(btrim(note)) > 0),

    -- A person, or the process that read a person's figure. The seven
    -- effort overrides that exist today came off InSoFast's ClickUp
    -- board, where somebody typed them by hand.
    set_by text not null,
    set_at timestamptz not null default now(),
    superseded_at timestamptz,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

comment on table pipeline.work_item_rice_override is
    'One person''s value for one RICE input on one work item, with the '
    'reason they gave. It beats the pipeline''s derivation and survives '
    'every re-score. Superseded rather than deleted, so a change of '
    'mind stays visible.';

create unique index if not exists work_item_rice_override_one_live
    on pipeline.work_item_rice_override (work_item_id, input)
    where superseded_at is null;

create index if not exists work_item_rice_override_by_domain
    on pipeline.work_item_rice_override (domain_id, input);

-- Updates and deletes only, as on pipeline.work_item: recovery is about
-- overwrites, and a first write is reproducible from its run.
create trigger log_change after update or delete on pipeline.work_item_rice
    for each row execute function pipeline.log_change();

create trigger log_change
    after update or delete on pipeline.work_item_rice_override
    for each row execute function pipeline.log_change();

-- ---------------------------------------------------------------------
-- pipeline.v_work_item_rice: what the dashboard reads.
--
-- One row per work item -- every item, scored or not -- so the column
-- can render a dash with its reason rather than the item disappearing
-- from the list. `rice_is_held` is the flag the panel needs: it says a
-- person has had a hand in this score, which is not visible from the
-- numbers.
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
    -- No score and no row at all are different states, and a reader
    -- cannot tell them apart from a NULL score alone.
    (r.work_item_id is not null) as rice_computed,
    exists (
        select 1 from pipeline.work_item_rice_override o
        where o.work_item_id = w.work_item_id
          and o.superseded_at is null
    ) as rice_is_held
from pipeline.work_item w
left join pipeline.work_item_rice r using (work_item_id);

comment on view pipeline.v_work_item_rice is
    'Every work item with its RICE score, the formula behind each '
    'input, and whether a person has overridden any of them. Every '
    'item appears whether or not it has a score, so the dashboard can '
    'show a dash with its reason instead of dropping the row.';

grant select, insert, update, delete
    on pipeline.work_item_rice to adam, pipeline_bot;
grant select, insert, update, delete
    on pipeline.work_item_rice_override to adam, pipeline_bot;
grant select on pipeline.work_item_rice to dashboard_ro;
grant select on pipeline.work_item_rice_override to dashboard_ro;
grant select on pipeline.v_work_item_rice to dashboard_ro;
