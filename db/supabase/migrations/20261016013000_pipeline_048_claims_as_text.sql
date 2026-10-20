-- pipeline_048: a claim is a string, and a promise can carry several.
--
-- Adam, 2026-10-16: "the claims col should just be a string. so you can
-- input as many claims as needed."
--
-- pipeline_042 and 047 built it as a foreign key into a `pipeline.claim`
-- bank, one claim per promise, with a `claim_kind` saying whether the
-- action tests the claim or merely bears on it. That is more machinery
-- than asked for, and it forces a decision -- which single claim is this
-- promise about -- that the operator should not have to make.
--
-- So: one free-text column on each table, holding as many claims as
-- somebody wants to write, and nothing to look up or maintain first.
--
-- `pipeline.claim` IS DROPPED. It held no rows and, once the link is a
-- string, nothing references it. Leaving an empty table behind would
-- leave a decoy: the next reader would have to work out which of the two
-- is authoritative, and the answer would be the string. If a canonical
-- list of claims is ever wanted -- Paul's "TC-018" references suggest he
-- keeps one somewhere -- it comes back as its own migration, and the
-- strings already written are the evidence of which claims were used.
--
-- WHAT IS LOST, STATED RATHER THAN BURIED. Paul drew a distinction on
-- the call between an action done IN ORDER TO test a claim and one that
-- was happening anyway and merely speaks to it, and said the difference
-- matters because a pile of incidental work must not be counted as a
-- designed experiment. With one string there is no field for that, so it
-- can only live inside the text by convention. Adam's call; recorded here
-- so nobody concludes later that the distinction was never raised.
--
-- Nothing is migrated across: both columns are empty on every row, no
-- claim was ever attached, and the bank never held a claim to attach.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

alter table pipeline.work_item_expectation
    drop constraint if exists work_item_expectation_claim_kind;
alter table pipeline.work_item_expectation
    drop column if exists claim_kind;
alter table pipeline.work_item_expectation
    drop column if exists claim_id;
alter table pipeline.work_item_expectation
    add column if not exists claims text;

alter table pipeline.action_expectation
    drop constraint if exists action_expectation_claim_kind;
alter table pipeline.action_expectation
    drop column if exists claim_kind;
alter table pipeline.action_expectation
    drop column if exists claim_id;
alter table pipeline.action_expectation
    add column if not exists claims text;

drop table if exists pipeline.claim;

comment on column pipeline.action_expectation.claims is
    'The industry claims this KIND of action bears on, as free text, as '
    'many as apply. Copied onto each item created from this declaration, '
    'which is how a claim reaches an item without anybody attaching it. '
    'Paul''s own references look like "TC-018 llms.txt"; writing them the '
    'same way each time is what makes results countable across clients, '
    'and nothing here enforces that.';

comment on column pipeline.work_item_expectation.claims is
    'The claims this promise is evidence for, as free text, as many as '
    'apply. Copied from the declaration at creation and filled only when '
    'empty, so a claim written by hand is never overwritten by a later '
    'run and a declaration''s claim still reaches promises that already '
    'exist.';
