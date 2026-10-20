-- pipeline_039: a declaration can name WHICH SIDE of the change it is about.
--
-- A redirect has two sides with opposite expectations on the same metric:
-- the subject URL is meant to LOSE impressions and the destination to GAIN
-- clicks. 037's key is (module, action_type, expected_metric), so only one
-- of those two rows fits and Phase 1 cannot declare its redirect at all
-- (P1-P2, 2026-10-14).
--
-- THE SIDE IS NOT A NEW CONCEPT and does not need a new word. Every page
-- of an action already carries work_item_url.expected_change, which is
-- exactly this: redirected_away for the subject, receives_redirect for
-- the destination, and thirteen more across Phases 1 and 2. So the
-- declaration keys on it.
--
-- NULL MEANS EVERY PAGE OF THE ACTION, which is what every single-sided
-- action type wants and what the eight rows Phase 1 has ready would use.
-- A value means only pages whose own expected_change matches, and that is
-- what the copy onto work_item_url should look for first.
--
-- The alternative was splitting the action type in two, and it was
-- rejected for a reason worth recording: a redirect is ONE piece of work,
-- and splitting it to satisfy a key would make every other query about
-- redirects reassemble it.
--
-- Postgres treats NULLs as distinct in a unique constraint, so the key is
-- declared NULLS NOT DISTINCT: without it, one action type could carry
-- two contradictory "every page" declarations of the same metric and the
-- database would allow both.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

alter table pipeline.action_expectation
    add column if not exists expected_change text;

comment on column pipeline.action_expectation.expected_change is
    'Which side of the change this declaration is about, in the same '
    'vocabulary as work_item_url.expected_change. NULL means every page '
    'of the action. A redirect declares impressions declining for '
    'redirected_away and clicks improving for receives_redirect.';

alter table pipeline.action_expectation
    drop constraint if exists action_expectation_natural_key;

alter table pipeline.action_expectation
    add constraint action_expectation_natural_key
        unique nulls not distinct
        (module, action_type, expected_change, expected_metric);
