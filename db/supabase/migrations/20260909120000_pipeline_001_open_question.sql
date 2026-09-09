-- 20260909120000_pipeline_001_open_question.sql
-- The pipeline namespace: questions a run could not answer for itself.
--
-- Deliberately NOT a Phase 0 table. What differs between modules is
-- their FINDINGS -- WQA emits an action per URL, KAGG emits clusters --
-- and those stay in each module's own tables. What does NOT differ is
-- "somebody has to answer this", which has the same shape whoever
-- raised it. So one table with a module column, rather than a table per
-- phase and a union view over them later.
--
-- A gap and a contradiction are the same object: an open question about
-- a client that a human has to close. Only the question text differs.
-- Storing them apart would mean two mechanisms, two reports and two
-- ways to mark something answered.
create schema if not exists pipeline;

create table pipeline.open_question (
    question_id     uuid primary key default gen_random_uuid(),
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,

    -- Who raised it. Phase 1 will write rows here too.
    module          text not null,

    kind            text not null
                        check (kind in
                            ('gap','contradiction','question','other')),

    -- What it is about. Nullable together: a question about the
    -- engagement as a whole belongs to no column.
    subject_table   text,
    subject_column  text,
    subject_key     text,

    -- One or two sentences, answerable by someone non-technical. This
    -- is the text a future cross-module view shows as the ask.
    question        text not null,
    detail          text,

    -- Contradictions only: what each source actually claimed. A list of
    -- source NAMES cannot say that, which is why this exists beside
    -- sources rather than instead of it.
    findings        jsonb,

    best_guess      text,
    confidence      text check (confidence in ('high','medium','low')),

    -- research: a later step can go and find it.
    -- skyward:  one of us has to do something.
    -- client:   only they can answer, so it reaches the client report.
    -- This is the column that keeps that report short enough to send.
    answerable_by   text not null
                        check (answerable_by in
                            ('research','skyward','client')),

    status          text not null default 'open'
                        check (status in ('open','answered','dismissed')),
    resolution      text,
    answered_by     text,
    answered_at     timestamptz,

    -- Plural because a contradiction has two or more by definition. A
    -- gap usually carries one.
    sources         text[] not null default '{}',
    notes           text,

    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- Re-running a populate step must update the question it raised last
-- time, not add a twin -- the 3-4 loop runs this repeatedly.
--
-- NULLS NOT DISTINCT because the three subject columns are nullable and
-- routinely null together. Without it, every question about no
-- particular column reads as a different question and the table grows a
-- duplicate on every pass. That is the exact trap meta.data_access hit.
alter table pipeline.open_question
    add constraint open_question_natural_key
        unique nulls not distinct
            (domain_id, module, kind, subject_table, subject_column,
             subject_key);

create index open_question_domain_idx
    on pipeline.open_question (domain_id);

-- The two reads that will actually happen: everything still open for a
-- site, and everything the client owes us.
create index open_question_open_idx
    on pipeline.open_question (domain_id, status)
    where status = 'open';
