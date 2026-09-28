-- 20260922180000_brand_009_lead_rule_serve_not_pursue.sql
-- A segment the client SERVES but will not CHASE.
--
-- Decided by Adam 2026-09-22, closing the pipeline request
-- lead-rule-kind-has-no-serve-but-do-not-pursue.
--
-- The enum was qualifies | disqualifies | not_offered. A segment the client
-- takes the work from but does not want marketed to had no honest value:
-- 'disqualifies' overstates it, because the lead is still served, and
-- 'not_offered' is simply false. Recording it as either teaches a later
-- phase the wrong thing -- Phase 3 would either chase the segment or refuse
-- a lead the client would have taken.
--
-- The shape recurs on any client with work they accept but will not spend
-- on, which is why it is a value rather than a note.

alter table brand.lead_rule
    drop constraint if exists lead_rule_kind_check;
alter table brand.lead_rule
    add constraint lead_rule_kind_check
    check (kind in ('qualifies', 'disqualifies', 'not_offered',
                    'serve_not_pursue'));

comment on column brand.lead_rule.kind is
    'qualifies: pursue it. disqualifies: not a lead at all. not_offered: we '
    'do not sell it. serve_not_pursue: the client serves this segment but '
    'does not want it marketed to, so the row is honest about both halves.';
