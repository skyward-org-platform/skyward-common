-- local.gbp_push: one row per push attempt per listing.
--
-- What was in scope, the live snapshot it was diffed against, the diff,
-- the payload, the rollback payload, and the approve/push/rollback
-- audit. Profile fields and content keys alike:
-- local_content.pushed_at and gbp_resource_name point at the push that
-- sent them.
--
-- WHY IT LANDS NOW
-- The design marked this HELD: "do not apply until GBP API access
-- exists", drafted early only so nothing on site.gbp or local_content
-- could contradict it. That condition is met. Google approved the
-- Business Profile API for project data-hub-468216 on 2026-10-13 (300
-- requests/min, 10,000 location updates/day), and the first live
-- read-only calls ran under data@ on 2026-10-19 across seven verified
-- listings. An empty table costs nothing and the write adapter now has
-- a target to build against.
--
-- NOTHING HERE PUSHES ANYTHING. This records attempts; the approval
-- rule lives in the adapter, and the schema only refuses to call
-- something approved without saying who approved it.
--
-- Designed by Abdullah Sajjad (skyward-local-seo), 2026-10-09. Moved
-- from site to local in review, 2026-10-21, with the other two tables:
-- it is our own record of our own action, not the client's.

create table local.gbp_push (
    push_id          uuid primary key default gen_random_uuid(),
    domain_id        bigint not null
                         references meta.site(domain_id) on delete cascade,
    gbp_cid          text not null,
    push_no          integer not null check (push_no >= 1),
    scope            text[] not null,
    snapshot_before  jsonb not null,
    diff             jsonb not null,
    payload          jsonb not null,
    rollback_payload jsonb not null,
    status           text not null default 'planned'
                         check (status in ('planned', 'approved', 'pushed',
                                           'failed', 'rolled_back')),
    approved_by      text,
    approved_at      timestamptz,
    pushed_by        text,
    pushed_at        timestamptz,
    rolled_back_at   timestamptz,
    error            text,
    job_id           text,
    sources          text[] not null
                         constraint gbp_push_sources_not_empty
                         check (cardinality(sources) > 0),
    notes            text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),

    constraint gbp_push_natural_key unique (domain_id, gbp_cid, push_no),
    constraint gbp_push_listing
        foreign key (domain_id, gbp_cid)
        references site.gbp (domain_id, gbp_cid)
        on update cascade on delete cascade,
    constraint gbp_push_approved_says_who check (
        status not in ('approved', 'pushed') or approved_by is not null)
);

create trigger log_change after update or delete on local.gbp_push
    for each row execute function pipeline.log_change();

comment on table local.gbp_push is
    'One row per push attempt per listing: scope, the snapshot it was '
    'diffed against, the diff, the payload, the rollback payload and '
    'the approve/push/rollback audit. The record of what we sent '
    'Google, kept so a push can be undone from stored state rather '
    'than from memory.';

comment on column local.gbp_push.sources is
    'Tightened in review to the house rule (cardinality > 0): the draft '
    'defaulted to an empty array, which would have let a push be '
    'recorded with no account of where its payload came from.';

grant select, insert, update, delete on local.gbp_push
    to adam, pipeline_bot;
grant select on local.gbp_push to dashboard_ro;
