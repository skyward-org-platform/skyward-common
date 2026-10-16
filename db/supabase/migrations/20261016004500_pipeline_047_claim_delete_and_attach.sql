-- pipeline_047: a referenced claim refuses deletion, and a declaration
-- can carry the claim so it is copied at creation.
--
-- TWO DEFECTS IN pipeline_042, BOTH MINE, found by P1-P2's Tester and
-- reproduced before being accepted.
--
-- 1. DELETING A CLAIM WAS IMPOSSIBLE AND SAID SO UNREADABLY. The FK was
-- ON DELETE SET NULL and the CHECK is both-or-neither, so the cascade
-- nulled claim_id, could not touch claim_kind, and built the one row the
-- CHECK forbids. Measured:
--
--     deleting the claim: CheckViolation
--     new row for relation "work_item_expectation" violates check
--     constraint "work_item_expectation_claim_kind"
--
-- It failed rather than corrupted, so nothing was ever at risk, but the
-- error named a check constraint on a different table from the one
-- somebody tried to delete from.
--
-- RESTRICT IS THE FIX RATHER THAN LOOSENING THE CHECK, and the reason is
-- not merely that the error reads better. A claim with evidence attached
-- SHOULD NOT be deletable: the link between a piece of work and the
-- hypothesis it tested is the entire purpose of the table, and
-- `status = 'withdrawn'` already exists for retiring a claim without
-- destroying its evidence. Detaching first is then an explicit act -- set
-- both columns null -- rather than something a delete does quietly.
--
-- 2. THERE WAS NO WAY TO ATTACH A CLAIM. pipeline.claim held no rows,
-- nothing referenced claim_id outside a TableSpec, and the protection
-- P1-P2 put on those columns had never run on the normal path because
-- no normal path existed.
--
-- THE ATTACH PATH IS THE DECLARATION, not a bespoke writer. Paul asked
-- for the claim to be "filled in when the item is created, next to the
-- expected effect", and the expectation is already copied from
-- action_expectation onto the item at creation. So the claim rides along
-- with it: the phase declares that this KIND of action tests this claim,
-- and every item created from it carries the claim without anybody
-- attaching anything. No new writer, and Phase 1 and Phase 2's existing
-- copy code picks it up by adding two columns to what it copies.
--
-- An operator attaching a claim to one item by hand is a later, narrower
-- thing, and it belongs on the dashboard rather than here.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

-- 1 ----------------------------------------------------------------------

alter table pipeline.work_item_expectation
    drop constraint if exists work_item_expectation_claim_id_fkey;

alter table pipeline.work_item_expectation
    add constraint work_item_expectation_claim_id_fkey
    foreign key (claim_id) references pipeline.claim(claim_id)
    on delete restrict;

-- 2 ----------------------------------------------------------------------

alter table pipeline.action_expectation
    add column if not exists claim_id uuid
        references pipeline.claim(claim_id) on delete restrict;

alter table pipeline.action_expectation
    add column if not exists claim_kind text;

alter table pipeline.action_expectation
    drop constraint if exists action_expectation_claim_kind;

-- `claim_kind is not null` written out, for the same reason as in 042: a
-- CHECK passes when it evaluates to NULL, and `null in (...)` is NULL
-- rather than false, so the obvious form of this accepts a claim with no
-- kind -- the one state it exists to forbid.
alter table pipeline.action_expectation
    add constraint action_expectation_claim_kind check (
        (claim_id is null and claim_kind is null)
        or (claim_id is not null
            and claim_kind is not null
            and claim_kind in ('tests', 'bears_on')));

comment on column pipeline.action_expectation.claim_id is
    'The industry hypothesis this KIND of action bears on, if any. '
    'Copied onto each item created from this declaration, which is how a '
    'claim gets attached without anybody attaching it: Paul asked for '
    'the claim to be filled in when the item is created, next to the '
    'expected effect, and this is the declaration the effect comes from.';

comment on column pipeline.action_expectation.claim_kind is
    'tests: the action exists in order to test the claim, which is the '
    'strongest evidence a claim can get. bears_on: the work was being '
    'done anyway and happens to speak to it. Counting the second as the '
    'first is how incidental work comes to look like an experiment.';
