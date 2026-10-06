-- 20261001120300_tsa_003_target_held_rule.sql
--
-- tsa.target.exclusion_rule gains 'held': a page Phase 1 holds (for example
-- the Canada hold) is left out of the Phase 2 audit and says so on its row.
-- The hold itself lives on pipeline.work_item_url (state 'excluded'), not on
-- wqa.page_verdict. Phase 2 only writes 'held' once Phase 1 names the hold
-- reason_kind; until then the new value is simply permitted.
--
-- The inline check from tsa_001 was auto-named target_exclusion_rule_check.
-- Drop and re-add it with the extra value.

alter table tsa.target drop constraint if exists target_exclusion_rule_check;
alter table tsa.target add constraint target_exclusion_rule_check
    check (exclusion_rule is null or exclusion_rule in
           ('doubled-slash','clean-twin','non-200','off-host','malformed',
            'held'));
