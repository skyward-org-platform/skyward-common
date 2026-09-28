-- 20260927210000_pipeline_013_work_item_outcomes.sql
--
-- Did the work an operator says they did actually land.
--
-- pipeline_012 gave a work item its page list and recorded what the
-- operator DECLARED they changed. `p1-wqa outcomes` then measures that
-- declaration against two audits of the site, page by page, and answers the
-- one question the maintenance lane exists for. It printed the answer to
-- stdout and threw it away, so the dashboard showed a claim with nothing
-- beside it, and getting the answer meant running a CLI and reading JSON
-- (Tester, 2026-09-26).
--
-- FOUR ANSWERS, AND THREE OF THEM ARE NOT "THEY DID NOT DO IT"
--
--   landed         the site shows what was expected
--   not_landed     the site shows the old state, measured properly
--   landed_wrong   they changed something and the page is now worse: the
--                  URL was to redirect and 404s instead, or the rewrite
--                  landed on a page that now returns 500. Reporting that
--                  as landed is how a 404 gets signed off.
--   cannot_tell    the audit cannot show either way -- the page left the
--                  universe, the crawl is older than the claim, the crawl
--                  is not trustworthy this run, or nothing in the audit
--                  measures that kind of change at all
--
-- The split matters more than the counts. "We could not read it" and "they
-- did not do it" are different sentences about different people, and the
-- recurring failure in this pipeline is the first being rendered as the
-- second.
--
-- WHY NOT change_finding, WHICH THE SPEC NAMED. The completion spec
-- (2026-09-25, "the outcome is computed per comparison and lives in
-- change_finding") does not survive contact with the table:
--
--   * A change_finding belongs to a change_report, which is keyed on the
--     SITE and a pair of versions. It carries no work item, so an outcome
--     stored there could not say which item was being verified.
--   * Its natural key is (report, subject_type, subject_key, pattern). One
--     URL can sit on two work items with OPPOSITE expectations -- redirected
--     away on one, receives_redirect on the other -- and those two verdicts
--     would collide into one row.
--   * `site-changes --write` DELETES and replaces every finding under its
--     report, by design. An outcome written there by a different verb would
--     be destroyed by the next site comparison.
--
-- So the outcomes get their own pair, and the spec is wrong on this point
-- rather than the implementation being lazy. Flagged to Adam 2026-09-27.
--
-- TWO TABLES, THE SAME SHAPE AS A CHANGE REPORT AND ITS FINDINGS. The facts
-- that explain a whole wall of verdicts at once -- the crawl is older than
-- the claim, the two reads were not comparable -- live on the header and are
-- said once. Repeating them on forty page rows buries them.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

-- ---------------------------------------------------------------------
-- pipeline.work_item_check: one work item, checked against one pair of
-- audits.
--
-- One row per (work item, version_before, version_after). Re-running the
-- SAME comparison overwrites it -- it is derived from two audits that do
-- not change, so a second row would be noise rather than history. A LATER
-- comparison is a new row on purpose: "did it land as of v7" and "as of v8"
-- are different questions, and the older answer stays readable.
-- ---------------------------------------------------------------------
create table pipeline.work_item_check (
    work_item_check_id uuid primary key default gen_random_uuid(),
    work_item_id    uuid not null
                        references pipeline.work_item(work_item_id)
                        on delete cascade,
    -- Denormalised for the same reason as work_item_url.domain_id: every
    -- read is scoped by site, and a join to get there would be a join on
    -- every row.
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,
    project_id      bigint
                        references meta.projects(project_id),

    -- The two audits compared, as Phase 1 numbers them.
    version_before  integer not null,
    version_after   integer not null,

    -- When the OPERATOR says the change landed, copied from the item so
    -- this row stands alone next to the dates below.
    completed_at    timestamptz,

    -- WHEN THE EVIDENCE WAS GATHERED, which is the CRAWL's date and never
    -- the aggregate's build time. An aggregate rebuilt from data already in
    -- the warehouse is new and its evidence is not, so reading the build
    -- time would let a ninety-second rebuild unlock credit for work claimed
    -- after the crawl (Tester, 2026-09-26).
    evidence_at     timestamptz,

    -- False when the two reads are not comparable at all. Every verdict
    -- under such a row is "we could not read", and a reader must be able to
    -- tell that from "they did not do it".
    comparable      boolean not null default true,

    -- The one fact that explains a wall of cannot_tell: no audit has been
    -- taken since the claim, so nothing measured here can show whether it
    -- landed.
    audit_predates_claim boolean not null default false,
    -- The same question for the copy, which has its OWN evidence dates: the
    -- library's stored page revisions. A rewrite must not be gated on a
    -- crawl that happens to be older than the revisions we hold. Null where
    -- there is no stored copy to date.
    copy_evidence_predates_claim boolean,

    -- Three sentences meant to be SHOWN, not parsed: why the verdicts are
    -- unanswerable, what the copy comparison could see on both sides, and
    -- how old the crawl is against when the aggregate was built.
    note            text,
    copy_note       text,
    evidence_note   text,

    job_id          text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),

    constraint work_item_check_natural_key
        unique (work_item_id, version_before, version_after)
);

