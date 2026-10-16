-- pipeline_042: the bank of claims, and the link from work to claim.
--
-- Paul, via the Skyward Council, 2026-10-15: "add a claim field to each
-- work item (for example "TC-018 llms.txt"), filled in when the item is
-- created, next to the expected effect. That lets us count results across
-- clients as we go. Matching claims to actions six months later is much
-- weaker, because by then you can't separate the change from everything
-- else that moved."
--
-- WHAT A CLAIM IS. Not a per-client thing: it is an industry hypothesis
-- about what moves the dial, held at the agency level, so evidence
-- accumulates across every client we do the work for. Paul's own example
-- on the call was llms.txt -- "does the creation of that have any impact,
-- does it not" -- and the answer only becomes visible once dozens of
-- actions across several sites have been attached to it and judged.
--
-- TWO KINDS OF LINK, AND THEY ARE NOT THE SAME EVIDENCE. Paul drew this
-- distinction himself: "are we doing this action because SEO says so, or
-- are you already doing this action and here's how SEO is going to help
-- with it. There's a difference in the nature of that work."
--
--   tests      the action exists IN ORDER TO test the claim. A designed
--              test, and the strongest evidence a claim can get.
--   bears_on   we were doing the work anyway and it happens to speak to
--              the claim. Real evidence, weaker, and it must never be
--              counted as though somebody had designed it.
--
-- Collapsing those two into one boolean would let a pile of incidental
-- work look like a deliberate experiment, which is exactly the kind of
-- false confidence this table exists to prevent.
--
-- WHY THE LINK SITS ON work_item_expectation RATHER THAN ON work_item.
-- Paul said "next to the expected effect", and that is where the expected
-- effect now lives (pipeline_041). It also means an action promising
-- three things can attach a claim to the ONE promise the claim is about:
-- a Restore that tests a claim about indexation attaches it to the
-- status_code promise and not to the clicks promise. A claim attached to
-- the whole item would silently claim all three as evidence.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

create table if not exists pipeline.claim (
    claim_id uuid primary key default gen_random_uuid(),

    -- The human reference Paul uses, e.g. 'TC-018'. Unique, because the
    -- whole point is that everyone quotes the same one.
    ref text not null,
    statement text not null,

    -- What the wider industry believes, recorded SEPARATELY from what we
    -- find. "It is consensus that llms.txt does nothing" and "our work
    -- shows llms.txt does nothing" are different sentences, and a claim
    -- that quietly adopted the consensus as its own finding would be
    -- worthless as evidence.
    consensus text
        check (consensus is null
               or consensus in ('believed', 'disputed', 'rejected',
                                'unknown')),

    status text not null default 'open'
        check (status in ('open', 'supported', 'contradicted',
                          'inconclusive', 'withdrawn')),

    -- Set only once somebody has read the evidence. A claim does not
    -- settle itself and nothing in the pipeline writes this.
    settled_at timestamptz,
    settled_by text,
    settled_note text,

    created_at timestamptz not null default now(),
    created_by text,

    constraint claim_ref_is_unique unique (ref),
    constraint claim_settled_says_who
        check (settled_at is null
               or (settled_by is not null
                   and length(btrim(settled_by)) > 0)),
    -- A SETTLED STATUS NEEDS A HUMAN BEHIND IT. Nothing computes this.
    constraint claim_settled_status_has_a_date
        check (status in ('open', 'withdrawn') or settled_at is not null)
);

comment on table pipeline.claim is
    'The bank of industry hypotheses about what moves the dial, held at '
    'the agency level rather than per client, so evidence accumulates '
    'across every site we do the work for. A claim is attached to a '
    'promise an action made (pipeline.work_item_expectation), and the '
    'verdicts on that promise are its evidence.';

alter table pipeline.work_item_expectation
    add column if not exists claim_id uuid
        references pipeline.claim(claim_id) on delete set null;

alter table pipeline.work_item_expectation
    add column if not exists claim_kind text;

alter table pipeline.work_item_expectation
    drop constraint if exists work_item_expectation_claim_kind;

-- `claim_kind is not null` IS WRITTEN OUT, and it has to be. A CHECK
-- passes when it evaluates to NULL, and `null in ('tests', 'bears_on')`
-- is NULL rather than false -- so the obvious form of this constraint
-- accepted a claim with no kind, which is the one state it exists to
-- forbid. Caught by probing it rather than by reading it.
alter table pipeline.work_item_expectation
    add constraint work_item_expectation_claim_kind check (
        (claim_id is null and claim_kind is null)
        or (claim_id is not null
            and claim_kind is not null
            and claim_kind in ('tests', 'bears_on')));

comment on column pipeline.work_item_expectation.claim_kind is
    'tests: this action exists in order to test the claim, which is a '
    'designed test and the strongest evidence a claim can get. '
    'bears_on: we were doing the work anyway and it happens to speak to '
    'the claim. Both are evidence; counting the second as the first is '
    'how a pile of incidental work comes to look like an experiment.';

create index if not exists work_item_expectation_by_claim
    on pipeline.work_item_expectation (claim_id)
    where claim_id is not null;
