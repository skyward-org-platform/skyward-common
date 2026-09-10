-- 20260909160000_pipeline_002_request.sql
-- Things a run wants changed about the PIPELINE, not about a client.
--
-- pipeline.open_question is about the client: something they or research
-- can answer. A run also finds things about our own tooling -- a schema
-- that cannot hold real data, a verb that does not exist, a command that
-- takes two minutes -- and those failed that test and had nowhere to go.
--
-- The first full BusBank run raised three inside a day: brand.value_input
-- could not store per-persona ranges (fixed, migration 003), there is no
-- way to reconcile two sources describing the same people, and
-- brand.lead_rule.kind has no value for "we serve it but do not pursue
-- it". All three were correct and useful, and all three survived only as
-- prose in a chat message.
--
-- Deliberately NOT domain-scoped. An inadequate schema is inadequate for
-- every client; filing it against whichever site exposed it puts it in
-- that client's outstanding list and means closing an identical row
-- everywhere it was raised. domain_id is here only to say where it was
-- noticed, and survives that site being removed.
create table pipeline.request (
    request_id  uuid primary key default gen_random_uuid(),

    module      text not null,

    -- Short, stable, caller-chosen: 'value-input-cannot-hold-ranges'.
    -- The key is (module, slug) rather than the title, so fixing a typo
    -- in the title does not create a second request.
    slug        text not null,

    kind        text not null
                    check (kind in ('bug', 'missing_capability', 'schema',
                                    'performance', 'docs', 'question')),

    -- What it cost. An operator triaging a list needs this more than
    -- the category.
    impact      text check (impact in ('blocks', 'slows',
                                       'degrades_output', 'cosmetic')),

    title       text not null,
    detail      text,

    -- The concrete case: the command, the error, the table, the count.
    -- A request without one is an opinion.
    evidence    text,

    -- What the raiser thinks should change. Optional: noticing a problem
    -- is worth reporting even with no idea what to do about it.
    proposal    text,

    -- Where it was noticed, if anywhere. Nullable, and SET NULL rather
    -- than cascade: removing the site does not make the request untrue.
    domain_id   bigint references meta.site(domain_id) on delete set null,

    status      text not null default 'open'
                    check (status in ('open', 'accepted', 'rejected',
                                      'done')),
    resolution  text,

    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

alter table pipeline.request
    add constraint request_natural_key unique (module, slug);

create index request_open_idx on pipeline.request (status)
    where status = 'open';