create index work_item_check_by_site
    on pipeline.work_item_check (domain_id, version_after desc);

-- ---------------------------------------------------------------------
-- pipeline.work_item_outcome: what the check concluded about one URL.
--
-- Two families under one table, because both answer the same question --
-- what did this check conclude about this URL -- and a reader wants them in
-- one list:
--
--   declared  a change the operator says they made, MEASURED against the
--             site: landed / not_landed / landed_wrong / cannot_tell.
--   excluded  a deferral, RE-EXAMINED: does the reason that parked it still
--             hold. Not measured against the site at all, so its conclusion
--             is about its own premise.
--
-- `look` IS THE QUEUE, AND IT IS NOT THE SAME AS "FAILED". A destination
-- that received nothing is the source URLs that did not redirect to it,
-- restated -- one failure, not two. Queueing both showed an operator a
-- backlog twice its real size (Tester, 2026-09-26). The row is still
-- written and still answered, and it names what it restates, so a reader who
-- opens it is sent to the cause instead of to a second copy of it.
-- ---------------------------------------------------------------------
create table pipeline.work_item_outcome (
    work_item_outcome_id uuid primary key default gen_random_uuid(),
    work_item_check_id uuid not null
                        references pipeline.work_item_check(work_item_check_id)
                        on delete cascade,
    work_item_id    uuid not null
                        references pipeline.work_item(work_item_id)
                        on delete cascade,
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,

    url             text not null,

    kind            text not null
                        check (kind in ('declared', 'excluded')),
    outcome         text not null
                        check (outcome in ('landed', 'not_landed',
                                           'landed_wrong', 'cannot_tell',
                                           'exclusion_premise_failed',
                                           'exclusion_standing',
                                           'exclusion_resolved')),
    -- What was claimed for this page, from work_item_url. Null on an
    -- exclusion, which claims nothing.
    expected_change text,

    -- The sentence that justifies the verdict, in words meant to be shown.
    -- A verdict without one is an accusation.
    note            text,
    look            boolean not null default false,
    -- The URLs whose failure this one merely repeats.
    restates        jsonb not null default '[]'::jsonb,

    created_at      timestamptz not null default now(),

    constraint work_item_outcome_natural_key
        unique (work_item_check_id, url)
);

create index work_item_outcome_queue
    on pipeline.work_item_outcome (work_item_check_id, look, kind);
create index work_item_outcome_by_url
    on pipeline.work_item_outcome (domain_id, url);

comment on table pipeline.work_item_check is
    'One work item checked against one pair of audits: what the evidence '
    'was, how old it was, and whether the two reads were comparable at all.';
comment on table pipeline.work_item_outcome is
    'What that check concluded about one URL. `look` is the operator queue '
    'and excludes failures that only restate another URL''s failure.';

-- Updates and deletes only, and in practice this table sees neither by
-- hand: a re-run replaces its rows wholesale. The trigger is here so that
-- an operator overriding a verdict later is recoverable, on the same terms
-- as every other table in this schema.
create trigger log_change after update or delete on pipeline.work_item_outcome
    for each row execute function pipeline.log_change();
